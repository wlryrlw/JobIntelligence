from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from eval.judge import JudgeConfig
from eval.judge_models import JudgeVerdict
from eval.judge_runner import _metrics_for_console, run_judge


class FakeJudge:
    def __init__(self, score: int = 2) -> None:
        self.score = score
        self.payloads: list[dict[str, object]] = []

    async def evaluate(self, system_prompt: str, payload: dict[str, object]) -> JudgeVerdict:
        self.payloads.append(payload)
        assert "SQL" in system_prompt
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

    assert len(result.cases) == result.dataset_size == 100
    assert len(fake.payloads) == 100
    assert result.summaries[0].completed_count == 100
    assert result.summaries[0].failed_count == 0
    assert result.summaries[0].score_1_count == 100
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


def test_judge_reports_progress_without_changing_result() -> None:
    fake = FakeJudge()
    progress: list[str] = []
    result = asyncio.run(
        run_judge(
            "seed",
            mode="single",
            prompt="请评测最终 SQL。",
            config=JudgeConfig(
                api_key="test-key",
                base_url="https://judge.test/v1",
                model="judge-model",
            ),
            evaluator=fake,
            progress=progress.append,
        )
    )

    assert result.summaries[0].completed_count == 6
    assert progress[0] == "开始 Judge 评测：dataset=seed，mode=single，共 6 个 case。"
    assert "[1/6] 正在调用 Judge：" in progress[1]
    assert "score=2" in progress[2]
    assert progress[-1] == "Judge 评测完成：6/6 个 case 已处理。"


def test_console_metrics_omit_case_details() -> None:
    fake = FakeJudge()
    result = asyncio.run(
        run_judge(
            "seed",
            mode="single",
            prompt="请评测最终 SQL。",
            config=JudgeConfig(
                api_key="test-key",
                base_url="https://judge.test/v1",
                model="judge-model",
            ),
            evaluator=fake,
        )
    )

    metrics = _metrics_for_console(result)
    assert "cases（case明细）" not in metrics
    assert metrics["dataset（数据集）"] == "seed"
    assert metrics["summaries（汇总指标）"][0]["score_2_count（score=2的case数）"] == 6
