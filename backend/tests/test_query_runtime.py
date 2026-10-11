from __future__ import annotations

import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes
from app.models import QueryResult
from app.query_runtime import QueryRunForbidden, QueryRunStore, QueryTimeoutError, query_runtime


def test_query_runtime_reports_timeout_stage_and_elapsed_time() -> None:
    with query_runtime("query-timeout", 1) as runtime:
        runtime.emit("sql_generation", "正在生成只读 SQL")
        runtime.started_at = time.monotonic() - 2
        with pytest.raises(QueryTimeoutError) as caught:
            runtime.check()

    assert caught.value.stage == "sql_generation"
    assert caught.value.elapsed_ms >= 2000


def test_query_run_store_replays_progress_and_protects_owner() -> None:
    store = QueryRunStore()
    store.start("query-1", "user-a")
    store.publish("query-1", "sql_generation", "正在生成只读 SQL", "running", {"elapsed_ms": 10})
    result = QueryResult(
        task_id="query-1",
        status="completed",
        route="database_query",
        message="查询完成",
        elapsed_ms=25,
    )
    store.finish("query-1", result)

    events = store.wait_events("query-1", "user-a", 0, timeout=0)
    assert [event.sequence for event in events] == [1, 2, 3]
    assert events[1].payload["stage"] == "sql_generation"
    assert store.snapshot("query-1", "user-a")["status"] == "completed"
    with pytest.raises(QueryRunForbidden):
        store.snapshot("query-1", "user-b")


def test_query_progress_api_replays_terminal_sse_event(monkeypatch) -> None:
    store = QueryRunStore()
    store.start("query-sse", "local_user")
    store.publish("query-sse", "sql_validation", "正在校验 SQL 安全性", "running", {"elapsed_ms": 8})
    store.finish(
        "query-sse",
        QueryResult(
            task_id="query-sse",
            status="completed",
            route="database_query",
            message="查询完成",
            elapsed_ms=15,
        ),
    )
    monkeypatch.setattr(routes.service, "query_runs", store)
    app = FastAPI()
    app.include_router(routes.router)

    with TestClient(app) as client:
        response = client.get(
            "/api/query-runs/query-sse/events",
            headers={"Accept": "text/event-stream"},
        )

    assert response.status_code == 200
    assert "query.sql_validation" in response.text
    assert "query.completed" in response.text
