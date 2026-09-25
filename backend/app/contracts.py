"""阶段 0 的跨模块 Pydantic 契约。

这些模型是任务 API、运行时存储和后续 Agent 之间的边界。阶段 0 的假
执行器只使用其中的任务和事件模型；其它模型先把后续 Agent 的输入输出
形状固定下来，避免后续实现退化为未校验的字典。
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    """返回带时区的 UTC 时间，作为所有运行时记录的统一时间格式。"""

    return datetime.now(timezone.utc)


class TaskStatus(StrEnum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_CLARIFICATION = "waiting_clarification"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class CrawlStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    BLOCKED = "blocked"
    ETL_RUNNING = "etl_running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    NOT_FOUND = "not_found"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    EXECUTION_FAILED = "execution_failed"
    INTERNAL_ERROR = "internal_error"


class SqlValidation(StrEnum):
    PASSED = "passed"
    REJECTED = "rejected"


class SqlExecution(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class SqlErrorKind(StrEnum):
    """SQL 执行失败的稳定分类，也是 QueryAgent 的重试边界。"""

    SYNTAX = "syntax"
    UNKNOWN_FIELD = "unknown_field"
    UNKNOWN_TABLE = "unknown_table"
    TYPE_BINDING = "type_binding"
    PERMISSION = "permission"
    SECURITY = "security"
    DATABASE_NOT_FOUND = "database_not_found"
    TIMEOUT = "timeout"
    EMPTY_RESULT = "empty_result"
    MODEL_UNAVAILABLE = "model_unavailable"
    UNKNOWN = "unknown"


class QueryEventType(StrEnum):
    """QueryAgent 对外发布的结构化事件类型。"""

    SCHEMA_LOADED = "query.schema_loaded"
    SQL_GENERATED = "query.sql_generated"
    SQL_VALIDATED = "query.sql_validated"
    SQL_EXECUTED = "query.sql_executed"
    SQL_REPAIR = "query.sql_repair"
    COMPLETED = "query.completed"
    FAILED = "query.failed"




class EvidenceRef(BaseModel):
    """指向简历或 JD 原文的短证据，不保存完整敏感文本。"""

    source: Literal["resume", "jd", "system"]
    quote: str = Field(min_length=1, max_length=500)
    location: str | None = Field(default=None, max_length=200)

class SkillMention(BaseModel):
    """经过 alias/canonical 归一化的单项技能证据。"""

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, max_length=200)
    display: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    requirement: Literal["must_have", "preferred", "unknown"] = "unknown"
    evidence: EvidenceRef
    confidence: float = Field(ge=0, le=1)
    source: Literal["regex", "llm", "resume_rule"]
    extractor_version: str = Field(min_length=1, max_length=100)


class ResumeProjectEvidence(BaseModel):
    """简历中的项目级证据，不保存完整简历文本。"""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    summary: str = Field(min_length=1, max_length=500)
    skills: list[str] = Field(default_factory=list, max_length=50)
    evidence: list[EvidenceRef] = Field(default_factory=list, max_length=10)
    quantitative_evidence: list[str] = Field(default_factory=list, max_length=30)


class ResumeEvidence(BaseModel):
    """可供 CareerAgent 使用的最小简历证据集。"""

    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(min_length=8, max_length=200)
    source_hash: str = Field(min_length=64, max_length=64)
    page_count: int = Field(ge=1)
    pii_redaction_count: int = Field(ge=0)
    skills: list[SkillMention] = Field(default_factory=list, max_length=100)
    projects: list[ResumeProjectEvidence] = Field(default_factory=list, max_length=100)
    quantitative_evidence: list[str] = Field(default_factory=list, max_length=100)
    extractor_version: str = Field(min_length=1, max_length=100)


class JobProfileSkill(BaseModel):
    """岗位级技能出现率和 JD 证据。"""

    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=100)
    occurrence_count: int = Field(ge=0)
    denominator: int = Field(ge=1)
    occurrence_rate: float = Field(ge=0, le=1)
    requirement: Literal["must_have", "preferred", "unknown"]
    jd_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=3)
    sample_size_note: str | None = Field(default=None, max_length=300)
    extractor_version: str = Field(min_length=1, max_length=100)


class JobProfile(BaseModel):
    """由岗位快照确定性聚合得到的岗位画像。"""

    model_config = ConfigDict(extra="forbid")

    target: TargetJobRequest
    dataset_version: str = Field(min_length=1, max_length=200)
    sample_size: int = Field(ge=1)
    valid_jd_count: int = Field(ge=1)
    skills: list[JobProfileSkill] = Field(default_factory=list, max_length=200)
    salary_sample_count: int = Field(ge=0)
    salary_p25_k: float | None = Field(default=None, ge=0)
    salary_p50_k: float | None = Field(default=None, ge=0)
    salary_p75_k: float | None = Field(default=None, ge=0)
    experience_distribution: dict[str, int] = Field(default_factory=dict)
    degree_distribution: dict[str, int] = Field(default_factory=dict)
    small_sample_warning: str | None = Field(default=None, max_length=300)


class TargetJobRequest(BaseModel):
    keyword: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    company: str | None = Field(default=None, max_length=200)
    experience: str | None = Field(default=None, max_length=100)
    degree: str | None = Field(default=None, max_length=100)

    @field_validator("keyword")
    @classmethod
    def keyword_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("岗位关键词不能为空")
        return value


class UserRequest(BaseModel):
    """Coordinator 接收的用户请求。

    API 创建任务时不要求调用方提供 task_id；任务执行器会在接收并校验
    请求后补齐它。保留该字段是为了让恢复任务和内部 Agent 使用同一契约。
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = ""
    session_id: str = Field(default="default-session", min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=5000)
    resume_id: str | None = Field(default=None, max_length=200)
    target: TargetJobRequest | None = None

    @field_validator("text")
    @classmethod
    def text_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("用户请求不能为空")
        return value


