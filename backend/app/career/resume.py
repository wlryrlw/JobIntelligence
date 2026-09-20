from __future__ import annotations

import hashlib
import io
import re
from dataclasses import dataclass
from pathlib import Path

from ..contracts import EvidenceRef, ResumeEvidence, ResumeProjectEvidence
from .skills import SkillCatalog


class ResumeParseError(ValueError):
    """简历无法满足上传或文本提取契约。"""


@dataclass(frozen=True)
class ResumeLimits:
    max_bytes: int = 5 * 1024 * 1024
    max_pages: int = 10
    min_text_chars: int = 10

    def __post_init__(self) -> None:
        if self.max_bytes <= 0 or self.max_pages <= 0 or self.min_text_chars <= 0:
            raise ValueError("简历限制必须为正数")


@dataclass(frozen=True)
class ParsedResume:
    filename: str
    media_type: str
    size_bytes: int
    page_count: int
    text: str
    redacted_text: str
    redaction_count: int
    source_hash: str


_TEXT_SUFFIXES = {".txt", ".text", ".md", ".markdown"}
_EMAIL = re.compile(r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}", re.IGNORECASE)
_PHONE = re.compile(r"(?<!\d)(?:\+?86[\s-]?)?1[3-9]\d{9}(?!\d)")
_LANDLINE = re.compile(r"(?<!\d)0\d{2,3}[\s-]?\d{7,8}(?!\d)")
_ID_CARD = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")
_NAME_LINE = re.compile(r"(?im)^(姓名|联系人|真实姓名)\s*[:：]\s*[^\n]{1,40}$")
_ADDRESS_LINE = re.compile(r"(?im)^(地址|住址|联系地址|所在地)\s*[:：]\s*[^\n]{1,120}$")
_QUANTITATIVE = re.compile(
    r"(?<!\w)(?:\d+(?:\.\d+)?\s*(?:%|％|倍|人|个|万|万元|天|月|年|ms|秒|GB|TB)|"
    r"\d+(?:\.\d+)?\s*万/月)(?!\w)",
    re.IGNORECASE,
)
_PROJECT_MARKERS = re.compile(
    r"(?:项目|project|负责|实现|搭建|开发|设计|优化|部署|上线|维护|构建|落地)",
    re.IGNORECASE,
)


def parse_resume(
    filename: str,
    content: bytes,
    limits: ResumeLimits | None = None,
) -> ParsedResume:
    """提取文本并在进入后续 Agent 前完成确定性 PII 脱敏。"""

    active_limits = limits or ResumeLimits()
    safe_filename = Path(filename or "").name
    suffix = Path(safe_filename).suffix.casefold()
    if not safe_filename or suffix not in _TEXT_SUFFIXES | {".pdf"}:
        raise ResumeParseError("只支持 .txt、.text、.md、.markdown 和文本型 .pdf 简历")
    if not content:
        raise ResumeParseError("简历文件不能为空")
    if len(content) > active_limits.max_bytes:
        raise ResumeParseError(f"简历文件超过 {active_limits.max_bytes} 字节限制")

    if suffix == ".pdf":
        text, page_count = _extract_pdf(content, active_limits)
        media_type = "application/pdf"
    else:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise ResumeParseError("文本简历必须使用 UTF-8 编码") from exc
        page_count = 1
        media_type = "text/plain"

    normalized = re.sub(r"\r\n?", "\n", text).replace("\x00", "")
    if len(normalized.strip()) < active_limits.min_text_chars:
        raise ResumeParseError("简历未提取到足够的文本；扫描版 PDF 或空文件不能生成伪分析")
    redacted, redaction_count = redact_pii(normalized)
    if len(redacted.strip()) < active_limits.min_text_chars:
        raise ResumeParseError("简历脱敏后没有足够的可分析文本")
    return ParsedResume(
        filename=safe_filename,
        media_type=media_type,
        size_bytes=len(content),
        page_count=page_count,
        text=normalized,
        redacted_text=redacted,
        redaction_count=redaction_count,
        source_hash=hashlib.sha256(content).hexdigest(),
    )


