from __future__ import annotations

import json
import os
from dataclasses import dataclass

from app.config import Settings
from app.model_client import ModelClient

from .judge_models import JudgeVerdict


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default))
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是数字，当前值为：{raw!r}") from exc


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default))
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数，当前值为：{raw!r}") from exc


@dataclass(frozen=True)
class JudgeConfig:
    """独立于主问答模型的 Judge API 配置。"""

    api_key: str
    base_url: str
    model: str
    timeout: float = 60.0
    temperature: float = 0.0
    max_retries: int = 1

    @classmethod
    def from_env(
        cls,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
    ) -> "JudgeConfig":
        resolved_key = api_key or os.getenv("JUDGE_API_KEY", "")
        resolved_url = base_url or os.getenv("JUDGE_BASE_URL", "")
        resolved_model = model or os.getenv("JUDGE_MODEL", "")
        missing = [
            name
            for name, value in (
                ("JUDGE_API_KEY", resolved_key),
                ("JUDGE_BASE_URL", resolved_url),
                ("JUDGE_MODEL", resolved_model),
            )
            if not value.strip()
        ]
        if missing:
            raise ValueError(f"Judge 配置缺少：{', '.join(missing)}")
        if resolved_url.rstrip("/").endswith("/chat/completions"):
            resolved_url = resolved_url.rsplit("/chat/completions", 1)[0]
        if not 0 <= _env_float("JUDGE_TEMPERATURE", 0.0) <= 2:
            raise ValueError("JUDGE_TEMPERATURE 必须在 0 到 2 之间")
        max_retries = _env_int("JUDGE_MAX_RETRIES", 1)
        if max_retries < 0:
            raise ValueError("JUDGE_MAX_RETRIES 不能小于 0")
        return cls(
            api_key=resolved_key,
            base_url=resolved_url.rstrip("/"),
            model=resolved_model,
            timeout=max(0.1, _env_float("JUDGE_TIMEOUT", 60.0)),
            temperature=_env_float("JUDGE_TEMPERATURE", 0.0),
            max_retries=max_retries,
        )


class JudgeClient:
    """使用独立模型配置调用 OpenAI-compatible Judge API。"""

    def __init__(self, config: JudgeConfig) -> None:
        settings = Settings(
            api_key=config.api_key,
            llm_base_url=config.base_url,
            llm_model=config.model,
            timeout=int(config.timeout),
            temperature=config.temperature,
            max_retries=config.max_retries,
        )
        self.config = config
        self._client = ModelClient(settings)

    async def evaluate_json(
        self,
        system_prompt: str,
        payload: dict[str, object],
    ) -> dict[str, object]:
        """调用独立 Judge 模型并返回已确认是对象的 JSON。

        SQL Judge 继续通过 ``evaluate`` 做 ``JudgeVerdict`` 校验；RAG
        Judge 使用同一套 Qwen API 配置，但有自己的 Pydantic 输出契约。
        """

        raw = await self._client.achat_json(
            system_prompt,
            json.dumps(payload, ensure_ascii=False),
        )
        if not isinstance(raw, dict):
            raise TypeError("Judge API 返回的 JSON 不是对象")
        return {str(key): value for key, value in raw.items()}

    async def evaluate(self, system_prompt: str, payload: dict[str, object]) -> JudgeVerdict:
        raw = await self.evaluate_json(system_prompt, payload)
        return JudgeVerdict.model_validate(raw)
