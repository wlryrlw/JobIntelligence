from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from .salary import Salary


class Record(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)


class SourceJob(BaseModel):
    """只读取已知业务字段；额外的 CDP 字段仅保留在原始归档中。"""
    model_config = ConfigDict(extra="ignore", strict=True)
    job_id: str = ""
    job_link: str = ""
    link: str = ""
    title: str = ""
    company: str = ""
    salary: str = ""
    salary_source: str = ""
    location: str = ""
    tags: str | list[str] = ""
    tags_list: str | list[str] = ""
    skills: str | list[str] = ""
    skill_tags: str | list[str] = ""
    welfare: str | list[str] = ""
    jd: str = ""
    company_scale: str = ""
    company_stage: str = ""
    company_industry: str = ""
    boss_active_status: str = ""
    captured_at: datetime | None = None

    @field_validator("captured_at", mode="before")
    @classmethod
    def parse_time(cls, value: object) -> datetime | None:
        if value is None:
            return None
        if isinstance(value, datetime):
            return normalize_time(value)
        if not isinstance(value, str):
            raise ValueError("captured_at 必须是 ISO 时间字符串")
        return normalize_time(datetime.fromisoformat(value))

    @model_validator(mode="after")
    def identity(self) -> SourceJob:
        if not (self.job_id.strip() or self.job_link.strip() or self.link.strip()):
            raise ValueError("缺少 job_id 和 job_link，无法可靠去重")
        return self


class SourceBatch(BaseModel):
    model_config = ConfigDict(extra="ignore")
    jobs: list[JsonValue]
    scraped_at: datetime
    keyword: str = ""
    city: str = ""
    requested_pages: int | None = Field(default=None, ge=0)
    completed_pages: int | None = Field(default=None, ge=0)
    scraper_version: str = "unknown"
    status: Literal["unknown", "succeeded", "failed", "blocked", "cancelled"] = "unknown"
    details: list[JsonValue] = Field(default_factory=list)

    @model_validator(mode="after")
    def page_counts(self) -> SourceBatch:
        if self.requested_pages is not None and self.completed_pages is not None and self.completed_pages > self.requested_pages:
            raise ValueError("completed_pages 不能超过 requested_pages")
        return self

    @field_validator("scraped_at")
    @classmethod
    def timestamp(cls, value: datetime) -> datetime:
        return normalize_time(value)


def normalize_time(value: datetime) -> datetime:
    # 源 scraper 使用 datetime.now().isoformat()，历史无时区时间按中国标准时间处理。
    if value.tzinfo is None:
        value = value.replace(tzinfo=ZoneInfo("Asia/Shanghai"))
    return value.astimezone(timezone.utc)


class Job(Salary):
    job_id: str
    title: str
    company: str
    city: str
    district: str
    location_raw: str
    tags: list[str]
    skills: list[str]
    welfare: list[str]
    experience: str
    degree: str
    company_scale: str
    company_stage: str
    company_industry: str
    boss_active_status: str
    job_link: str
    keyword: str
    salary_source: str
    first_seen_at: datetime
    last_seen_at: datetime
    latest_scraped_at: datetime
    dataset_version: str


class JobDetail(Record):
    job_id: str
    captured_at: datetime
    jd_text: str
    jd_hash: str
    detail_status: Literal["complete", "missing"]
    company: str
    skill_tags: list[str]


class JobSkill(Record):
    job_id: str
    skill_canonical: str
    skill_display: str
    category: str
    source: Literal["list_tags", "detail_tags"]
    evidence_text: str
    confidence: float = Field(ge=0, le=1)
    extractor_version: str
    extracted_at: datetime
    run_id: str


class CrawlRun(Record):
    run_id: str
    keyword: str
    city: str
    requested_pages: int | None
    completed_pages: int | None
    status: Literal["succeeded"]
    source_status: str
    job_count: int
    detail_count: int
    started_at: datetime
    finished_at: datetime
    failure_code: str | None = None
    scraper_version: str
    etl_version: str
    dataset_version: str
    source_hash: str


class QualityIssue(Record):
    source: str
    row: int
    code: str
    fields: list[str] = Field(default_factory=list)


class QualityReport(Record):
    run_id: str
    dataset_version: str
    input_jobs: int
    accepted_jobs: int
    rejected_jobs: int
    duplicate_jobs: int
    input_details: int
    rejected_details: int
    orphan_details: int
    total_jobs: int
    salary_status_counts: dict[str, int]
    salary_failure_count: int
    salary_untrusted_count: int
    missing_detail_count: int
    issues: list[QualityIssue]


class DatasetManifest(Record):
    dataset_version: str
    etl_version: str
    run_ids: list[str]
    file_hashes: dict[str, str]


TABLE_MODELS = {"jobs": Job, "job_details": JobDetail, "job_skills": JobSkill, "crawl_runs": CrawlRun}