def _extract_pdf(content: bytes, limits: ResumeLimits) -> tuple[str, int]:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PdfReadError
    except ImportError as exc:
        raise ResumeParseError("PDF 解析依赖 pypdf 未安装，请先同步项目依赖") from exc

    try:
        reader = PdfReader(io.BytesIO(content))
        page_count = len(reader.pages)
        if page_count == 0:
            raise ResumeParseError("PDF 没有页面")
        if page_count > limits.max_pages:
            raise ResumeParseError(f"PDF 页数超过 {limits.max_pages} 页限制")
        pages = [(page.extract_text() or "") for page in reader.pages]
    except PdfReadError as exc:
        raise ResumeParseError("PDF 文件损坏或不是可读取的 PDF") from exc
    except (OSError, ValueError, KeyError) as exc:
        raise ResumeParseError(f"PDF 文本提取失败：{type(exc).__name__}") from exc

    return "\n".join(pages), page_count


def redact_pii(text: str) -> tuple[str, int]:
    """只对确定可识别的 PII 做替换，保留技能和项目上下文。"""

    count = 0

    def replace(pattern: re.Pattern[str], replacement: str, value: str) -> str:
        nonlocal count

        def repl(_: re.Match[str]) -> str:
            nonlocal count
            count += 1
            return replacement

        return pattern.sub(repl, value)

    redacted = replace(_NAME_LINE, "姓名：[已脱敏]", text)
    redacted = replace(_ADDRESS_LINE, "地址：[已脱敏]", redacted)
    redacted = replace(_EMAIL, "[邮箱已脱敏]", redacted)
    redacted = replace(_PHONE, "[手机已脱敏]", redacted)
    redacted = replace(_LANDLINE, "[电话已脱敏]", redacted)
    redacted = replace(_ID_CARD, "[证件号已脱敏]", redacted)
    return redacted, count


def extract_resume_evidence(parsed: ParsedResume, resume_id: str) -> ResumeEvidence:
    """从脱敏文本提取技能、项目行和量化证据；不保留完整脱敏全文。"""

    if not resume_id.strip():
        raise ResumeParseError("resume_id 不能为空")
    catalog = SkillCatalog()
    skills = catalog.extract_resume(parsed.redacted_text)
    projects: list[ResumeProjectEvidence] = []
    quantitative: list[str] = []
    seen_project_lines: set[str] = set()

    for line_number, raw_line in enumerate(parsed.redacted_text.splitlines(), 1):
        line = raw_line.strip().lstrip("-•*· ")
        if not line:
            continue
        metrics = list(dict.fromkeys(match.group(0).strip() for match in _QUANTITATIVE.finditer(line)))
        quantitative.extend(metrics)
        mentions = catalog.extract_resume(line)
        if not mentions and not _PROJECT_MARKERS.search(line):
            continue
        if len(line) < 8 or line in seen_project_lines:
            continue
        seen_project_lines.add(line)
        name = line.split(":", 1)[0].split("：", 1)[0].strip()
        name = name[:80] if name else "未命名项目"
        project_skills = list(dict.fromkeys(item.skill for item in mentions))
        projects.append(
            ResumeProjectEvidence(
                name=name,
                summary=line[:500],
                skills=project_skills,
                evidence=[
                    EvidenceRef(
                        source="resume",
                        quote=line[:500],
                        location=f"line:{line_number}",
                    )
                ],
                quantitative_evidence=metrics,
            )
        )

    return ResumeEvidence(
        resume_id=resume_id,
        source_hash=parsed.source_hash,
        page_count=parsed.page_count,
        pii_redaction_count=parsed.redaction_count,
        skills=skills,
        projects=projects[:100],
        quantitative_evidence=list(dict.fromkeys(quantitative))[:100],
        extractor_version="resume-regex-v1",
    )


__all__ = [
    "ParsedResume",
    "ResumeLimits",
    "ResumeParseError",
    "extract_resume_evidence",
    "parse_resume",
    "redact_pii",
]
