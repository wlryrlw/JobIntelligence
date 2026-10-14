from __future__ import annotations

from app.querying.models import SqlExecution
from app.workflows.result_builder import ResultBuilder


def _state() -> dict[str, object]:
    return {
        "task_id": "result-builder-test",
        "intent": {"reason": "测试"},
        "schema_tables": [],
    }


def test_completed_query_has_fallback_analysis_when_database_returns_no_rows() -> None:
    execution = SqlExecution(
        sql="SELECT COUNT(*) AS job_count FROM jobs",
        success=True,
        columns=["job_count"],
        rows=[],
    )

    result = ResultBuilder.completed(
        _state(),
        [execution],
        execution,
        {"valid": True},
        [],
        [],
    )

    assert result.status == "completed"
    assert result.message == "查询完成（无匹配结果）"
    assert result.analysis == "查询已完成，但数据库没有返回匹配行。"


def test_completed_query_has_fallback_analysis_when_result_explanation_is_missing() -> None:
    execution = SqlExecution(
        sql="SELECT title FROM jobs LIMIT 1",
        success=True,
        columns=["title"],
        rows=[{"title": "AI Agent 工程师"}],
    )

    result = ResultBuilder.completed(
        _state(),
        [execution],
        execution,
        {"valid": True, "analysis": "  "},
        [],
        [],
    )

    assert result.status == "completed"
    assert result.analysis == "查询已完成，返回 1 行结果。"
