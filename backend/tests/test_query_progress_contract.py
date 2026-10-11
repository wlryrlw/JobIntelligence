from __future__ import annotations

from app.models import QueryResult
from app.query_runtime import QueryRunStore


def test_terminal_progress_event_contains_the_final_result_status() -> None:
    store = QueryRunStore()
    store.start("terminal-progress", "local_user")
    store.finish(
        "terminal-progress",
        QueryResult(
            task_id="terminal-progress",
            status="completed",
            route="database_query",
            message="查询完成",
            analysis="当前共有 91 个岗位。",
            rows=[{"job_count": 91}],
        ),
    )

    events = store.wait_events("terminal-progress", "local_user", 0, timeout=0)

    assert events[-1].payload["status"] == "completed"
    assert events[-1].payload["message"] == "查询完成"
