from __future__ import annotations

import re
import unicodedata
from typing import Callable

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import EvidenceRef, SkillMention
from ..etl.normalize import ALIASES
from ..model_client import ModelClient
from ..query_runtime import QueryTimeoutError


class SkillExtractionError(ValueError):
    """技能结构化抽取失败，不能静默生成没有证据的结论。"""


class LlmSkillItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str = Field(min_length=1, max_length=100)
    canonical: str = Field(min_length=1, max_length=100)
    category: str = Field(min_length=1, max_length=50)
    evidence: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0, le=1)
    requirement: str = Field(default="must_have", pattern=r"^(must_have|preferred|unknown)$")


class LlmSkillResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: list[LlmSkillItem] = Field(default_factory=list, max_length=100)


_CATEGORY_BY_SKILL = {
    "Python": "language",
    "Java": "language",
    "JavaScript": "language",
    "TypeScript": "language",
    "C++": "language",
    "C#": "language",
    "Go": "language",
    "SQL": "data",
    "MySQL": "data",
    "PostgreSQL": "data",
    "Redis": "data",
    "RAG": "llm",
    "LLM": "llm",
    "Agent": "llm",
    "LangChain": "llm",
    "LangGraph": "llm",
    "PyTorch": "ml",
    "TensorFlow": "ml",
    "FastAPI": "backend",
    "Docker": "infra",
    "Kubernetes": "infra",
    "Linux": "infra",
    "Git": "tooling",
    "Vue": "frontend",
    "React": "frontend",
}
_EXTRA_ALIASES = {
    "retrieval augmented generation": "RAG",
    "large language model": "LLM",
    "machine learning": "Machine Learning",
    "deep learning": "Deep Learning",
    "prompt engineering": "Prompt Engineering",
    "airflow": "Airflow",
    "pandas": "Pandas",
    "numpy": "NumPy",
}


def _normalize_alias(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value)).casefold()


def _canonical_map() -> dict[str, str]:
    mapping = {_normalize_alias(alias): canonical for alias, canonical in ALIASES.items()}
    mapping.update({_normalize_alias(alias): canonical for alias, canonical in _EXTRA_ALIASES.items()})
    return mapping