class FailureInfo(BaseModel):
    code: TaskErrorCode
    message: str = Field(min_length=1, max_length=1000)
    retryable: bool = False
    detail: str | None = Field(default=None, max_length=2000)


class TaskResult(BaseModel):
    """任务完成后的结构化结果；阶段 0 使用 fake 结果，后续可扩展。"""

    summary: str = Field(min_length=1, max_length=2000)
    steps: list[str] = Field(default_factory=list, max_length=100)
    data: dict[str, Any] = Field(default_factory=dict)


class TaskSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=200)
    status: TaskStatus
    session_id: str = Field(min_length=1, max_length=200)
    request: UserRequest
    created_at: datetime
    updated_at: datetime
    sequence: int = Field(default=0, ge=0)
    result: TaskResult | None = None
    error: FailureInfo | None = None


class SseEvent(BaseModel):
    """append-only 事件模型，同时是 SSE 和轮询的共同协议。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    event_id: str = Field(min_length=1, max_length=200)
    task_id: str = Field(min_length=1, max_length=200)
    sequence: int = Field(ge=1)
    type: str = Field(alias="event_type", min_length=1, max_length=100)
    timestamp: datetime
    payload: dict[str, Any] = Field(default_factory=dict)

    @property
    def event_type(self) -> str:
        return self.type


class CrawlRunResult(BaseModel):
    run_id: str = Field(min_length=1, max_length=200)
    status: CrawlStatus
    dataset_version: str | None = None
    job_count: int = Field(default=0, ge=0)
    detail_count: int = Field(default=0, ge=0)
    failure: FailureInfo | None = None


class SqlAttempt(BaseModel):
    attempt: int = Field(ge=1, le=3)
    sql: str = Field(min_length=1, max_length=20000)
    validation: SqlValidation
    execution: SqlExecution
    error_kind: SqlErrorKind | None = None
    retryable: bool = False


class QueryPlan(BaseModel):
    """QueryAgent 的最小输入契约。"""

    query: str = Field(min_length=1, max_length=5000)
    database: str = Field(default="", max_length=100)
    dataset_version: str | None = Field(default=None, max_length=200)
    filters: list[str] = Field(default_factory=list, max_length=50)
    metrics: list[str] = Field(default_factory=list, max_length=50)
    dimensions: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("query")
    @classmethod
    def query_must_not_be_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("查询问题不能为空")
        return value


class SqlGeneration(BaseModel):
    """模型生成 SQL 时使用的严格结构化输出。"""

    sql: str = Field(min_length=1, max_length=20000)
    explanation: str = Field(default="", max_length=1000)


class SchemaCatalogField(BaseModel):
    table: str = Field(min_length=1, max_length=128)
    name: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=200)
    type: str = Field(default="文本", max_length=50)
    source_type: str = Field(default="VARCHAR", max_length=50)
    description: str = Field(default="", max_length=1000)
    aliases: list[str] = Field(default_factory=list, max_length=50)
    role: str = Field(default="dimension", max_length=50)
    aggregation: str = Field(default="group", max_length=50)
    samples: list[str] = Field(default_factory=list, max_length=10)
    data_profile: dict[str, Any] = Field(default_factory=dict)


class SchemaCatalogTable(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    label: str = Field(default="", max_length=200)
    description: str = Field(default="", max_length=1000)
    database: str = Field(default="", max_length=100)
    primary_key: list[str] = Field(default_factory=list, max_length=20)
    row_count: int = Field(default=0, ge=0)
    fields: list[SchemaCatalogField] = Field(default_factory=list)


class SchemaCatalog(BaseModel):
    """可完整注入 SQL Prompt 的确定性 Schema。"""

    database: str = Field(min_length=1, max_length=100)
    dataset_version: str = Field(default="unknown", max_length=200)
    tables: list[SchemaCatalogTable] = Field(default_factory=list)
    relations: list[dict[str, str]] = Field(default_factory=list)
    prompt_token_count: int = Field(default=0, ge=0)
    prompt_char_count: int = Field(default=0, ge=0)

    @property
    def fields(self) -> list[SchemaCatalogField]:
        return [field for table in self.tables for field in table.fields]

    @property
    def token_count(self) -> int:
        return self.prompt_token_count

    def to_prompt(self) -> str:
        return self.render()

    def render(self) -> str:
        """以稳定顺序渲染，避免同一数据集的 Prompt 随文件顺序漂移。"""

        lines = [
            f"数据库：{self.database}",
            f"数据集版本：{self.dataset_version}",
            "仅允许生成一条 DuckDB SELECT/WITH 只读 SQL；必须显式列出字段，禁止 SELECT *。",
        ]
        for table in sorted(self.tables, key=lambda item: item.name):
            lines.append(
                f"表 {table.name}（{table.label}，{table.row_count} 行）：{table.description}"
            )
            if table.primary_key:
                lines.append(f"主键：{', '.join(table.primary_key)}")
            for field in table.fields:
                aliases = "、".join(field.aliases) or "无"
                samples = "、".join(field.samples) or "无"
                lines.append(
                    f"- {table.name}.{field.name} | {field.label} | {field.type}/{field.source_type} | "
                    f"{field.description} | 别名：{aliases} | 聚合：{field.aggregation} | 样例：{samples}"
                )
        for relation in self.relations:
            lines.append(
                "关系："
                + f"{relation.get('left_table', '')}.{relation.get('left_field', '')} = "
                + f"{relation.get('right_table', '')}.{relation.get('right_field', '')}"
                + (f"（{relation.get('description')}）" if relation.get("description") else "")
            )
        return "\n".join(lines)

    def with_prompt_stats(self) -> "SchemaCatalog":
        rendered = self.render()
        # 项目不额外引入 tokenizer；以字符长度 / 4 做保守、可复现的估算，
        # 并明确字段名，避免把估算值误报成供应商精确 token 数。
        import math

        return self.model_copy(
            update={
                "prompt_char_count": len(rendered),
                "prompt_token_count": max(1, math.ceil(len(rendered) / 4)),
            }
        )


class QueryAgentResult(BaseModel):
    """QueryAgent 对 Coordinator 和 SSE 的输出契约。"""

    model_config = ConfigDict(populate_by_name=True)

    query: str = Field(min_length=1, max_length=5000)
    database: str = Field(min_length=1, max_length=100)
    dataset_version: str | None = Field(default=None, max_length=200)
    status: Literal["completed", "failed"]
    sql: str = ""
    columns: list[str] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    attempts: list[SqlAttempt] = Field(default_factory=list, max_length=3)
    error_kind: SqlErrorKind | None = None
    error: str | None = Field(default=None, max_length=2000)
    schema_catalog: SchemaCatalog | None = Field(default=None, alias="schema")

    @property
    def schema(self) -> SchemaCatalog | None:
        return self.schema_catalog




class SkillCoverageItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, max_length=200)
    category: str = Field(default="other", max_length=100)
    status: Literal["covered", "partial", "missing"]
    requirement: Literal["must_have", "preferred", "unknown"] = "unknown"
    jd_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=10)
    resume_evidence: list[EvidenceRef] = Field(default_factory=list, max_length=10)
    suggestion: str | None = Field(default=None, max_length=1000)
    confidence: float = Field(default=0, ge=0, le=1)
    extractor_version: str = Field(default="unknown", max_length=100)
    jd_occurrence_rate: float | None = Field(default=None, ge=0, le=1)


class CareerReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    summary: str = Field(min_length=1, max_length=5000)
    resume_id: str = Field(min_length=8, max_length=200)
    target: TargetJobRequest
    job_profile: JobProfile
    facts: list[str] = Field(default_factory=list, max_length=100)
    recommendations: list[str] = Field(default_factory=list, max_length=50)
    skill_coverage: list[SkillCoverageItem] = Field(default_factory=list, max_length=200)
    rewrite_directions: list[str] = Field(default_factory=list, max_length=50)
    interview_checklist: list[str] = Field(default_factory=list, max_length=50)
    generated_at: datetime = Field(default_factory=utc_now)


# 便于调用方按文档中的通用名称导入。
ErrorCode = TaskErrorCode


__all__ = [
    "CareerReport",
    "CrawlRunResult",
    "CrawlStatus",
    "ErrorCode",
    "EvidenceRef",
    "FailureInfo",
    "JobProfile",
    "JobProfileSkill",
    "ResumeEvidence",
    "ResumeProjectEvidence",
    "SkillCoverageItem",
    "SkillMention",
    "SqlAttempt",
    "SqlErrorKind",
    "SqlExecution",
    "SqlGeneration",
    "SqlValidation",
    "QueryEventType",
    "QueryAgentResult",
    "QueryPlan",
    "SchemaCatalog",
    "SchemaCatalogField",
    "SchemaCatalogTable",
    "SseEvent",
    "TargetJobRequest",
    "TaskErrorCode",
    "TaskResult",
    "TaskSnapshot",
    "TaskStatus",
    "UserRequest",
    "utc_now",
]
