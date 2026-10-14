from __future__ import annotations

from typing import Any, TypedDict


class QueryState(TypedDict, total=False):
    """LangGraph 工作流状态。"""

    task_id: str
    query: str
    session_id: str
    workspace: dict[str, Any]
    access_scope: dict[str, Any]
    route_context: str
    short_term_context: str
    recent_result_context: str
    analysis_context: str
    analysis_sources: list[dict[str, Any]]
    intent: dict[str, Any]
    direct_response: str
    response_type: str
    standalone_query: str
    rewritten: bool
    target: dict[str, Any] | None
    resume_id: str | None
    use_jd_rag: bool
    career_mode: str | None
    extraction: dict[str, Any]
    schema_context: str
    schema_tables: list[dict[str, str]]
    clarification: dict[str, Any] | None
    dataset_version: str | None
    prompt_token_count: int | None
    tool_facts: dict[str, Any]
    execution_log: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]]
    workflow_mode: str
    result: dict[str, Any]
