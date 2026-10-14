from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


RagEvalBehavior = Literal["retrieval", "no_data"]
RagEvalVariant = Literal["A", "B", "D"]


class RagEvalTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=200)
    city: str | None = Field(default=None, max_length=100)
    experience: str | None = Field(default=None, max_length=100)


class RagEvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=2000)
    target: RagEvalTarget
    source_dataset_version: str = Field(min_length=1, max_length=200)
    relevant_job_ids: list[str] = Field(default_factory=list, max_length=100)
    answerable: bool
    expected_behavior: RagEvalBehavior
    required_aspects: list[str] = Field(default_factory=list, max_length=30)
    tags: list[str] = Field(default_factory=list, max_length=20)


class RagEvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["golden_set"]
    cases: list[RagEvalCase] = Field(min_length=1)


class DataCoverage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_digest: str = Field(min_length=64, max_length=64)
    dataset_version: str = Field(min_length=1, max_length=200)
    job_count: int = Field(ge=0)
    jd_count: int = Field(ge=0)
    document_count: int = Field(ge=0)
    city_counts: dict[str, int] = Field(default_factory=dict)
    experience_counts: dict[str, int] = Field(default_factory=dict)
    keyword_counts: dict[str, int] = Field(default_factory=dict)
    city_experience_counts: dict[str, int] = Field(default_factory=dict)


class RetrievedJobRank(BaseModel):
    model_config = ConfigDict(extra="forbid")

    job_id: str = Field(min_length=1, max_length=200)
    chunk_id: str = Field(min_length=1, max_length=128)
    city: str = Field(default="", max_length=100)
    experience: str = Field(default="", max_length=100)
    rank: int = Field(ge=1)


class RetrievalCaseMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    variant: RagEvalVariant
    answerable: bool
    relevant_count: int = Field(ge=0)
    ranked_job_ids: list[str] = Field(default_factory=list)
    hits: list[RetrievedJobRank] = Field(default_factory=list)
    recall_at_5: float | None = Field(default=None, ge=0, le=1)
    recall_at_10: float | None = Field(default=None, ge=0, le=1)
    recall_at_20: float | None = Field(default=None, ge=0, le=1)
    precision_at_8: float | None = Field(default=None, ge=0, le=1)
    mrr_at_10: float | None = Field(default=None, ge=0, le=1)
    ndcg_at_10: float | None = Field(default=None, ge=0, le=1)
    no_data_correct: bool | None = None
    filter_violation: bool = False


class RetrievalVariantSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    variant: RagEvalVariant
    total_cases: int = Field(ge=1)
    answerable_cases: int = Field(ge=0)
    no_data_cases: int = Field(ge=0)
    recall_at_5: float | None = Field(default=None, ge=0, le=1)
    recall_at_10: float | None = Field(default=None, ge=0, le=1)
    recall_at_20: float | None = Field(default=None, ge=0, le=1)
    precision_at_8: float | None = Field(default=None, ge=0, le=1)
    mrr_at_10: float | None = Field(default=None, ge=0, le=1)
    ndcg_at_10: float | None = Field(default=None, ge=0, le=1)
    no_data_accuracy: float | None = Field(default=None, ge=0, le=1)
    filter_violation_rate: float = Field(ge=0, le=1)


class RetrievalRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_version: str
    dataset: str
    dataset_size: int = Field(ge=1)
    dataset_version: str
    top_k: int = Field(ge=1)
    summaries: list[RetrievalVariantSummary] = Field(min_length=1, max_length=3)
    cases: list[RetrievalCaseMetric] = Field(min_length=1)


class RagEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chunk_id: str = Field(min_length=1, max_length=128)
    job_id: str = Field(min_length=1, max_length=200)
    title: str = Field(default="", max_length=300)
    company: str = Field(default="", max_length=300)
    city: str = Field(default="", max_length=100)
    experience: str = Field(default="", max_length=100)
    snippet: str = Field(min_length=1, max_length=1200)


class RagAnswerCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=20000)
    evidence: list[RagEvidence] = Field(default_factory=list, max_length=20)
    required_aspects: list[str] = Field(default_factory=list, max_length=30)
    answerable: bool

    @model_validator(mode="after")
    def validate_evidence_for_answerability(self) -> "RagAnswerCase":
        if self.answerable and not self.evidence:
            raise ValueError("answerable=true 的答案必须包含至少一条 JD evidence")
        return self


class RagAnswerDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["answer_set"]
    cases: list[RagAnswerCase] = Field(min_length=1)


class RagJudgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")

    faithfulness_score: Literal[0, 1, 2]
    relevance_score: Literal[0, 1, 2]
    unsupported_claims: list[str] = Field(default_factory=list, max_length=20)
    missing_aspects: list[str] = Field(default_factory=list, max_length=20)
    reason: str = Field(min_length=1, max_length=1000)


class RagJudgeCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["completed", "failed"]
    faithfulness_score: Literal[0, 1, 2] | None = None
    relevance_score: Literal[0, 1, 2] | None = None
    unsupported_claims: list[str] = Field(default_factory=list)
    missing_aspects: list[str] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=1000)
    latency_ms: float | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=1000)


class RagJudgeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    faithfulness_pass_rate: float = Field(ge=0, le=1)
    relevance_pass_rate: float = Field(ge=0, le=1)
    joint_pass_rate: float = Field(ge=0, le=1)
    average_faithfulness: float = Field(ge=0, le=2)
    average_relevance: float = Field(ge=0, le=2)


class RagJudgeRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_version: str
    dataset: str
    dataset_size: int = Field(ge=1)
    model: str
    api_base_url: str
    prompt_version: str
    summaries: list[RagJudgeSummary] = Field(min_length=1, max_length=1)
    cases: list[RagJudgeCaseResult] = Field(min_length=1)
