from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings, settings
from .prompt_context import with_product_context
from .query_runtime import QueryTimeoutError, current_query_runtime


class ModelClient:
    """OfferAgent 主模型客户端。"""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._blocked_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.api_key)

    def chat(self, system: str, user: str, *, temperature: float | None = None) -> str:
        system = with_product_context(system)
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "stream": False,
        }
        result = self._post(self.config.chat_url, payload)
        try:
            return str(result["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("大模型响应缺少 message.content") from exc

    async def achat(self, system: str, user: str, *, temperature: float | None = None) -> str:
        """异步边界；urllib 保留在线程中，避免阻塞 FastAPI/SSE 事件循环。"""

        import asyncio

        return await asyncio.to_thread(self.chat, system, user, temperature=temperature)

    async def achat_json(self, system: str, user: str) -> dict[str, Any]:
        import asyncio

        return await asyncio.to_thread(self.chat_json, system, user)

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        raw = self.chat(system, user)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                raise RuntimeError("大模型未返回有效 JSON")
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise RuntimeError("大模型未返回有效 JSON") from exc

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        key = self.config.api_key
        if not key:
            raise RuntimeError("LLM_API_KEY 尚未配置")
        if time.monotonic() < self._blocked_until.get(url, 0):
            raise RuntimeError("模型接口暂时处于30秒熔断窗口，请稍后重试")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        runtime = current_query_runtime()
        max_timeout = max(0.1, float(self.config.timeout))
        max_retries = max(0, self.config.max_retries)
        if runtime is not None:
            runtime.check("llm_call")
            max_timeout = min(max_timeout, max(0.1, self.config.query_llm_timeout_seconds))
            max_retries = min(max_retries, max(0, self.config.query_max_retries))

        last_error: Exception | None = None
        timed_out = False
        for attempt in range(max_retries + 1):
            request_timeout = max_timeout
            if runtime is not None:
                remaining = runtime.remaining_seconds
                if remaining <= 0:
                    raise QueryTimeoutError(
                        runtime.current_stage,
                        "模型调用前已耗尽问答时间预算",
                        elapsed_ms=runtime.elapsed_ms,
                        timeout_seconds=runtime.timeout_seconds,
                    )
                request_timeout = min(request_timeout, remaining)
            try:
                with urllib.request.urlopen(request, timeout=request_timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = RuntimeError(f"模型接口返回 HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                timed_out = isinstance(exc, TimeoutError) or (
                    isinstance(exc, urllib.error.URLError)
                    and "timed out" in str(exc).lower()
                )
            if runtime is not None and runtime.remaining_seconds <= 0:
                raise QueryTimeoutError(
                    runtime.current_stage,
                    "模型调用耗尽问答时间预算",
                    elapsed_ms=runtime.elapsed_ms,
                    timeout_seconds=runtime.timeout_seconds,
                )
            if attempt < max_retries:
                backoff = 0.4 * (2**attempt)
                if runtime is not None and runtime.remaining_seconds <= backoff:
                    break
                time.sleep(backoff)
        if runtime is not None and (runtime.remaining_seconds <= 0 or timed_out):
            raise QueryTimeoutError(
                runtime.current_stage,
                "模型调用超时，已停止重试",
                elapsed_ms=runtime.elapsed_ms,
                timeout_seconds=runtime.timeout_seconds,
            )
        # 网络请求失败后对当前接口启用短时熔断。
        self._blocked_until[url] = time.monotonic() + 30
        raise RuntimeError(f"模型接口调用失败: {last_error}") from last_error
