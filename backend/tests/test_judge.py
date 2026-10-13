from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from eval.judge import JudgeConfig
from eval.judge_models import JudgeVerdict
from eval.judge_runner import run_judge


class FakeJudge:
    def __init__(self, score: int = 2) -> None:
        self.score = score
        self.payloads: list[dict[str, object]] = []

    async def evaluate(self, system_prompt: str, payload: dict[str, object]) -> JudgeVerdict:
        self.payloads.append(payload)
        assert "候选 SQL" in system_prompt
        return JudgeVerdict(
            score=self.score,
            label="correct" if self.score == 2 else "partially_correct",
            error_type="none",
            reason="测试 Judge 接口返回结构化结果。",
        )


def test_reference_judge_uses_expected_sql_and_one_final_answer() -> None:
    fake = FakeJudge()
    result = asyncio.run(
        run_judge(
            "seed",
            mode="reference",
            prompt="请评测最终候选 SQL。",
            config=JudgeConfig(
                api_key="test-key",
                base_url="https://judge.test/v1",
                model="judge-model",
            ),
            evaluator=fake,
        )
    )

    assert result.mode == "reference"
    assert result.dataset_size == 6
    assert len(fake.payloads) == 6
    assert all("reference_answer" in payload for payload in fake.payloads)
    assert all("answer_field" not in payload for payload in fake.payloads)
    assert fake.payloads[0]["reference_answer"] == (
        "SELECT COUNT(job_id) AS job_count FROM jobs"
    )
    assert result.summaries[0].requested_count == 6
    assert result.summaries[0].passed_count == 6
    assert result.summaries[0].score_2_count == 6
    assert result.summaries[0].pass_rate == 1


def test_judge_only_evaluates_repair_sql_as_final_answer() -> None:
    fake = FakeJudge(score=1)
    result = asyncio.run(
        run_judge(
            "eval_test",
            mode="single",
            prompt="请独立评测最终候选 SQL。",
            config=JudgeConfig(
                api_key="test-key",
                base_url="https://judge.test/v1",
                model="judge-model",
            ),
            evaluator=fake,
        )
    )

    assert len(result.cases) == result.dataset_size == 35
    assert len(fake.payloads) == 35
    assert result.summaries[0].completed_count == 35
    assert result.summaries[0].failed_count == 0
    assert result.summaries[0].score_1_count == 35
    assert result.summaries[0].passed_count == 0
    assert all("reference_answer" not in payload for payload in fake.payloads)

    negotiable = next(
        payload
        for payload in fake.payloads
        if str(payload["case_id"]).startswith("test-negotiable-")
    )
    assert negotiable["final_answer"] == (
        "SELECT COUNT(job_id) AS job_count FROM jobs WHERE is_negotiable = true"
    )


def test_judge_score_only_accepts_zero_one_or_two() -> None:
    for score in (0, 1, 2):
        verdict = JudgeVerdict(
            score=score,
            label="incorrect" if score == 0 else "partially_correct",
            error_type="other",
            reason="合法分数。",
        )
        assert verdict.score == score

    with pytest.raises(ValidationError):
        JudgeVerdict(
            score=3,
            label="correct",
            error_type="none",
            reason="非法分数。",
        )
