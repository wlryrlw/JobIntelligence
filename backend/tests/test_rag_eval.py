from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from app.rag.models import RetrievedJd
from eval.rag.dataset import load_golden_set, load_job_rows
from eval.rag.judge_runner import run_judge
from eval.rag.metrics import score_case, summarize
from eval.rag.models import RagJudgeVerdict
from eval.judge import JudgeConfig


REPO_ROOT = Path(__file__).resolve().parents[2]
GOLDEN_SET = REPO_ROOT / "eval" / "rag" / "golden_set.jsonl"


def test_golden_set_matches_current_job_snapshot() -> None:
    dataset = load_golden_set(GOLDEN_SET)
    rows = load_job_rows(REPO_ROOT / "data" / "databases")
    versions = {row["dataset_version"] for row in rows}
    assert versions == {"ds_8eeedf2a6a5cf4500a2bd39e"}
    for case in dataset.cases:
        assert case.source_dataset_version in versions
        assert all(
            any(row["job_id"] == job_id for row in rows)
            for job_id in case.relevant_job_ids
        )
    # 该断言确保“北京”是受控 no-data case，而不是误把标题中的北京当作城市样本。
    beijing = next(case for case in dataset.cases if case.case_id == "beijing_missing_city")
    assert beijing.answerable is False
    assert beijing.relevant_job_ids == []


def test_retrieval_metrics_deduplicate_chunks_and_score_no_data() -> None:
    case = load_golden_set(GOLDEN_SET).cases[0]
    hits = [
        RetrievedJd(
            chunk_id="chunk-one",
            job_id=case.relevant_job_ids[0],
            text="evidence",
            city="上海",
            experience="3-5年",
            dataset_version=case.source_dataset_version,
        ),
        RetrievedJd(
            chunk_id="chunk-two",
            job_id=case.relevant_job_ids[0],
            text="same job second chunk",
            city="上海",
            experience="3-5年",
            dataset_version=case.source_dataset_version,
        ),
        RetrievedJd(
            chunk_id="chunk-three",
            job_id=case.relevant_job_ids[1],
            text="evidence",
            city="上海",
            experience="3-5年",
            dataset_version=case.source_dataset_version,
        ),
    ]
    metric = score_case(case, hits, "A")
    assert metric.ranked_job_ids == case.relevant_job_ids[:2]
    assert metric.recall_at_5 == pytest.approx(2 / len(case.relevant_job_ids))
    assert metric.filter_violation is False

    no_data = next(
        item for item in load_golden_set(GOLDEN_SET).cases if item.case_id == "beijing_missing_city"
    )
    no_data_metric = score_case(no_data, [], "A")
    assert no_data_metric.no_data_correct is True
    assert summarize([no_data_metric], "A").no_data_accuracy == 1


class FakeRagJudge:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    async def evaluate_json(
        self,
        system_prompt: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        assert "faithfulness_score" in system_prompt
        self.payloads.append(payload)
        return RagJudgeVerdict(
            faithfulness_score=2,
            relevance_score=1,
            unsupported_claims=[],
            missing_aspects=["一个方面"],
            reason="测试结构化输出。",
        ).model_dump(mode="json")


def test_rag_judge_reuses_independent_judge_contract(tmp_path: Path) -> None:
    answers = tmp_path / "answers.jsonl"
    answers.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "query": "上海 Agent 岗位要求什么？",
                "answer": "要求具备 Agent 开发能力。",
                "evidence": [
                    {
                        "chunk_id": "chunk-1",
                        "job_id": "job-1",
                        "snippet": "熟悉 Agent 开发。",
                    }
                ],
                "required_aspects": ["Agent"],
                "answerable": True,
            },
        ),
        encoding="utf-8",
    )
    fake = FakeRagJudge()
    result = asyncio.run(
        run_judge(
            answers,
            prompt="你是 RAG 评测器。faithfulness_score relevance_score",
            config=JudgeConfig(
                api_key="test-key",
                base_url="https://judge.test/v1",
                model="qwen-judge",
            ),
            evaluator=fake,
        )
    )
    assert len(fake.payloads) == 1
    assert result.summaries[0].average_faithfulness == 2
    assert result.summaries[0].average_relevance == 1
    assert result.summaries[0].joint_pass_rate == 0
