from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from ..config import BASE_DIR, Settings, settings
from ..contracts import TargetJobRequest
from ..etl.models import Job, JobDetail
from ..etl.pipeline import read_rows
from .clients import BailianEmbeddingClient, BailianRerankClient, RAGApiError
from .models import JdDocument, RetrievedJd


logger = logging.getLogger(__name__)


class RagError(RuntimeError):
    """JD RAG 无法完成索引或检索。"""


class RagNotConfiguredError(RagError):
    """用户选择了 RAG，但必要配置尚未完成。"""


class JdRagService:
    """岗位 JD RAG 边界：快照读取、Milvus 建索引、BM25+dense+RRF+Rerank。"""

    OUTPUT_FIELDS = [
        "chunk_id", "job_id", "text", "text_hash", "title", "company", "city",
        "experience", "dataset_version", "source_url", "chunk_index",
    ]

    def __init__(
        self,
        config: Settings | None = None,
        database_root: Path | None = None,
    ) -> None:
        self.config = config or settings
        self.database_root = database_root or BASE_DIR.parent / "data" / "databases"
        self.embedding = BailianEmbeddingClient(self.config)
        self.rerank = BailianRerankClient(self.config)
        self._client: Any | None = None

    def retrieve(
        self,
        query: str,
        target: TargetJobRequest,
        *,
        dataset_version: str | None = None,
    ) -> list[RetrievedJd]:
        self._validate_config()
        version, _ = self.ensure_index()
        requested_version = dataset_version or version
        client = self._milvus_client()
        filter_expr = self._filter_expression(target, requested_version)
        try:
            dense_vector = self.embedding.embed([query])[0]
            dense_hits = client.search(
                collection_name=self.config.milvus_collection,
                data=[dense_vector],
                anns_field="dense_vector",
                limit=max(1, self.config.rag_dense_top_k),
                filter=filter_expr,
                output_fields=self.OUTPUT_FIELDS,
                search_params={"metric_type": "COSINE", "params": {}},
            )
            bm25_hits = client.search(
                collection_name=self.config.milvus_collection,
                data=[query],
                anns_field="sparse_vector",
                limit=max(1, self.config.rag_bm25_top_k),
                filter=filter_expr,
                output_fields=self.OUTPUT_FIELDS,
                search_params={"metric_type": "BM25", "params": {}},
            )
        except Exception as exc:
            raise RagError(f"Milvus JD 检索失败：{type(exc).__name__}: {exc}") from exc

        dense = self._flatten_hits(dense_hits)
        sparse = self._flatten_hits(bm25_hits)
        merged = self._rrf_merge(dense, sparse)
        candidates = merged[: max(self.config.rag_rerank_top_k * 8, 20)]
        if not candidates:
            return []
        if self.config.rag_rerank_enabled:
            try:
                ranked = self.rerank.rerank(
                    query,
                    [item["text"] for item in candidates],
                    max(1, self.config.rag_rerank_top_k),
                )
            except RAGApiError as exc:
                raise RagError(f"JD Rerank 失败：{exc}") from exc
            result: list[RetrievedJd] = []
            for score in ranked:
                if score.index >= len(candidates):
                    raise RagError("JD Rerank 返回了越界的候选文档索引")
                result.append(
                    RetrievedJd.model_validate(
                        {**candidates[score.index], "rerank_score": score.relevance_score}
                    )
                )
            return result
        return [RetrievedJd.model_validate(item) for item in candidates[: self.config.rag_rerank_top_k]]

    def ensure_index(self) -> tuple[str, int]:
        self._validate_config()
        documents = self._load_current_documents()
        if not documents:
            raise RagError("当前已发布岗位数据中没有有效 JD，无法建立 RAG 索引")
        version = documents[0].dataset_version
        if any(item.dataset_version != version for item in documents):
            raise RagError("当前岗位快照包含多个 dataset_version，拒绝建立不一致的 RAG 索引")
        client = self._milvus_client()
        self._ensure_collection(client)
        state = self._read_state()
        if (
            state.get("dataset_version") == version
            and state.get("document_count") == len(documents)
            and state.get("embedding_model") == self.config.rag_embedding_model
            and state.get("embedding_dimension") == self.config.rag_embedding_dimension
        ):
            return version, len(documents)

        batch_size = max(1, self.config.rag_embedding_batch_size)
        for start in range(0, len(documents), batch_size):
            batch = documents[start : start + batch_size]
            vectors = self.embedding.embed([item.text for item in batch])
            rows = [
                {**item.model_dump(mode="json"), "dense_vector": vector}
                for item, vector in zip(batch, vectors, strict=True)
            ]
            try:
                client.upsert(collection_name=self.config.milvus_collection, data=rows)
            except Exception as exc:
                raise RagError(f"Milvus JD 索引写入失败：{type(exc).__name__}: {exc}") from exc
        self._write_state(
            {
                "dataset_version": version,
                "document_count": len(documents),
                "embedding_model": self.config.rag_embedding_model,
                "embedding_dimension": self.config.rag_embedding_dimension,
            }
        )
        return version, len(documents)

    def _validate_config(self) -> None:
        missing: list[str] = []
        if not self.config.rag_enabled:
            missing.append("RAG_ENABLED=true")
        if not self.config.rag_api_key:
            missing.append("RAG_API_KEY")
        if not self.config.rag_embedding_model:
            missing.append("RAG_EMBEDDING_MODEL")
        if self.config.rag_embedding_dimension <= 0:
            missing.append("RAG_EMBEDDING_DIMENSION>0")
        if missing:
            raise RagNotConfiguredError("岗位 JD RAG 尚未配置：" + "、".join(missing))
        if self.config.rag_rerank_enabled and not self.config.rag_rerank_model:
            raise RagNotConfiguredError("RAG_RERANK_ENABLED=true 时必须填写 RAG_RERANK_MODEL")

    def _milvus_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            from pymilvus import MilvusClient
        except ImportError as exc:
            raise RagNotConfiguredError(
                "未安装 pymilvus，请先执行 uv sync；如果使用本地 Milvus，还需要启动 Milvus 服务。"
            ) from exc
        try:
            kwargs = {"uri": self.config.milvus_uri}
            if self.config.milvus_token:
                kwargs["token"] = self.config.milvus_token
            self._client = MilvusClient(**kwargs)
            return self._client
        except Exception as exc:
            raise RagError(f"无法连接 Milvus（{self.config.milvus_uri}）：{type(exc).__name__}: {exc}") from exc

    def _ensure_collection(self, client: Any) -> None:
        try:
            if client.has_collection(collection_name=self.config.milvus_collection):
                return
            from pymilvus import DataType, Function, FunctionType

            schema = client.create_schema(auto_id=False, enable_dynamic_field=False)
            schema.add_field(
                field_name="chunk_id", datatype=DataType.VARCHAR, is_primary=True, max_length=128
            )
            schema.add_field(
                field_name="text",
                datatype=DataType.VARCHAR,
                max_length=65535,
                enable_analyzer=True,
                analyzer_params={"type": "chinese"},
            )
            schema.add_field(field_name="text_hash", datatype=DataType.VARCHAR, max_length=64)
            schema.add_field(field_name="job_id", datatype=DataType.VARCHAR, max_length=200)
            schema.add_field(field_name="title", datatype=DataType.VARCHAR, max_length=300)
            schema.add_field(field_name="company", datatype=DataType.VARCHAR, max_length=300)
            schema.add_field(field_name="city", datatype=DataType.VARCHAR, max_length=100)
            schema.add_field(field_name="experience", datatype=DataType.VARCHAR, max_length=100)
            schema.add_field(field_name="dataset_version", datatype=DataType.VARCHAR, max_length=200)
            schema.add_field(field_name="source_url", datatype=DataType.VARCHAR, max_length=2048)
            schema.add_field(field_name="chunk_index", datatype=DataType.INT64)
            schema.add_field(
                field_name="dense_vector",
                datatype=DataType.FLOAT_VECTOR,
                dim=self.config.rag_embedding_dimension,
            )
            schema.add_field(field_name="sparse_vector", datatype=DataType.SPARSE_FLOAT_VECTOR)
            schema.add_function(
                Function(
                    name="jd_bm25",
                    input_field_names=["text"],
                    output_field_names=["sparse_vector"],
                    function_type=FunctionType.BM25,
                )
            )
            index_params = client.prepare_index_params()
            index_params.add_index(
                field_name="dense_vector", index_type="AUTOINDEX", metric_type="COSINE"
            )
            index_params.add_index(
                field_name="sparse_vector",
                index_type="SPARSE_INVERTED_INDEX",
                metric_type="BM25",
                params={
                    "inverted_index_algo": "DAAT_MAXSCORE",
                    "bm25_k1": 1.2,
                    "bm25_b": 0.75,
                },
            )
            client.create_collection(
                collection_name=self.config.milvus_collection,
                schema=schema,
                index_params=index_params,
            )
        except Exception as exc:
            raise RagError(f"Milvus JD 集合初始化失败：{type(exc).__name__}: {exc}") from exc

    def _load_current_documents(self) -> list[JdDocument]:
        root = self.database_root.resolve()
        folder = root if root.name == "jobs_market" else root / "jobs_market"
        jobs_path = folder / "jobs.csv"
        details_path = folder / "job_details.csv"
        if not jobs_path.exists() or not details_path.exists():
            raise RagError(f"当前已发布岗位数据不存在：{folder}")
        jobs = read_rows(jobs_path, Job)
        details = {item.job_id: item for item in read_rows(details_path, JobDetail)}
        documents: list[JdDocument] = []
        for job in jobs:
            detail = details.get(job.job_id)
            if detail is None or not detail.jd_text.strip():
                continue
            source_text = "\n".join(
                [
                    f"岗位：{job.title}",
                    f"公司：{job.company}",
                    f"城市：{job.city}",
                    f"经验：{job.experience}",
                    f"岗位关键词：{job.keyword}",
                    f"技能标签：{'、'.join(job.skills)}",
                    f"职位描述：{detail.jd_text.strip()}",
                ]
            )
            for index, chunk in enumerate(_chunk_text(source_text)):
                digest = hashlib.sha256(
                    f"{job.job_id}:{detail.jd_hash}:{index}".encode("utf-8")
                ).hexdigest()
                documents.append(
                    JdDocument(
                        chunk_id=digest,
                        job_id=job.job_id,
                        text=chunk,
                        text_hash=detail.jd_hash,
                        title=job.title,
                        company=job.company,
                        city=job.city,
                        experience=job.experience,
                        dataset_version=job.dataset_version,
                        source_url=job.job_link,
                        chunk_index=index,
                    )
                )
        return documents

    @staticmethod
    def _flatten_hits(raw: Any) -> list[dict[str, Any]]:
        rows = raw[0] if isinstance(raw, list) and raw else raw
        if rows is None:
            return []
        result: list[dict[str, Any]] = []
        for hit in rows:
            if not isinstance(hit, dict):
                raise RagError("Milvus 检索返回了无法解析的 hit")
            entity = hit.get("entity") if isinstance(hit.get("entity"), dict) else hit
            chunk_id = str(hit.get("id") or entity.get("chunk_id") or "")
            if not chunk_id:
                raise RagError("Milvus hit 缺少 chunk_id")
            result.append(
                {
                    "chunk_id": chunk_id,
                    "job_id": str(entity.get("job_id") or ""),
                    "text": str(entity.get("text") or ""),
                    "title": str(entity.get("title") or ""),
                    "company": str(entity.get("company") or ""),
                    "city": str(entity.get("city") or ""),
                    "experience": str(entity.get("experience") or ""),
                    "dataset_version": str(entity.get("dataset_version") or ""),
                    "source_url": str(entity.get("source_url") or ""),
                    "rrf_score": 0.0,
                }
            )
        return result

    def _rrf_merge(self, dense: list[dict[str, Any]], sparse: list[dict[str, Any]]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        for rank, item in enumerate(dense, start=1):
            merged.setdefault(item["chunk_id"], item)["rrf_score"] += 1 / (self.config.rag_rrf_k + rank)
        for rank, item in enumerate(sparse, start=1):
            current = merged.setdefault(item["chunk_id"], item)
            current["rrf_score"] += 1 / (self.config.rag_rrf_k + rank)
        return sorted(merged.values(), key=lambda item: item["rrf_score"], reverse=True)

    @staticmethod
    def _filter_expression(target: TargetJobRequest, dataset_version: str) -> str:
        parts = [f'dataset_version == "{_escape_filter_value(dataset_version)}"']
        if target.city:
            parts.append(f'city == "{_escape_filter_value(target.city)}"')
        if target.experience:
            parts.append(f'experience == "{_escape_filter_value(target.experience)}"')
        return " and ".join(parts)

    def _read_state(self) -> dict[str, Any]:
        path = self.config.rag_index_state_file
        if not path.exists():
            return {}
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("rag_index_state_unreadable path=%s error_type=%s", path, type(exc).__name__)
            return {}
        return value if isinstance(value, dict) else {}

    def _write_state(self, value: dict[str, Any]) -> None:
        path = self.config.rag_index_state_file
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
        except OSError as exc:
            raise RagError(f"无法写入 RAG 索引状态文件：{path}") from exc


def _chunk_text(text: str, *, size: int = 1600, overlap: int = 240) -> list[str]:
    normalized = "\n".join(line.strip() for line in text.splitlines() if line.strip()).strip()
    if len(normalized) <= size:
        return [normalized]
    chunks: list[str] = []
    start = 0
    while start < len(normalized):
        end = min(len(normalized), start + size)
        chunks.append(normalized[start:end])
        if end == len(normalized):
            break
        start = max(start + 1, end - overlap)
    return chunks


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


__all__ = ["JdRagService", "RagError", "RagNotConfiguredError"]
