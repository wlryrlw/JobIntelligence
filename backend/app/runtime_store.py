"""本地 Runtime Store：任务快照 JSON + append-only SSE JSONL。"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from threading import RLock

from .contracts import SseEvent, TaskSnapshot


class RuntimeStore:
    """一个轻量、可跨请求和进程重启读取的本地任务存储。

    每个任务使用两个文件：

    - ``tasks/<task_id>.json``：当前快照，采用同目录临时文件原子替换；
    - ``events/<task_id>.jsonl``：事件只追加不覆盖，支持断线后按 sequence 重放。

    该类不承诺多进程分布式锁；阶段 0 的服务是本地单进程应用，进程内锁
    用于避免并发请求交错写入。若未来改成多 worker，应替换为外部存储。
    """

    def __init__(self, root: Path) -> None:
        self.root = root
        self.snapshot_dir = root / "tasks"
        self.event_dir = root / "events"
        self._lock = RLock()

    def _ensure_dirs(self) -> None:
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.event_dir.mkdir(parents=True, exist_ok=True)

    def snapshot_path(self, task_id: str) -> Path:
        return self.snapshot_dir / f"{self._safe_id(task_id)}.json"

    def event_path(self, task_id: str) -> Path:
        return self.event_dir / f"{self._safe_id(task_id)}.jsonl"

    def save_snapshot(self, snapshot: TaskSnapshot) -> TaskSnapshot:
        path = self.snapshot_path(snapshot.task_id)
        encoded = json.dumps(
            snapshot.model_dump(mode="json"),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        with self._lock:
            self._ensure_dirs()
            fd, temporary_name = tempfile.mkstemp(
                prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
            )
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(encoded)
                    stream.write("\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary_name, path)
            except BaseException:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
                raise
        return snapshot

    def get_snapshot(self, task_id: str) -> TaskSnapshot | None:
        path = self.snapshot_path(task_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        try:
            return TaskSnapshot.model_validate(json.loads(raw))
        except (json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(f"任务快照损坏：{path}") from exc

    def append_event(self, event: SseEvent) -> SseEvent:
        path = self.event_path(event.task_id)
        line = json.dumps(
            event.model_dump(by_alias=True, mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock:
            self._ensure_dirs()
            existing = self.list_events(event.task_id)
            for previous in existing:
                if previous.event_id == event.event_id:
                    if previous != event:
                        raise ValueError(f"重复 event_id 的内容不一致：{event.event_id}")
                    return previous
            expected = existing[-1].sequence + 1 if existing else 1
            if event.sequence != expected:
                raise ValueError(
                    f"事件 sequence 不连续：期望 {expected}，收到 {event.sequence}"
                )
            with path.open("a", encoding="utf-8") as stream:
                stream.write(line)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
        return event

    def list_events(self, task_id: str, after_sequence: int = 0) -> list[SseEvent]:
        if after_sequence < 0:
            raise ValueError("after_sequence 不能小于 0")
        path = self.event_path(task_id)
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        events: list[SseEvent] = []
        for line_number, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                event = SseEvent.model_validate_json(line)
            except ValueError as exc:
                raise RuntimeError(
                    f"事件日志损坏：{path} 第 {line_number} 行"
                ) from exc
            if events and event.sequence != events[-1].sequence + 1:
                raise RuntimeError(f"事件日志 sequence 不连续：{path}")
            if event.sequence > after_sequence:
                events.append(event)
        return events

    def all_events(self, task_id: str) -> list[SseEvent]:
        """读取全部事件；``list_events`` 的过滤逻辑不会隐藏前置事件。"""

        return self.list_events(task_id, after_sequence=0)

    def sequence_after_event_id(self, task_id: str, event_id: str) -> int | None:
        for event in self.all_events(task_id):
            if event.event_id == event_id:
                return event.sequence
        return None

    @staticmethod
    def _safe_id(task_id: str) -> str:
        if not task_id or any(char not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for char in task_id):
            raise ValueError("非法 task_id")
        return task_id