class SkillCatalog:
    """技能 alias/canonical 目录和可复现 regex 抽取器。"""

    def __init__(self) -> None:
        self._aliases = _canonical_map()
        aliases = list(ALIASES) + list(_EXTRA_ALIASES)
        self._ordered_aliases = sorted(dict.fromkeys(aliases), key=len, reverse=True)

    def canonicalize(self, value: str) -> str:
        normalized = _normalize_alias(value)
        if not normalized:
            raise SkillExtractionError("技能名不能为空")
        return self._aliases.get(normalized, value.strip())

    def category(self, skill: str) -> str:
        return _CATEGORY_BY_SKILL.get(skill, "other")

    def extract_resume(self, text: str) -> list[SkillMention]:
        return self.extract_regex(
            text,
            evidence_source="resume",
            source="resume_rule",
            extractor_version="resume-regex-v1",
            requirement="unknown",
        )

    def extract_jd_regex(self, text: str) -> list[SkillMention]:
        return self.extract_regex(
            text,
            evidence_source="jd",
            source="regex",
            extractor_version="jd-regex-v1",
            requirement_resolver=self._resolve_requirement,
        )

    def extract_regex(
        self,
        text: str,
        *,
        evidence_source: str,
        source: str,
        extractor_version: str,
        requirement: str = "unknown",
        requirement_resolver: Callable[[str, int], str] | None = None,
    ) -> list[SkillMention]:
        if evidence_source not in {"resume", "jd"}:
            raise ValueError("evidence_source 必须是 resume 或 jd")
        if source not in {"regex", "resume_rule"}:
            raise ValueError("source 必须是 regex 或 resume_rule")
        result: list[SkillMention] = []
        seen: set[str] = set()
        for alias in self._ordered_aliases:
            pattern = self._pattern(alias)
            match = pattern.search(text)
            if not match:
                continue
            canonical = self.canonicalize(alias)
            if canonical in seen:
                continue
            start, end = match.span()
            resolved_requirement = (
                requirement_resolver(text, start)
                if requirement_resolver
                else requirement
            )
            result.append(
                SkillMention(
                    skill=canonical,
                    display=match.group(0),
                    category=self.category(canonical),
                    requirement=resolved_requirement,
                    evidence=EvidenceRef(
                        source=evidence_source,
                        quote=self._quote(text, start, end),
                        location=f"chars:{start}-{end}",
                    ),
                    confidence=0.98 if source == "regex" else 0.95,
                    source=source,
                    extractor_version=extractor_version,
                )
            )
            seen.add(canonical)
        return sorted(result, key=lambda item: item.evidence.location or "")

    def extract_jd(
        self,
        text: str,
        *,
        model_client: ModelClient | None = None,
        use_llm: bool = False,
    ) -> list[SkillMention]:
        baseline = self.extract_jd_regex(text)
        if not use_llm:
            return baseline
        if model_client is None:
            raise SkillExtractionError("启用 LLM 技能抽取时必须提供 ModelClient")
        return [*baseline, *self.extract_jd_llm(text, model_client)]

    def extract_jd_llm(self, text: str, model_client: ModelClient) -> list[SkillMention]:
        system = (
            "你是 JD 技能证据抽取器。只返回严格 JSON 对象，格式为 "
            '{"skills":[{"skill":"原文技能名","canonical":"规范技能名",'
            '"category":"类别","evidence":"JD中原文短证据","confidence":0.0,'
            '"requirement":"must_have|preferred|unknown"}]}。'
            "evidence 必须逐字来自 JD；不要抽取公司、软技能或无法定位的推断。"
        )
        try:
            payload = model_client.chat_json(system, text[:12000])
            response = LlmSkillResponse.model_validate(payload)
        except QueryTimeoutError:
            raise
        except (RuntimeError, TypeError, ValueError, ValidationError) as exc:
            raise SkillExtractionError(f"LLM 技能结构化输出校验失败：{type(exc).__name__}") from exc

        result: list[SkillMention] = []
        folded_text = text.casefold()
        for index, item in enumerate(response.skills):
            evidence = item.evidence.strip()
            start = folded_text.find(evidence.casefold())
            if start < 0:
                raise SkillExtractionError(f"LLM 第 {index + 1} 项技能证据不在 JD 原文中")
            canonical = self.canonicalize(item.canonical or item.skill)
            result.append(
                SkillMention(
                    skill=canonical,
                    display=item.skill,
                    category=item.category,
                    requirement=item.requirement,
                    evidence=EvidenceRef(
                        source="jd",
                        quote=evidence,
                        location=f"chars:{start}-{start + len(evidence)}",
                    ),
                    confidence=item.confidence,
                    source="llm",
                    extractor_version="jd-llm-v1",
                )
            )
        return result

    @staticmethod
    def _pattern(alias: str) -> re.Pattern[str]:
        escaped = re.escape(alias).replace(r"\ ", r"\s*")
        if re.search(r"[A-Za-z0-9]", alias):
            return re.compile(
                rf"(?<![A-Za-z0-9_+#]){escaped}(?![A-Za-z0-9_+#])",
                re.IGNORECASE,
            )
        return re.compile(escaped)

    @staticmethod
    def _quote(text: str, start: int, end: int) -> str:
        left = max(0, start - 60)
        right = min(len(text), end + 100)
        return re.sub(r"\s+", " ", text[left:right]).strip()[:500]

    @staticmethod
    def _resolve_requirement(text: str, start: int) -> str:
        before = text[max(0, start - 80):start]
        after = text[start:min(len(text), start + 100)]
        before_clause = re.split(r"[，,。；;\n]", before)[-1]
        after_clause = re.split(r"[，,。；;\n]", after)[0]
        context = f"{before_clause} {after_clause}"
        if re.search(r"(?:加分|优先|最好|熟悉即可|了解即可|有.*经验者优先)", context):
            return "preferred"
        return "must_have"


__all__ = [
    "LlmSkillItem",
    "LlmSkillResponse",
    "SkillCatalog",
    "SkillExtractionError",
]
