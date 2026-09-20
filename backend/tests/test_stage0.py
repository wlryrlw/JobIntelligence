from __future__ import annotations

import json
from datetime import datetime, timezone

from fastapi.testclient import TestClient

from app.contracts import (
    SseEvent,
    TaskResult,
    TaskSnapshot,
    TaskStatus,
    UserRequest,
)
from app.api import routes
from app.fake_executor import FakeTaskExecutor
from app.main import app
from app.runtime_store import RuntimeStore


def test_contracts_reject_blank_user_request() -> None:
    try:
        UserRequest(text="   ")
    except ValueError as error:
        assert "用户请求不能为空" in str(error)
    else:
        raise AssertionError("空用户请求必须被拒绝")


def test_runtime_store_persists_snapshot_and_append_only_events(tmp_path) -> None:
    store = RuntimeStore(tmp_path / "runtime")
    now = datetime.now(timezone.utc)
    snapshot = TaskSnapshot(
        task_id="task-test",
        status=TaskStatus.CREATED,
        session_id="session-test",
        request=UserRequest(task_id="task-test", text="测试任务"),
        created_at=now,
        updated_at=now,
    )
    store.save_snapshot(snapshot)
    store.append_event(
        SseEvent(
            event_id="event-1",
            task_id="task-test",
            sequence=1,
            event_type="task.created",
            timestamp=now,
            payload={"message": "created"},
        )
    )

    assert store.get_snapshot("task-test") == snapshot
    assert [event.sequence for event in store.all_events("task-test")] == [1]
    assert store.list_events("task-test", after_sequence=1) == []
    assert json.loads(store.event_path("task-test").read_text())[
        "event_type"
    ] == "task.created"


def test_fake_task_api_completes_and_sse_replays_events(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "fake_executor",
        FakeTaskExecutor(RuntimeStore(tmp_path / "runtime"), step_delay=0),
    )
    with TestClient(app) as client:
        create = client.post(
            "/api/tasks",
            json={"text": "验证阶段 0", "session_id": "sse-test"},
        )
        assert create.status_code == 202
        created = create.json()
        task_id = created["task_id"]
        assert created["status"] == "created"

        with client.stream(
            "GET",
            f"/api/tasks/{task_id}/events",
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            stream_body = "".join(response.iter_text())

        assert "event: task.created" in stream_body
        assert "event: heartbeat" in stream_body
        assert "event: task.completed" in stream_body

        snapshot = client.get(f"/api/tasks/{task_id}")
        assert snapshot.status_code == 200
        result = snapshot.json()
        assert result["status"] == "completed"
        assert result["sequence"] >= 1
        assert result["result"]["data"]["executor"] == "fake"

        events = [
            line
            for line in stream_body.splitlines()
            if line.startswith("data: ")
        ]
        payloads = [json.loads(line.removeprefix("data: ")) for line in events]
        sequences = [payload["sequence"] for payload in payloads]
        assert sequences == sorted(set(sequences))
        last_event_id = payloads[1]["event_id"]

        resumed = client.get(
            f"/api/tasks/{task_id}/events",
            headers={
                "Accept": "text/event-stream",
                "Last-Event-ID": last_event_id,
            },
        )
        assert resumed.status_code == 200
        resumed_sequences = [
            json.loads(line.removeprefix("data: "))["sequence"]
            for line in resumed.text.splitlines()
            if line.startswith("data: ")
        ]
        assert resumed_sequences
        assert min(resumed_sequences) > payloads[1]["sequence"]


def test_existing_query_task_route_uses_local_scope_and_rejects_invalid_token(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        routes,
        "fake_executor",
        FakeTaskExecutor(RuntimeStore(tmp_path / "runtime"), step_delay=0),
    )
    with TestClient(app) as client:
        response = client.get("/api/tasks/not-a-fake-task")
        assert response.status_code == 404
        invalid = client.get("/api/tasks/not-a-fake-task", headers={"Authorization": "Bearer invalid"})
        assert invalid.status_code == 401

