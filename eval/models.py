from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EvalCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1, max_length=100)
    query: str = Field(min_length=1, max_length=5000)
    expected_sql: str = Field(min_length=1, max_length=20000)
    first_sql: str = Field(min_length=1, max_length=20000)
    repair_sql: str = Field(min_length=1, max_length=20000)
    few_shot_sql: str | None = Field(default=None, max_length=20000)
    error_kind: str | None = Field(default=None, max_length=50)
    repeat: int = Field(default=1, ge=1, le=20)


class EvalDataset(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Literal["seed", "eval_dev", "eval_test"]
    cases: list[EvalCase] = Field(min_length=1)


class CaseMetrics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    first_pass_correct: bool
    final_correct: bool
    recovered: bool
    attempts: int = Field(ge=0, le=3)
    latency_ms: float = Field(ge=0)
    estimated_tokens: int = Field(ge=0)
    estimated_cost: float = Field(ge=0)
    status: Literal["completed", "failed"]
    error_kind: str | None = None


class BenchmarkResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runner_version: str
    dataset: str
    dataset_size: int = Field(ge=1)
    model: str
    correction_enabled: bool
    few_shot_enabled: bool
    cost_per_1k_tokens: float = Field(ge=0)
    first_pass_accuracy: float = Field(ge=0, le=1)
    final_accuracy: float = Field(ge=0, le=1)
    recovery_rate: float = Field(ge=0, le=1)
    average_attempts: float = Field(ge=0)
    p50_latency_ms: float = Field(ge=0)
    p95_latency_ms: float = Field(ge=0)
    total_estimated_tokens: int = Field(ge=0)
    total_estimated_cost: float = Field(ge=0)
    cases: list[CaseMetrics]
