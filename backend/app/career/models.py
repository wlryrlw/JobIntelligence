from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from ..contracts import ResumeEvidence


class ResumeRecord(BaseModel):
    """简历元数据；不保存原文、脱敏全文或完整证据文本。"""

    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(min_length=8, max_length=100)
    user_id: str = Field(min_length=1, max_length=200)
    filename: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=100)
    size_bytes: int = Field(ge=1)
    page_count: int = Field(ge=1)
    redaction_count: int = Field(ge=0)
    source_hash: str = Field(min_length=64, max_length=64)
    created_at: datetime
    status: str = Field(pattern=r"^(ready|failed)$")
    skill_count: int = Field(ge=0)
    project_count: int = Field(ge=0)


class ResumeUploadResponse(BaseModel):
    """上传接口的公开响应，不返回原始文件或完整简历文本。"""

    model_config = ConfigDict(extra="forbid")

    resume_id: str = Field(min_length=8, max_length=100)
    filename: str = Field(min_length=1, max_length=255)
    status: str = Field(pattern=r"^ready$")
    page_count: int = Field(ge=1)
    skill_count: int = Field(ge=0)
    project_count: int = Field(ge=0)
    redaction_count: int = Field(ge=0)
    message: str = Field(min_length=1, max_length=300)


def public_resume_response(record: ResumeRecord, _: ResumeEvidence) -> ResumeUploadResponse:
    return ResumeUploadResponse(
        resume_id=record.resume_id,
        filename=record.filename,
        status="ready",
        page_count=record.page_count,
        skill_count=record.skill_count,
        project_count=record.project_count,
        redaction_count=record.redaction_count,
        message="简历已解析并完成本地脱敏，原始文件不会通过 API 返回。",
    )
