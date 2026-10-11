from __future__ import annotations

import threading
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterator

from .contracts import SseEvent


ProgressSink = Callable[[str, str, str, dict[str, Any]], None]


class QueryTimeoutError(RuntimeError):
    """统一的问答超时错误，保留发生阶段和耗时信息。"""

    def __init__(
        self,
        stage: str,
        message: str,
        *,
        elapsed_ms: int,
        timeout_seconds: float,
    ) -> None:
        super().__init__(message)
        self.stage = stage
        self.message = message
        self.elapsed_ms = elapsed_ms
        self.timeout_seconds = timeout_seconds


@dataclass
class QueryRuntime:
    task_id: str
    timeout_seconds: float
    progress_sink: ProgressSink | None = None
    started_at: float = field(default_factory=time.monotonic)
    current_stage: str = "queued"

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.monotonic() - self.started_at) * 1000))

    @property
    def remaining_seconds(self) -> float:
        return self.timeout_seconds - (time.monotonic() - self.started_at)

    def emit(
        self,
        stage: str,
        message: str,
        *,
        status: str = "running",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.current_stage = stage
        if self.progress_sink is not None:
            self.progress_sink(
                stage,
                message,
                status,
                {
                    "elapsed_ms": self.elapsed_ms,
                    **(metadata or {}),
                },
            )

    def check(self, stage: str | None = None) -> None:
        if self.remaining_seconds > 0:
            return
        active_stage = stage or self.current_stage
        raise QueryTimeoutError(
            active_stage,
            f"问答超过 {self.timeout_seconds:.0f} 秒时间预算",
            elapsed_ms=self.elapsed_ms,
            timeout_seconds=self.timeout_seconds,
        )


_runtime: ContextVar[QueryRuntime | None] = ContextVar("offeragent_query_runtime", default=None)


def current_query_runtime() -> QueryRuntime | None:
    return _runtime.get()


@contextmanager
def query_runtime(
    task_id: str,
    timeout_seconds: float,
    progress_sink: ProgressSink | None = None,
) -> Iterator[QueryRuntime]:
    runtime = QueryRuntime(
        task_id=task_id,
        timeout_seconds=max(1.0, timeout_seconds),
        progress_sink=progress_sink,
    )
    token = _runtime.set(runtime)
    try:
        runtime.emit("started", "已开始处理问题")
        yield runtime
    finally:
        _runtime.reset(token)


def emit_progress(
    stage: str,
    message: str,
    *,
    status: str = "running",
    metadata: dict[str, Any] | None = None,
) -> None:
    runtime = current_query_runtime()
    if runtime is not None:
        runtime.emit(stage, message, status=status, metadata=metadata)


@dataclass
class _QueryRun:
    task_id: str
    owner_id: str
    status: str = "queued"
    current_stage: str = "queued"
    current_message: str = "等待开始"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    started_at: datetime | None = None
    result: Any | None = None
    events: list[SseEvent] = field(default_factory=list)


class QueryRunNotFound(LookupError):
    """查询运行不存在。"""


class QueryRunForbidden(PermissionError):
    """当前用户无权读取查询运行。"""


class QueryRunStore:
    """进程内问答运行状态和 SSE 事件存储。

    查询结果仍由 ``POST /api/query`` 返回；Store 只承担活动运行的进度、
    重连所需事件和最终快照，避免把完整模型上下文写入日志或 SSE。
    """

    TERMINAL_STATUSES = frozenset({"completed", "failed", "waiting_clarification"})

    def __init__(self, *, max_runs: int = 256) -> None:
        self.max_runs = max(16, max_runs)
        self._runs: dict[str, _QueryRun] = {}
        self._condition = threading.Condition(threading.RLock())

    def start(self, task_id: str, owner_id: str) -> None:
        with self._condition:
            if task_id in self._runs:
                raise ValueError(f"查询任务已存在：{task_id}")
            now = datetime.now(timezone.utc)
            self._runs[task_id] = _QueryRun(
                task_id=task_id,
                owner_id=owner_id,
                status="running",
                current_stage="started",
                current_message="已开始处理问题",
                created_at=now,
                updated_at=now,
                started_at=now,
            )
            self._append_locked(
                self._runs[task_id],
                stage="started",
                message="已开始处理问题",
                status="running",
                metadata={},
                terminal=False,
            )

    def publish(
        self,
        task_id: str,
        stage: str,
        message: str,
        status: str,
        metadata: dict[str, Any],
    ) -> None:
        with self._condition:
            run = self._get_locked(task_id)
            if run.status in self.TERMINAL_STATUSES:
                return
            self._append_locked(
                run,
                stage=stage,
                message=message,
                status=status,
                metadata=metadata,
                terminal=False,
            )

    def finish(self, task_id: str, result: Any) -> None:
        status = getattr(result, "status", "failed")
        terminal_status = status if status in self.TERMINAL_STATUSES else "failed"
        elapsed_ms = getattr(result, "elapsed_ms", None)
        message = getattr(result, "message", "处理完成")
        stage = getattr(result, "failure_stage", None) or "completed"
        if terminal_status == "failed" and getattr(result, "error_code", None) == "timeout":
            stage = getattr(result, "failure_stage", None) or "timeout"
            message = getattr(result, "analysis", None) or "处理超时"
        with self._condition:
            run = self._get_locked(task_id)
            run.result = result
            run.status = terminal_status
            run.updated_at = datetime.now(timezone.utc)
            self._append_locked(
                run,
                stage=stage,
                message=message,
                status=terminal_status,
                metadata={
                    "elapsed_ms": elapsed_ms,
                    "result_status": status,
                },
                terminal=True,
            )
            self._prune_locked()

    def snapshot(self, task_id: str, owner_id: str) -> dict[str, Any]:
        with self._condition:
            run = self._get_locked(task_id)
            self._check_owner(run, owner_id)
            elapsed_ms = None
            if run.started_at is not None:
                end = run.updated_at if run.status in self.TERMINAL_STATUSES else datetime.now(timezone.utc)
                elapsed_ms = max(0, int((end - run.started_at).total_seconds() * 1000))
            return {
                "task_id": run.task_id,
                "status": run.status,
                "current_stage": run.current_stage,
                "current_message": run.current_message,
                "created_at": run.created_at.isoformat(),
                "updated_at": run.updated_at.isoformat(),
                "elapsed_ms": elapsed_ms,
                "result": run.result.model_dump(mode="json") if run.result is not None else None,
            }

    def wait_events(
        self,
        task_id: str,
        owner_id: str,
        after_sequence: int,
        timeout: float = 1.0,
    ) -> list[SseEvent]:
        with self._condition:
            run = self._get_locked(task_id)
            self._check_owner(run, owner_id)
            events = [event for event in run.events if event.sequence > after_sequence]
            if events:
                return events
            self._condition.wait(timeout=max(0.05, timeout))
            run = self._get_locked(task_id)
            self._check_owner(run, owner_id)
            return [event for event in run.events if event.sequence > after_sequence]

    def _append_locked(
        self,
        run: _QueryRun,
        *,
        stage: str,
        message: str,
        status: str,
        metadata: dict[str, Any],
        terminal: bool,
    ) -> None:
        run.status = status if terminal and status in self.TERMINAL_STATUSES else "running"
        run.current_stage = stage
        run.current_message = message
        run.updated_at = datetime.now(timezone.utc)
        sequence = len(run.events) + 1
        run.events.append(
            SseEvent(
                event_id=f"{run.task_id}:{sequence}:{uuid.uuid4().hex[:8]}",
                task_id=run.task_id,
                sequence=sequence,
                event_type=f"query.{stage}",
                timestamp=run.updated_at,
                payload={
                    "stage": stage,
                    "message": message,
                    "status": status,
                    **metadata,
                },
            )
        )
        self._condition.notify_all()

    def _get_locked(self, task_id: str) -> _QueryRun:
        run = self._runs.get(task_id)
        if run is None:
            raise QueryRunNotFound(task_id)
        return run

    @staticmethod
    def _check_owner(run: _QueryRun, owner_id: str) -> None:
        if run.owner_id != owner_id:
            raise QueryRunForbidden(run.task_id)

    def _prune_locked(self) -> None:
        completed = [
            run for run in self._runs.values() if run.status in self.TERMINAL_STATUSES
        ]
        overflow = len(self._runs) - self.max_runs
        if overflow <= 0:
            return
        for run in sorted(completed, key=lambda item: item.updated_at)[:overflow]:
            self._runs.pop(run.task_id, None)


__all__ = [
    "ProgressSink",
    "QueryRunForbidden",
    "QueryRunNotFound",
    "QueryRunStore",
    "QueryRuntime",
    "QueryTimeoutError",
    "current_query_runtime",
    "emit_progress",
    "query_runtime",
]
