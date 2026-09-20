from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from .contracts import CareerReport


class ConfirmedField(BaseModel):
    name: str
    aggregation: Literal["auto", "group", "sum", "avg", "count", "max", "min"] = "auto"
    tableId: str | None = None


class AnalysisTableInput(BaseModel):
    task_id: str
    title: str
    query: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class WorkspaceConfig(BaseModel):
    schema_fields: list[ConfirmedField] = Field(default_factory=list)
    analysis_table_ids: list[str] = Field(default_factory=list)
    analysis_tables: list[AnalysisTableInput] = Field(default_factory=list)
    # 兼容旧版前端的 fields 属性。
    fields: list[ConfirmedField] = Field(default_factory=list)


class QueryRequest(BaseModel):
    query: str = Field(min_length=1, max_length=500)
    session_id: str = "demo-session"
    workspace: WorkspaceConfig | None = None


class ClarificationRequest(BaseModel):
    option_id: str


class ClarificationOption(BaseModel):
    id: str
    label: str
    description: str
    recommended: bool = False


class Clarification(BaseModel):
    parameter: str = "other"
    question: str
    reason: str
    options: list[ClarificationOption]


class Interpretation(BaseModel):
    metric: str
    dimension: str
    time_range: str
    table: str
    assumptions: list[str] = []


class ChartSpec(BaseModel):
    """受限的业务图表契约；前端只消费该契约的确定性 ECharts 映射。"""

    type: Literal["bar", "pie", "line", "histogram"]
    title: str
    source_task_id: str
    category_field: str
    value_field: str
    max_items: int = Field(default=12, ge=3, le=30)
    bins: int = Field(default=10, ge=3, le=50)


class VisualizationSpec(ChartSpec):
    """旧 API 名称的兼容别名。"""


class AnalysisReport(BaseModel):
    title: str
    markdown: str
    visualizations: list[VisualizationSpec] = Field(default_factory=list)


class QueryResult(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    task_id: str
    status: Literal["waiting_clarification", "completed", "failed"]
    route: Literal["database_query", "data_qa", "career_advice", "direct_response"]
    message: str
    interpretation: Interpretation | None = None
    clarification: Clarification | None = None
    steps: list[str] = []
    sql: str | None = None
    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    analysis: str | None = None
    saved: bool = False
    route_reason: str | None = None
    retrieval: dict[str, Any] | None = None
    execution_log: list[dict[str, Any]] = Field(default_factory=list)
    sql_attempts: list[dict[str, Any]] = Field(default_factory=list, alias="attempts")
    dataset_version: str | None = None
    prompt_token_count: int | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    result_title: str | None = None
    analysis_sources: list[dict[str, Any]] = Field(default_factory=list)
    standalone_query: str | None = None
    schema_graph: dict[str, Any] | None = None
    workflow_mode: str | None = None
    report: AnalysisReport | None = None
    career_report: CareerReport | None = None
    report_tool_calls: list[dict[str, Any]] = Field(default_factory=list)


class SchemaField(BaseModel):
    name: str
    label: str
    type: str
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    role: str = ""
    aggregation: str = "none"
    source_type: str = ""
    data_profile: dict[str, Any] = Field(default_factory=dict)
    index_content: dict[str, str] = Field(default_factory=dict)


class SchemaTable(BaseModel):
    id: str
    name: str = ""
    label: str
    description: str
    fields: list[SchemaField]
    database: str = "askdata_mock"
    domain: str = ""
    business_terms: list[str] = Field(default_factory=list)
    primary_key: list[str] = Field(default_factory=list)
    row_count: int = 0


class SaveMemoryRequest(BaseModel):
    task_id: str


class SaveFieldMemoryRequest(BaseModel):
    table_id: str
    name: str
    label: str
    field_type: str


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)
