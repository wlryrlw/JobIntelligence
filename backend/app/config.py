from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent


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

    @property
    def session_archive_file(self) -> Path:
        path = Path(self.session_archive_path)
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
        }


settings = Settings()
