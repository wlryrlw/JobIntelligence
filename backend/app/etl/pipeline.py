from __future__ import annotations

import csv
import fcntl
import hashlib
import json
import logging
import os
import tempfile
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import TypeVar

import duckdb

from pydantic import BaseModel, TypeAdapter, ValidationError

from .models import (CrawlRun, DatasetManifest, Job, JobDetail, JobSkill, QualityIssue,
                     QualityReport, SourceBatch, SourceJob, TABLE_MODELS)
from .normalize import extract_tags, identity, make_detail, make_job, merge_job
from .schema import generate_schema, load_market_schema


ETL_VERSION = "1.0.0"
logger = logging.getLogger(__name__)
Model = TypeVar("Model", bound=BaseModel)


class EtlError(ValueError):
    """输入或已发布数据无法满足 ETL 契约；不发布部分结果。"""


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_rows(path: Path, model: type[BaseModel], rows: list[BaseModel]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(model.model_fields))
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, list) else value
                             for key, value in row.model_dump(mode="json").items()})


def read_rows(path: Path, model: type[Model]) -> list[Model]:
    properties = model.model_json_schema()["properties"]
    rows = []
    with path.open(encoding="utf-8", newline="") as stream:
        for raw in csv.DictReader(stream):
            data: dict[str, object] = dict(raw)
            for name, field in properties.items():
                value = raw[name]
                types = [item.get("type") for item in field.get("anyOf", [field])]
                if value == "" and "null" in types:
                    data[name] = None
                elif "array" in types:
                    data[name] = json.loads(value)
            rows.append(model.model_validate(data))
    return rows


def read_valid_rows(values: list[object], source: str, issues: list[QualityIssue]) -> list[SourceJob]:
    rows = []
    for index, value in enumerate(values, 1):
        try:
            rows.append(SourceJob.model_validate(value))
        except ValidationError as exc:
            issues.append(QualityIssue(source=source, row=index, code="invalid_record",
                                       fields=[".".join(map(str, error["loc"])) for error in exc.errors(include_input=False)]))
            logger.warning("etl_record_rejected", extra={"source_kind": source, "row_index": index, "error_count": exc.error_count()})
    return rows


