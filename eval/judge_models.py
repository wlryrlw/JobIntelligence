from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


JudgeMode = Literal["single", "reference"]
JudgeLabel = Literal["correct", "partially_correct", "incorrect", "uncertain"]
JudgeScore = Literal[0, 1, 2]


class JudgeVerdict(BaseModel):
    """LLM-as-judge 必须返回的最小结构化结果。"""

    model_config = ConfigDict(extra="forbid")

    score: JudgeScore
    label: JudgeLabel
    error_type: str = Field(default="none", min_length=1, max_length=50)
    reason: str = Field(min_length=1, max_length=500)


class JudgeCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    status: Literal["completed", "failed"]
    score: JudgeScore | None = None
    passed: bool | None = None
    label: JudgeLabel | None = None
    error_type: str | None = None
    reason: str | None = Field(default=None, max_length=500)
    final_execution_success: bool | None = None
    reference_execution_success: bool | None = None
    latency_ms: float | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=1000)


class JudgeSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requested_count: int = Field(ge=0)
    completed_count: int = Field(ge=0)
    failed_count: int = Field(ge=0)
    passed_count: int = Field(ge=0)
    score_0_count: int = Field(ge=0)
    score_1_count: int = Field(ge=0)
    score_2_count: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    average_score: float = Field(ge=0, le=2)


class JudgeRunResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_version: str
    dataset: str
    dataset_size: int = Field(ge=1)
    mode: JudgeMode
    model: str
    api_base_url: str
    prompt_version: str
    summaries: list[JudgeSummary] = Field(min_length=1, max_length=1)
    cases: list[JudgeCaseResult] = Field(min_length=1)
