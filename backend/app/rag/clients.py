from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any

from pydantic import BaseModel, Field

from ..config import Settings, settings


class RAGApiError(RuntimeError):
    """百炼 Embedding 或 Rerank API 调用失败。"""


class EmbeddingItem(BaseModel):
    index: int = Field(ge=0)
    embedding: list[float] = Field(min_length=1)


class EmbeddingResponse(BaseModel):
    data: list[EmbeddingItem] = Field(min_length=1)


class RerankScore(BaseModel):
    index: int = Field(ge=0)
    relevance_score: float


class BailianEmbeddingClient:
    """使用百炼 OpenAI-compatible Embeddings API，不依赖 OpenAI SDK。"""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._blocked_until = 0.0

    @property
    def enabled(self) -> bool:
        return bool(self.config.rag_api_key and self.config.rag_embedding_model)

    def embed(self, texts: list[str]) -> list[list[float]]:
        values = [text.strip() for text in texts]
        if not values or any(not value for value in values):
            raise ValueError("Embedding 输入不能为空")
        if not self.enabled:
            raise RAGApiError("RAG_API_KEY 或 RAG_EMBEDDING_MODEL 尚未配置")
        payload: dict[str, Any] = {
            "model": self.config.rag_embedding_model,
            "input": values,
            "dimensions": self.config.rag_embedding_dimension,
            "encoding_format": "float",
        }
        result = self._post(
            f"{self.config.rag_embedding_base_url}/embeddings", payload
        )
        try:
            parsed = EmbeddingResponse.model_validate(result)
            ordered = sorted(parsed.data, key=lambda item: item.index)
            if [item.index for item in ordered] != list(range(len(values))):
                raise RAGApiError("百炼 Embedding 响应 index 不连续，无法安全对齐输入")
            vectors = [item.embedding for item in ordered]
        except (TypeError, ValueError) as exc:
            raise RAGApiError("百炼 Embedding 响应结构不符合预期") from exc
        if len(vectors) != len(values):
            raise RAGApiError("百炼 Embedding 返回数量与输入数量不一致")
        return vectors

    async def aembed(self, texts: list[str]) -> list[list[float]]:
        return await asyncio.to_thread(self.embed, texts)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if time.monotonic() < self._blocked_until:
            raise RAGApiError("百炼 RAG 接口处于短时熔断窗口，请稍后重试")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.rag_api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(max(0, self.config.max_retries) + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    raw = json.loads(response.read().decode("utf-8"))
                if not isinstance(raw, dict):
                    raise RAGApiError("百炼 API 返回值不是 JSON 对象")
                return raw
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = RAGApiError(f"百炼 Embedding 返回 HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, RAGApiError) as exc:
                last_error = exc
            if attempt < max(0, self.config.max_retries):
                time.sleep(0.4 * (2**attempt))
        self._blocked_until = time.monotonic() + 30
        raise RAGApiError(f"百炼 Embedding 调用失败: {last_error}") from last_error


class BailianRerankClient:
    """使用百炼 qwen3-rerank 的 OpenAI-compatible /reranks 接口。"""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._embedding_client = BailianEmbeddingClient(config)

    @property
    def enabled(self) -> bool:
        return bool(
            self.config.rag_rerank_enabled
            and self.config.rag_api_key
            and self.config.rag_rerank_model
        )

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[RerankScore]:
        if not documents:
            return []
        if not self.enabled:
            raise RAGApiError("RAG_RERANK_ENABLED=true 时必须配置 RAG_API_KEY 和 RAG_RERANK_MODEL")
        payload = {
            "model": self.config.rag_rerank_model,
            "query": query,
            "documents": documents,
            "top_n": max(1, min(top_n, len(documents))),
            "instruct": "Given a job-requirement query, rank JD passages by their usefulness for answering it.",
        }
        result = self._post(f"{self.config.rag_rerank_base_url}/reranks", payload)
        raw_items = result.get("results") or result.get("data")
        if not isinstance(raw_items, list):
            raise RAGApiError("百炼 Rerank 响应缺少 results 数组")
        try:
            return sorted(
                [RerankScore.model_validate(item) for item in raw_items],
                key=lambda item: item.relevance_score,
                reverse=True,
            )[:top_n]
        except (TypeError, ValueError) as exc:
            raise RAGApiError("百炼 Rerank 响应结构不符合预期") from exc

    async def arerank(self, query: str, documents: list[str], top_n: int) -> list[RerankScore]:
        return await asyncio.to_thread(self.rerank, query, documents, top_n)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        return self._embedding_client._post(url, payload)


__all__ = [
    "BailianEmbeddingClient",
    "BailianRerankClient",
    "RAGApiError",
    "RerankScore",
]