def _verify_snapshot(folder: Path) -> DatasetManifest:
    manifest = DatasetManifest.model_validate_json((folder / "_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest.file_hashes.items():
        if Path(name).name != name or digest((folder / name).read_bytes()) != expected:
            raise EtlError(f"已发布数据校验失败：{folder.name}/{name}；请恢复该版本，不要手工修改 CSV")
    required = {f"{name}.csv" for name in TABLE_MODELS} | {"_schema.json", "_quality.json"}
    if set(manifest.file_hashes) != required:
        raise EtlError("已发布 manifest 文件清单不完整")
    load_market_schema(folder)
    return manifest


def run_etl(jobs_path: Path, details_path: Path | None, database_root: Path, raw_root: Path) -> QualityReport:
    """离线单写入器 ETL。先完整生成和校验快照，再原子切换 jobs_market 链接。"""
    database_root.mkdir(parents=True, exist_ok=True)
    raw_root.mkdir(parents=True, exist_ok=True)
    with (database_root / ".jobs_market.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise EtlError("另一个 ETL 正在发布岗位库；请等待其结束后重试") from exc
        # 使用打开的文件描述符持有 OS 锁，进程崩溃也会自动释放。
        return _run_locked(jobs_path, details_path, database_root, raw_root)


def _run_locked(jobs_path: Path, details_path: Path | None, database_root: Path, raw_root: Path) -> QualityReport:
    jobs_bytes = jobs_path.read_bytes()
    details_bytes = details_path.read_bytes() if details_path else b""
    source_hash = digest(jobs_bytes + b"\0" + details_bytes)
    run_id = "run_" + digest((source_hash + ETL_VERSION).encode())[:24]
    archive = raw_root / run_id
    archive.mkdir(exist_ok=True)
    (archive / "jobs.json").write_bytes(jobs_bytes)
    if details_path:
        (archive / "details.json").write_bytes(details_bytes)
    try:
        return _build(jobs_bytes, details_bytes, run_id, source_hash, archive, database_root)
    except (ValidationError, ValueError, OSError, duckdb.Error) as exc:
        (archive / "failure.json").write_text(json.dumps({"run_id": run_id, "error_type": type(exc).__name__, "code": "etl_failed"}), encoding="utf-8")
        if isinstance(exc, EtlError):
            raise
        raise EtlError(f"ETL 失败，输入归档位于 {archive}；错误类型 {type(exc).__name__}，请检查输入结构和文件权限") from exc


def _build(jobs_bytes: bytes, details_bytes: bytes, run_id: str, source_hash: str, archive: Path, database_root: Path) -> QualityReport:
    batch = SourceBatch.model_validate_json(jobs_bytes)
    if batch.status in {"failed", "blocked", "cancelled"}:
        raise EtlError(f"源采集状态为 {batch.status}，不发布成功数据版本；原始数据已归档于 {archive}")
    detail_values = batch.details
    if details_bytes:
        payload = json.loads(details_bytes)
        if isinstance(payload, dict):
            payload = payload.get("details", payload.get("jobs"))
        detail_values = TypeAdapter(list[object]).validate_python(payload)
    target = database_root / "jobs_market"
    if target.exists() and not target.is_symlink():
        raise EtlError(f"目标 {target} 是普通目录；请先备份并移走，ETL 不会覆盖现有数据")
    previous = target.resolve() if target.exists() else None
    if target.is_symlink() and previous is None:
        raise EtlError(f"岗位库发布链接已损坏：{target}")
    manifest = _verify_snapshot(previous) if previous else None
    if manifest and manifest.etl_version != ETL_VERSION:
        raise EtlError("ETL 版本不同，请使用新的 database_root 重建，不能混合旧版本增量数据")
    if manifest and run_id in manifest.run_ids:
        return QualityReport.model_validate_json((archive / "quality.json").read_text(encoding="utf-8"))
    version = "ds_" + digest(((manifest.dataset_version if manifest else "") + run_id).encode())[:24]
    issues: list[QualityIssue] = []
    sources = read_valid_rows(batch.jobs, "jobs", issues)
    detail_sources = read_valid_rows(detail_values, "details", issues)
    if not sources:
        raise EtlError(f"输入无有效岗位，不发布空快照；检查 {archive}/jobs.json")
    jobs = {row.job_id: row for row in read_rows(previous / "jobs.csv", Job)} if previous else {}
    details = {row.job_id: row for row in read_rows(previous / "job_details.csv", JobDetail)} if previous else {}
    skills = read_rows(previous / "job_skills.csv", JobSkill) if previous else []
    runs = read_rows(previous / "crawl_runs.csv", CrawlRun) if previous else []
    seen: set[str] = set()
    duplicates = 0
    for index, source in enumerate(sources, 1):
        job = make_job(source, batch.scraped_at, batch.keyword, version)
        if job.job_id in seen:
            duplicates += 1
        seen.add(job.job_id)
        old = jobs.get(job.job_id)
        if old and old.job_link and job.job_link and old.job_link != job.job_link:
            raise EtlError(f"job_id 对应不同 job_link，拒绝错误合并：{job.job_id}")
        jobs[job.job_id] = merge_job(old, job) if old else job
        skills.extend(extract_tags(source, batch.scraped_at, run_id, detail=False))
        if source.salary_source not in {"api", "fixture"}:
            issues.append(QualityIssue(source="jobs", row=index, code="untrusted_salary_source", fields=["salary_source"]))
    orphans = 0
    accepted_detail_ids: set[str] = set()
    for index, source in enumerate(detail_sources, 1):
        key = identity(source)
        if key not in jobs:
            orphans += 1
            issues.append(QualityIssue(source="details", row=index, code="orphan_detail", fields=["job_id"]))
            continue
        job = jobs[key]
        detail = make_detail(source, batch.scraped_at)
        link = source.job_link or source.link
        if link and job.job_link and link != job.job_link:
            raise EtlError(f"详情 job_link 与岗位不一致：{key}")
        old = details.get(key)
        # 空详情不能擦掉旧的有效 JD；旧批次不能覆盖新详情。
        if old is None or (detail.captured_at >= old.captured_at and (detail.jd_text or not old.jd_text)):
            details[key] = detail
        if source.company and detail.captured_at >= job.latest_scraped_at:
            jobs[key] = Job.model_validate({**job.model_dump(), "company": source.company})
        if detail.jd_text:
            accepted_detail_ids.add(key)
        skills.extend(extract_tags(source, batch.scraped_at, run_id, detail=True))
    unique_skills = {(s.job_id, s.skill_canonical, s.source, s.evidence_text, s.extractor_version, s.run_id): s for s in skills}
    jobs = {key: Job.model_validate({**job.model_dump(), "dataset_version": version}) for key, job in jobs.items()}
    counts = Counter(job.salary_parse_status for job in jobs.values())
    report = QualityReport(run_id=run_id, dataset_version=version, input_jobs=len(batch.jobs),
                           accepted_jobs=len(sources), rejected_jobs=len(batch.jobs)-len(sources), duplicate_jobs=duplicates,
                           input_details=len(detail_values), rejected_details=len(detail_values)-len(detail_sources), orphan_details=orphans,
                           total_jobs=len(jobs), salary_status_counts=dict(counts), salary_failure_count=counts["unsupported"]+counts["invalid"],
                           salary_untrusted_count=sum(j.salary_source not in {"api", "fixture"} for j in jobs.values()),
                           missing_detail_count=sum(key not in details or not details[key].jd_text for key in jobs), issues=issues)
    runs.append(CrawlRun(run_id=run_id, keyword=batch.keyword, city=batch.city, requested_pages=batch.requested_pages,
                         completed_pages=batch.completed_pages, status="succeeded", source_status=batch.status, job_count=len(seen),
                         detail_count=len(accepted_detail_ids), started_at=batch.scraped_at, finished_at=datetime.now(timezone.utc),
                         scraper_version=batch.scraper_version, etl_version=ETL_VERSION, dataset_version=version, source_hash=source_hash))
    versions = database_root / ".jobs_market_versions"
    versions.mkdir(exist_ok=True)
    # 即使先前在发布前崩溃，新的临时目录也不会覆盖已发布的版本。
    folder = Path(tempfile.mkdtemp(prefix=version + "_", dir=versions))
    rows = {"jobs": [jobs[key] for key in sorted(jobs)], "job_details": [details[key] for key in sorted(details)],
            "job_skills": [unique_skills[key] for key in sorted(unique_skills)], "crawl_runs": runs}
    for name, model in TABLE_MODELS.items():
        write_rows(folder / f"{name}.csv", model, rows[name])
    generate_schema(folder, version)
    (folder / "_quality.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    hashes = {p.name: digest(p.read_bytes()) for p in folder.iterdir()}
    published = DatasetManifest(dataset_version=version, etl_version=ETL_VERSION,
                                run_ids=[run.run_id for run in runs], file_hashes=hashes)
    (folder / "_manifest.json").write_text(published.model_dump_json(indent=2), encoding="utf-8")
    _verify_snapshot(folder)
    (archive / "quality.json").write_text(report.model_dump_json(indent=2), encoding="utf-8")
    pending = database_root / f".jobs_market_{folder.name}.link"
    pending.symlink_to(folder.relative_to(database_root), target_is_directory=True)
    os.replace(pending, target)
    logger.info("etl_published", extra={"run_id": run_id, "dataset_version": version, "job_count": len(jobs), "salary_failure_count": report.salary_failure_count})
    return report
