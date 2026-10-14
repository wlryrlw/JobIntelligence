from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    return int(raw) if raw else default


def load_env(path: Path | None = None) -> None:
    """加载项目内 .env，不覆盖系统已经提供的变量。"""
    env_path = path or BASE_DIR / ".env"
    if not env_path.exists():
        return
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


load_env()


@dataclass(frozen=True)
class Settings:
    api_key: str = os.getenv("LLM_API_KEY", "")
    llm_base_url: str = os.getenv(
        "LLM_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"
    ).rstrip("/")
    llm_model: str = os.getenv("LLM_MODEL", "qwen-plus")
    timeout: int = int(os.getenv("LLM_TIMEOUT", "60"))
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.2"))
    max_retries: int = int(os.getenv("LLM_MAX_RETRIES", "2"))
    query_timeout_seconds: float = float(os.getenv("QUERY_TIMEOUT_SECONDS", "60"))
    query_llm_timeout_seconds: float = float(os.getenv("QUERY_LLM_TIMEOUT_SECONDS", "20"))
    query_max_retries: int = int(os.getenv("QUERY_MAX_RETRIES", "1"))
    max_saved_memories: int = int(os.getenv("MAX_SAVED_MEMORIES", "20"))
    short_term_summary_trigger_tokens: int = int(
        os.getenv("SHORT_TERM_SUMMARY_TRIGGER_TOKENS", "12000")
    )
    short_term_summary_enabled: bool = os.getenv(
        "SHORT_TERM_SUMMARY_ENABLED", "true"
    ).lower() in {"1", "true", "yes", "on"}
    short_term_summary_batch_tokens: int = int(
        os.getenv("SHORT_TERM_SUMMARY_BATCH_TOKENS", "6000")
    )
    short_term_min_recent_turns: int = int(
        os.getenv("SHORT_TERM_MIN_RECENT_TURNS", "5")
    )
    session_archive_enabled: bool = os.getenv(
        "SESSION_ARCHIVE_ENABLED", "false"
    ).lower() in {"1", "true", "yes", "on"}
    session_archive_path: str = os.getenv(
        "SESSION_ARCHIVE_PATH", str(BASE_DIR / "data" / "session_archive.db")
    )
    context_table_row_limit: int = int(os.getenv("CONTEXT_TABLE_ROW_LIMIT", "50"))
    route_context_turns: int = int(os.getenv("ROUTE_CONTEXT_TURNS", "6"))
    resume_storage_path: str = os.getenv(
        "RESUME_STORAGE_PATH", str(BASE_DIR.parent / "data" / "local" / "resumes")
    )
    resume_max_bytes: int = int(os.getenv("RESUME_MAX_BYTES", str(5 * 1024 * 1024)))
    resume_max_pages: int = int(os.getenv("RESUME_MAX_PAGES", "10"))
    resume_min_text_chars: int = int(os.getenv("RESUME_MIN_TEXT_CHARS", "10"))
    career_llm_enabled: bool = os.getenv("CAREER_LLM_ENABLED", "false").lower() in {
        "1", "true", "yes", "on"
    }
    # JD RAG 配置：默认关闭；显式开关或明确的 JD 要求问题才会调用。
    rag_enabled: bool = _env_bool("RAG_ENABLED")
    rag_api_key: str = os.getenv("RAG_API_KEY", "")
    rag_embedding_base_url: str = (os.getenv(
        "RAG_EMBEDDING_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
    ).strip() or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/")
    rag_embedding_model: str = os.getenv("RAG_EMBEDDING_MODEL", "")
    rag_embedding_dimension: int = _env_int("RAG_EMBEDDING_DIMENSION", 1024)
    rag_embedding_batch_size: int = _env_int("RAG_EMBEDDING_BATCH_SIZE", 10)
    rag_rerank_enabled: bool = _env_bool("RAG_RERANK_ENABLED")
    rag_rerank_base_url: str = (os.getenv(
        "RAG_RERANK_BASE_URL",
        "https://dashscope.aliyuncs.com/compatible-api/v1",
    ).strip() or "https://dashscope.aliyuncs.com/compatible-api/v1").rstrip("/")
    rag_rerank_model: str = os.getenv("RAG_RERANK_MODEL", "")
    milvus_uri: str = (os.getenv("MILVUS_URI", "http://localhost:19530").strip() or "http://localhost:19530").rstrip("/")
    milvus_token: str = os.getenv("MILVUS_TOKEN", "")
    milvus_collection: str = os.getenv("MILVUS_COLLECTION", "offeragent_job_jd").strip() or "offeragent_job_jd"
    rag_dense_top_k: int = _env_int("RAG_DENSE_TOP_K", 50)
    rag_bm25_top_k: int = _env_int("RAG_BM25_TOP_K", 50)
    rag_rrf_k: int = _env_int("RAG_RRF_K", 60)
    rag_rerank_top_k: int = _env_int("RAG_RERANK_TOP_K", 8)
    rag_index_state_path: str = os.getenv(
        "RAG_INDEX_STATE_PATH", str(BASE_DIR.parent / "data" / "rag" / "index_state.json")
    ).strip() or str(BASE_DIR.parent / "data" / "rag" / "index_state.json")

    @property
    def session_archive_file(self) -> Path:
        path = Path(self.session_archive_path)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def rag_index_state_file(self) -> Path:
        path = Path(self.rag_index_state_path)
        return path if path.is_absolute() else BASE_DIR / path

    @property
    def chat_url(self) -> str:
        return f"{self.llm_base_url}/chat/completions"

    def public_status(self) -> dict[str, object]:
        return {
            "api_key_configured": bool(self.api_key),
            "llm_model": self.llm_model,
            "query_timeout_seconds": self.query_timeout_seconds,
            "query_llm_timeout_seconds": self.query_llm_timeout_seconds,
            "query_max_retries": self.query_max_retries,
            "route_availability_fallback": "direct_response_no_database",
            "max_saved_memories": self.max_saved_memories,
            "short_term_memory": {
                "summary_enabled": self.short_term_summary_enabled,
                "summary_trigger_tokens": self.short_term_summary_trigger_tokens,
                "summary_batch_tokens": self.short_term_summary_batch_tokens,
                "min_recent_turns": self.short_term_min_recent_turns,
                "archive_enabled": self.session_archive_enabled,
                "table_row_limit": self.context_table_row_limit,
            },
            "route_context_turns": self.route_context_turns,
            "career_agent": {
                "llm_enabled": self.career_llm_enabled,
                "resume_max_bytes": self.resume_max_bytes,
                "resume_max_pages": self.resume_max_pages,
                "resume_min_text_chars": self.resume_min_text_chars,
            },
            "jd_rag": {
                "enabled": self.rag_enabled,
                "api_key_configured": bool(self.rag_api_key),
                "embedding_model_configured": bool(self.rag_embedding_model),
                "rerank_enabled": self.rag_rerank_enabled,
                "rerank_model_configured": bool(self.rag_rerank_model),
                "milvus_uri": self.milvus_uri,
                "collection": self.milvus_collection,
                "embedding_dimension": self.rag_embedding_dimension,
                "dense_top_k": self.rag_dense_top_k,
                "bm25_top_k": self.rag_bm25_top_k,
                "rrf_k": self.rag_rrf_k,
                "rerank_top_k": self.rag_rerank_top_k,
            },
        }


settings = Settings()
