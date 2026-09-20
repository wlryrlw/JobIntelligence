"""阶段 0 的非阻塞假任务执行器。"""

from __future__ import annotations

import asyncio
from pathlib import Path
from uuid import uuid4

from .contracts import (
    FailureInfo,
    SseEvent,
    TaskErrorCode,
    TaskResult,
    TaskSnapshot,
    TaskStatus,
    UserRequest,
    utc_now,
)
from .runtime_store import RuntimeStore


class FakeTaskExecutor:
    """用 async sleep 模拟长任务，验证事件协议而不调用 LLM 或 scraper。"""

    def __init__(self, store: RuntimeStore, step_delay: float = 0.02) -> None:
        if step_delay < 0:
            raise ValueError("step_delay 不能小于 0")
        self.store = store
        self.step_delay = step_delay
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()

    async def create(self, request: UserRequest) -> TaskSnapshot:
        task_id = uuid4().hex
        now = utc_now()
        task_request = request.model_copy(update={"task_id": task_id})
        snapshot = TaskSnapshot(
            task_id=task_id,
            status=TaskStatus.CREATED,
            session_id=task_request.session_id,
            request=task_request,
            created_at=now,
            updated_at=now,
        )
        self.store.save_snapshot(snapshot)
        self._record(snapshot, "task.created", {"message": "任务已创建"})
        async with self._lock:
            self._workers[task_id] = asyncio.create_task(self._run(task_id))
        return self.store.get_snapshot(task_id) or snapshot

    def get(self, task_id: str) -> TaskSnapshot | None:
        try:
            return self.store.get_snapshot(task_id)
        except ValueError:
            return None

    def events(self, task_id: str, after_sequence: int = 0) -> list[SseEvent]:
        if self.get(task_id) is None:
            return []
        return self.store.list_events(task_id, after_sequence=after_sequence)

    def sequence_after_event_id(self, task_id: str, event_id: str) -> int | None:
        if self.get(task_id) is None:
            return None
        return self.store.sequence_after_event_id(task_id, event_id)

    async def _run(self, task_id: str) -> None:
        try:
            snapshot = self._transition(task_id, TaskStatus.RUNNING)
            self._record(snapshot, "task.started", {"message": "假任务开始执行"})

            steps = ["validate_contract", "run_fake_work", "assemble_result"]
            for index, step in enumerate(steps, start=1):
                self._record(
                    snapshot,
                    "step.started",
                    {"step": step, "index": index, "message": f"开始：{step}"},
                )
                await asyncio.sleep(self.step_delay)
                snapshot = self.store.get_snapshot(task_id) or snapshot
                self._record(
                    snapshot,
                    "heartbeat",
                    {"step": step, "message": f"仍在执行：{step}"},
                )
                await asyncio.sleep(self.step_delay)
                snapshot = self.store.get_snapshot(task_id) or snapshot
                self._record(
                    snapshot,
                    "step.completed",
                    {"step": step, "index": index, "message": f"完成：{step}"},
                )

            snapshot = self.store.get_snapshot(task_id) or snapshot
            result = TaskResult(
                summary="阶段 0 假任务已完成，未调用真实 LLM 或 scraper。",
                steps=steps,
                data={"executor": "fake", "contract_validated": True},
            )
            completed = snapshot.model_copy(
                update={
                    "status": TaskStatus.COMPLETED,
                    "result": result,
                    "updated_at": utc_now(),
                    "error": None,
                }
            )
            self._record(completed, "task.completed", {"result": result.model_dump(mode="json")})
        except asyncio.CancelledError:
            snapshot = self.store.get_snapshot(task_id)
            if snapshot and snapshot.status not in {
                TaskStatus.COMPLETED,
                TaskStatus.FAILED,
                TaskStatus.CANCELLED,
            }:
                cancelled = snapshot.model_copy(
                    update={
                        "status": TaskStatus.CANCELLED,
                        "updated_at": utc_now(),
                        "error": FailureInfo(
                            code=TaskErrorCode.CANCELLED,
                            message="任务已取消",
                        ),
                    }
                )
                self._record(cancelled, "task.cancelled", {"message": "任务已取消"})
            raise
        except Exception as exc:
            snapshot = self.store.get_snapshot(task_id)
            if snapshot:
                failed = snapshot.model_copy(
                    update={
                        "status": TaskStatus.FAILED,
                        "updated_at": utc_now(),
                        "error": FailureInfo(
                            code=TaskErrorCode.EXECUTION_FAILED,
                            message="假任务执行失败",
                            detail=str(exc)[:2000],
                        ),
                    }
                )
                self._record(failed, "task.failed", {"error": failed.error.model_dump(mode="json")})
        finally:
            self._workers.pop(task_id, None)

    def _transition(self, task_id: str, status: TaskStatus) -> TaskSnapshot:
        snapshot = self.store.get_snapshot(task_id)
        if snapshot is None:
            raise KeyError(task_id)
        updated = snapshot.model_copy(update={"status": status, "updated_at": utc_now()})
        self.store.save_snapshot(updated)
        return updated

    def _record(self, snapshot: TaskSnapshot, event_type: str, payload: dict) -> SseEvent:
        latest = self.store.get_snapshot(snapshot.task_id) or snapshot
        if snapshot.result is not None:
            latest = latest.model_copy(update={"result": snapshot.result})
        if snapshot.error is not None:
            latest = latest.model_copy(update={"error": snapshot.error})
        event = SseEvent(
            event_id=uuid4().hex,
            task_id=latest.task_id,
            sequence=latest.sequence + 1,
            event_type=event_type,
            timestamp=utc_now(),
            payload=payload,
        )
        self.store.append_event(event)
        updated = latest.model_copy(
            update={"sequence": event.sequence, "updated_at": event.timestamp}
        )
        if event_type == "task.completed":
            updated = updated.model_copy(update={"status": TaskStatus.COMPLETED})
        elif event_type == "task.failed":
            updated = updated.model_copy(update={"status": TaskStatus.FAILED})
        elif event_type == "task.cancelled":
            updated = updated.model_copy(update={"status": TaskStatus.CANCELLED})
        self.store.save_snapshot(updated)
        return event


def default_fake_executor(base_dir: Path) -> FakeTaskExecutor:
    return FakeTaskExecutor(RuntimeStore(base_dir / "data" / "local" / "runtime"))


__all__ = ["FakeTaskExecutor", "default_fake_executor"]
