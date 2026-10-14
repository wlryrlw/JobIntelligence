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


def test_jd_frequency_question_automatically_uses_job_requirement_route() -> None:
    result = RequestPreprocessor(NoCallModel()).prepare(
        "上海agent开发的jd，提到最多的是什么",
        "",
        use_jd_rag=False,
    )

    assert result.action == "career_advice"
    assert result.career_mode == "job_requirement"
    assert result.source == "jd_requirement_rule"
    assert result.target is not None
    assert result.target.keyword == "agent开发"
    assert result.target.city == "上海"


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


def test_retrieval_variants_isolate_bm25_and_embedding(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeEmbedding:
        def __init__(self) -> None:
            self.calls = 0

        def embed(self, texts: list[str]) -> list[list[float]]:
            self.calls += 1
            return [[0.1, 0.2] for _ in texts]

    class FakeClient:
        def __init__(self) -> None:
            self.fields: list[str] = []

        def search(self, **kwargs: object) -> list[list[dict[str, object]]]:
            field = str(kwargs["anns_field"])
            self.fields.append(field)
            item = {
                "id": f"{field}-chunk",
                "entity": {
                    "chunk_id": f"{field}-chunk",
                    "job_id": f"{field}-job",
                    "text": f"{field} evidence",
                    "city": "上海",
                    "experience": "3-5年",
                    "dataset_version": "ds-test",
                },
            }
            return [[item]]

    config = Settings(
        rag_enabled=True,
        rag_api_key="test",
        rag_embedding_model="test",
        rag_embedding_dimension=2,
        rag_rerank_enabled=False,
        rag_rerank_top_k=2,
    )
    service = JdRagService(config)
    embedding = FakeEmbedding()
    client = FakeClient()
    monkeypatch.setattr(service, "embedding", embedding)
    monkeypatch.setattr(service, "_milvus_client", lambda: client)
    monkeypatch.setattr(service, "ensure_index", lambda: ("ds-test", 2))
    target = TargetJobRequest(keyword="Agent", city="上海", experience="3-5年")

    sparse = service.retrieve_variant("RAG", target, variant="bm25", top_k=1)
    assert [item.job_id for item in sparse] == ["sparse_vector-job"]
    assert embedding.calls == 0
    assert client.fields == ["sparse_vector"]

    dense = service.retrieve_variant("RAG", target, variant="embedding", top_k=1)
    assert [item.job_id for item in dense] == ["dense_vector-job"]
    assert embedding.calls == 1
    assert client.fields[-1] == "dense_vector"
