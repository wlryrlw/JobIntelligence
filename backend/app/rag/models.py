from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class JdDocument(BaseModel):
    """Milvus 中的一段岗位 JD，字段同时用于过滤和证据追溯。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=8, max_length=128)
    job_id: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=65535)
    text_hash: str = Field(min_length=64, max_length=64)
    title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    experience: str = Field(default="", max_length=100)
    dataset_version: str = Field(min_length=1, max_length=200)
    source_url: str = Field(default="", max_length=2048)
    chunk_index: int = Field(ge=0)


class RetrievedJd(BaseModel):
    """供 CareerAgent 使用的、可审计的 JD 检索证据。"""

    model_config = ConfigDict(extra="forbid")

    chunk_id: str
    job_id: str
    text: str
    title: str = ""
    company: str = ""
    city: str = ""
    experience: str = ""
    dataset_version: str
    source_url: str = ""
    rrf_score: float = 0.0
    rerank_score: float | None = None

    def public_source(self) -> dict[str, object]:
        return {
            "chunk_id": self.chunk_id,
            "job_id": self.job_id,
            "title": self.title,
            "company": self.company,
            "city": self.city,
            "experience": self.experience,
            "dataset_version": self.dataset_version,
            "source_url": self.source_url,
            "rrf_score": self.rrf_score,
            "rerank_score": self.rerank_score,
            "snippet": self.text[:500],
        }
