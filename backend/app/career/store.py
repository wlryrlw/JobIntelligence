from __future__ import annotations

import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ..config import BASE_DIR, settings
from ..contracts import ResumeEvidence
from .models import ResumeRecord, ResumeUploadResponse, public_resume_response
from .resume import ResumeLimits, extract_resume_evidence, parse_resume


class ResumeStoreError(ValueError):
    """简历本地存储或读取失败。"""


class ResumeNotFound(FileNotFoundError):
    """简历不存在或不属于当前用户。"""


class ResumeStore:
    """本地短期简历存储；元数据和结构化证据与原始文件分开保存。"""

    ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{8,100}$")

    def __init__(
        self,
        root: Path | None = None,
        *,
        limits: ResumeLimits | None = None,
    ) -> None:
        configured_root = Path(settings.resume_storage_path)
        if not configured_root.is_absolute():
            configured_root = BASE_DIR.parent / configured_root
        self.root = (root or configured_root).resolve()
        self.limits = limits or ResumeLimits(
            max_bytes=settings.resume_max_bytes,
            max_pages=settings.resume_max_pages,
            min_text_chars=settings.resume_min_text_chars,
        )

    def save(
        self,
        filename: str,
        content: bytes,
        *,
        user_id: str,
    ) -> ResumeUploadResponse:
        resume_id = uuid.uuid4().hex
        parsed = parse_resume(filename, content, self.limits)
        evidence = extract_resume_evidence(parsed, resume_id)
        record = ResumeRecord(
            resume_id=resume_id,
            user_id=user_id,
            filename=parsed.filename,
            media_type=parsed.media_type,
            size_bytes=parsed.size_bytes,
            page_count=parsed.page_count,
            redaction_count=parsed.redaction_count,
            source_hash=parsed.source_hash,
            created_at=datetime.now(timezone.utc),
            status="ready",
            skill_count=len(evidence.skills),
            project_count=len(evidence.projects),
        )
        record_dir = self._record_dir(resume_id)
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            record_dir.mkdir(parents=True, exist_ok=False)
            source_path = record_dir / f"source{Path(parsed.filename).suffix.casefold()}"
            source_path.write_bytes(content)
            (record_dir / "metadata.json").write_text(
                record.model_dump_json(indent=2),
                encoding="utf-8",
            )
            (record_dir / "evidence.json").write_text(
                evidence.model_dump_json(indent=2),
                encoding="utf-8",
            )
        except OSError as exc:
            if record_dir.exists():
                shutil.rmtree(record_dir)
            raise ResumeStoreError(f"简历保存失败：{type(exc).__name__}") from exc
        return public_resume_response(record, evidence)

    def load(self, resume_id: str, *, user_id: str) -> ResumeEvidence:
        record = self._load_record(resume_id)
        if record.user_id != user_id:
            raise ResumeNotFound("简历不存在")
        evidence_path = self._record_dir(resume_id) / "evidence.json"
        try:
            return ResumeEvidence.model_validate_json(evidence_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResumeStoreError(f"简历结构化证据读取失败：{type(exc).__name__}") from exc

    def delete(self, resume_id: str, *, user_id: str) -> None:
        record = self._load_record(resume_id)
        if record.user_id != user_id:
            raise PermissionError("无权删除该简历")
        record_dir = self._record_dir(resume_id)
        try:
            shutil.rmtree(record_dir)
        except OSError as exc:
            raise ResumeStoreError(f"简历删除失败：{type(exc).__name__}") from exc

    def _load_record(self, resume_id: str) -> ResumeRecord:
        record_dir = self._record_dir(resume_id)
        path = record_dir / "metadata.json"
        if not path.exists():
            raise ResumeNotFound("简历不存在")
        try:
            return ResumeRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ResumeStoreError(f"简历元数据无效：{type(exc).__name__}") from exc

    def _record_dir(self, resume_id: str) -> Path:
        if not self.ID_PATTERN.fullmatch(resume_id):
            raise ResumeNotFound("简历标识无效")
        candidate = (self.root / resume_id).resolve()
        if self.root not in candidate.parents:
            raise ResumeNotFound("简历标识无效")
        return candidate


__all__ = ["ResumeNotFound", "ResumeStore", "ResumeStoreError"]
