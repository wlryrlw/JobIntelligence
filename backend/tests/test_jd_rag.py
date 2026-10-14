from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings
from app.contracts import TargetJobRequest
from app.preprocessing import RequestPreprocessor
from app.rag.service import JdRagService, RagNotConfiguredError


class NoCallModel:
    def chat_json(self, *_: object, **__: object) -> dict[str, object]:
        raise AssertionError("显式岗位要求 RAG 路由不应依赖模型分类")


def test_explicit_jd_rag_forces_job_requirement_career_route() -> None:
    result = RequestPreprocessor(NoCallModel()).prepare(
        "上海 3-5 年 Agent 岗位通常如何要求 RAG？",
        "",
        use_jd_rag=True,
    )

    assert result.action == "career_advice"
    assert result.career_mode == "job_requirement"
    assert result.source == "user_jd_rag_toggle"
    assert result.target is not None
    assert result.target.keyword == "Agent"
    assert result.target.city == "上海"
    assert result.target.experience == "3-5年"


def test_jd_rag_fails_explicitly_when_not_configured(tmp_path: Path) -> None:
    config = Settings(
        rag_enabled=False,
        rag_api_key="",
        rag_embedding_model="",
        rag_index_state_path=str(tmp_path / "index_state.json"),
    )
    service = JdRagService(config, database_root=tmp_path / "databases")

    with pytest.raises(RagNotConfiguredError, match="RAG_ENABLED=true"):
        service.retrieve(
            "Agent 岗位如何要求 RAG",
            TargetJobRequest(keyword="Agent", city="上海"),
        )


def test_rrf_merge_is_rank_based() -> None:
    service = JdRagService(
        Settings(
            rag_enabled=True,
            rag_api_key="test",
            rag_embedding_model="test",
            rag_rrf_k=60,
        )
    )
    dense = [
        {"chunk_id": "a", "text": "A", "rrf_score": 0.0},
        {"chunk_id": "b", "text": "B", "rrf_score": 0.0},
    ]
    sparse = [
        {"chunk_id": "b", "text": "B", "rrf_score": 0.0},
        {"chunk_id": "a", "text": "A", "rrf_score": 0.0},
    ]

    merged = service._rrf_merge(dense, sparse)

    assert {item["chunk_id"] for item in merged} == {"a", "b"}
    assert merged[0]["rrf_score"] == merged[1]["rrf_score"]
