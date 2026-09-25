from __future__ import annotations

"""当前已发布岗位快照的只读概览。

概览只读取 ``jobs_market`` 的当前发布版本，不访问 scraper 原始输出，也不执行
用户提供的 SQL。这样前端可以快速展示数据规模和分布，同时与对话查询使用同一份
已校验快照。
"""

import fcntl
import hashlib
import json
import logging
import math
import os
import re
import shutil
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .etl.models import CrawlRun, DatasetManifest, Job, JobDetail, JobSkill, QualityReport, normalize_time
from .etl.pipeline import ETL_VERSION, EtlError, digest, read_rows, run_etl, write_rows
from .etl.schema import generate_schema, load_market_schema
from .querying.duckdb_engine import DATABASE_ROOT, DuckDbEngine


logger = logging.getLogger(__name__)


class MarketDataBreakdown(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str
    count: int = Field(ge=0)
    ratio: float = Field(ge=0, le=1)


class MarketDataSummary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    available: bool
    dataset_version: str | None = None
    job_count: int = Field(default=0, ge=0)
    detail_count: int = Field(default=0, ge=0)
    detail_coverage: float = Field(default=0, ge=0, le=1)
    first_scraped_at: datetime | None = None
    latest_scraped_at: datetime | None = None
    top_cities: list[MarketDataBreakdown] = Field(default_factory=list)
    top_keywords: list[MarketDataBreakdown] = Field(default_factory=list)
    top_industries: list[MarketDataBreakdown] = Field(default_factory=list)
    raw_available: bool = False
    raw_job_count: int = Field(default=0, ge=0)
    raw_detail_count: int = Field(default=0, ge=0)
    raw_latest_scraped_at: datetime | None = None
    raw_pending_publish: bool = False


class MarketDataRun(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    keyword: str
    city: str
    status: str
    source_status: str
    requested_pages: int | None = None
    completed_pages: int | None = None
    page_count: int | None = Field(default=None, ge=0)
    page_count_estimated: bool = False
    job_count: int = Field(ge=0)
    detail_count: int = Field(ge=0)
    started_at: datetime
    finished_at: datetime
    dataset_version: str
    etl_version: str
    archive_available: bool
    deletable: bool
    delete_block_reason: str | None = None


class MarketDataDeleteResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted: bool
    run_id: str
    dataset_version: str | None = None
    message: str


class MarketDataError(ValueError):
    """岗位批次不存在或无法在保持快照一致性的前提下删除。"""


class MarketDataSummaryService:
    """读取并汇总当前 ``jobs_market`` 发布快照。"""

    _BREAKDOWN_FIELDS = {
        "city": "top_cities",
        "keyword": "top_keywords",
        "company_industry": "top_industries",
    }

    def __init__(self, database_root: Path | None = None) -> None:
        self.database_root = database_root or DATABASE_ROOT
        self.engine = DuckDbEngine(self.database_root)

    @property
    def etl_raw_root(self) -> Path:
        return self.database_root.parent / "local" / "etl"

    @property
    def scraper_result_root(self) -> Path:
        return self.database_root.parent / "local" / "boss_zhipin" / "job-result"

    def runs(self) -> list[MarketDataRun]:
        folder = self.database_root / "jobs_market"
        path = folder / "crawl_runs.csv"
        if not path.exists():
            return []
        records = read_rows(path, CrawlRun)
        result = []
        for record in records:
            jobs_path, details_path = self._archive_for(record)
            archive_available = jobs_path is not None
            detail_available = self._archive_has_details(jobs_path, details_path)
            requested_pages, completed_pages, page_count, page_count_estimated = self._page_metadata(record)
            block_reason = None
            if not archive_available:
                block_reason = "缺少该批次的 ETL 原始归档，无法安全重建数据"
            elif record.detail_count > 0 and not detail_available:
                block_reason = "缺少该批次的详情归档，无法安全重建数据"
            result.append(
                MarketDataRun(
                    run_id=record.run_id,
                    keyword=record.keyword,
                    city=record.city,
                    status=record.status,
                    source_status=record.source_status,
                    requested_pages=requested_pages,
                    completed_pages=completed_pages,
                    page_count=page_count,
                    page_count_estimated=page_count_estimated,
                    job_count=record.job_count,
                    detail_count=record.detail_count,
                    started_at=record.started_at,
                    finished_at=record.finished_at,
                    dataset_version=record.dataset_version,
                    etl_version=record.etl_version,
                    archive_available=archive_available,
                    deletable=block_reason is None,
                    delete_block_reason=block_reason,
                )
            )
        return sorted(result, key=lambda item: item.finished_at, reverse=True)

    def delete_run(self, run_id: str) -> MarketDataDeleteResult:
        if not re.fullmatch(r"run_[A-Za-z0-9_-]{1,80}", run_id):
            raise MarketDataError("采集批次编号格式不合法")
        self.database_root.mkdir(parents=True, exist_ok=True)
        lock_path = self.database_root / ".jobs_market.lock"
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise MarketDataError("另一个 ETL 或数据删除任务正在运行，请稍后重试") from exc
            try:
                return self._delete_run_locked(run_id)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _delete_run_locked(self, run_id: str) -> MarketDataDeleteResult:
        folder = self.database_root / "jobs_market"
        runs_path = folder / "crawl_runs.csv"
        if not runs_path.exists():
            raise MarketDataError("当前没有已发布的岗位采集批次")
        records = read_rows(runs_path, CrawlRun)
        selected = next((record for record in records if record.run_id == run_id), None)
        if selected is None:
            raise MarketDataError(f"采集批次不存在：{run_id}")

        retained = [record for record in records if record.run_id != run_id]
        archives = {record.run_id: self._archive_for(record) for record in retained}
        selected_archive = self._archive_for(selected)
        if selected_archive[0] is None:
            raise MarketDataError("缺少待删除批次的 ETL 原始归档，无法安全删除")
        if selected.detail_count > 0 and not self._archive_has_details(*selected_archive):
            raise MarketDataError("缺少待删除批次的详情归档，无法安全删除")
        for record in retained:
            jobs_path, details_path = archives[record.run_id]
            if jobs_path is None:
                raise MarketDataError(f"保留批次 {record.run_id} 的岗位归档缺失，删除会造成数据不完整")
            if record.detail_count > 0 and not self._archive_has_details(jobs_path, details_path):
                raise MarketDataError(f"保留批次 {record.run_id} 的详情归档缺失，删除会造成 JD 数据不完整")

        staging_root = Path(tempfile.mkdtemp(prefix=".jobs_market_rebuild_", dir=self.database_root))
        staged_database_root = staging_root / "databases"
        staged_raw_root = staging_root / "etl"
        try:
            for record in sorted(retained, key=lambda item: item.started_at):
                jobs_path, details_path = archives[record.run_id]
                if jobs_path is None:
                    raise MarketDataError(f"保留批次 {record.run_id} 的岗位归档缺失")
                run_etl(jobs_path, details_path, staged_database_root, staged_raw_root)

            published_version: str | None = None
            versions_root = self.database_root / ".jobs_market_versions"
            versions_root.mkdir(parents=True, exist_ok=True)
            target = self.database_root / "jobs_market"
            if retained:
                staged_folder = (staged_database_root / "jobs_market").resolve()
                published_version = self._relabel_staged_snapshot(staged_folder, retained)
                final_folder = versions_root / f"{staged_folder.name}_rebuild_{uuid.uuid4().hex[:8]}"
                shutil.copytree(staged_folder, final_folder)
                pending = self.database_root / f".jobs_market_{uuid.uuid4().hex}.link"
                pending.symlink_to(final_folder.relative_to(self.database_root), target_is_directory=True)
                os.replace(pending, target)
                self._remove_versions_containing(run_id, keep=final_folder)
            else:
                if target.exists() or target.is_symlink():
                    if not target.is_symlink():
                        raise MarketDataError("当前岗位库不是受控的发布链接，拒绝删除")
                    target.unlink()
                self._remove_versions_containing(run_id, keep=None)

            self._remove_archive(selected.run_id)
            return MarketDataDeleteResult(
                deleted=True,
                run_id=run_id,
                dataset_version=published_version,
                message="已删除该采集批次，并重新发布剩余岗位数据",
            )
        except (EtlError, OSError, ValueError) as exc:
            if isinstance(exc, MarketDataError):
                raise
            raise MarketDataError(f"删除批次失败，当前岗位数据未切换：{exc}") from exc
        finally:
            try:
                shutil.rmtree(staging_root)
            except OSError as exc:
                logger.warning("market_data_staging_cleanup_failed", extra={"path": str(staging_root), "error": str(exc)})

    def _archive_for(self, record: CrawlRun) -> tuple[Path | None, Path | None]:
        jobs_path = self._archive_jobs_path(record.run_id)
        details_path = self._archive_details_path(record.run_id)
        return (jobs_path if jobs_path.is_file() else None, details_path if details_path.is_file() else None)

    def _archive_jobs_path(self, run_id: str) -> Path:
        return self.etl_raw_root / run_id / "jobs.json"

    def _archive_details_path(self, run_id: str) -> Path:
        return self.etl_raw_root / run_id / "details.json"

    def _page_metadata(self, record: CrawlRun) -> tuple[int | None, int | None, int | None, bool]:
        requested_pages = record.requested_pages
        completed_pages = record.completed_pages
        if requested_pages is None or completed_pages is None:
            archive_requested, archive_completed = self._read_archive_page_metadata(record.run_id)
            requested_pages = requested_pages if requested_pages is not None else archive_requested
            completed_pages = completed_pages if completed_pages is not None else archive_completed
        if completed_pages is not None:
            return requested_pages, completed_pages, completed_pages, False
        if requested_pages is not None:
            return requested_pages, None, requested_pages, True
        if record.job_count > 0:
            # 旧版 BOSS scraper 没有落盘页数；其页面契约是每页最多 15 条，
            # 因此只能给出带有明确“估算”标记的兼容值。
            return None, None, math.ceil(record.job_count / 15), True
        return None, None, None, False

    def _read_archive_page_metadata(self, run_id: str) -> tuple[int | None, int | None]:
        path = self._archive_jobs_path(run_id)
        if not path.is_file():
            return None, None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("market_data_archive_page_metadata_unreadable", extra={"path": str(path), "error": str(exc)})
            return None, None
        if not isinstance(payload, dict):
            return None, None
        return self._nonnegative_int(payload.get("requested_pages")), self._nonnegative_int(payload.get("completed_pages"))

    @staticmethod
    def _nonnegative_int(value: object) -> int | None:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return None
        return value

    @staticmethod
    def _archive_has_details(jobs_path: Path | None, details_path: Path | None) -> bool:
        if details_path is not None and details_path.is_file():
            return True
        if jobs_path is None or not jobs_path.is_file():
            return False
        try:
            payload = json.loads(jobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("market_data_archive_details_unreadable", extra={"path": str(jobs_path), "error": str(exc)})
            return False
        return isinstance(payload, dict) and isinstance(payload.get("details"), list)

    def _relabel_staged_snapshot(self, folder: Path, retained: list[CrawlRun]) -> str:
        version = "ds_" + hashlib.sha256(("delete-rebuild:" + ":".join(item.run_id for item in retained) + ":" + str(datetime.now(timezone.utc).timestamp())).encode()).hexdigest()[:24]
        jobs = [item.model_copy(update={"dataset_version": version}) for item in read_rows(folder / "jobs.csv", Job)]
        details = read_rows(folder / "job_details.csv", JobDetail)
        skills = read_rows(folder / "job_skills.csv", JobSkill)
        crawl_runs = [item.model_copy(update={"dataset_version": version}) for item in retained]
        for name, model, rows in (
            ("jobs", Job, jobs),
            ("job_details", JobDetail, details),
            ("job_skills", JobSkill, skills),
            ("crawl_runs", CrawlRun, crawl_runs),
        ):
            write_rows(folder / f"{name}.csv", model, rows)
        generate_schema(folder, version)
        quality_path = folder / "_quality.json"
        quality = QualityReport.model_validate_json(quality_path.read_text(encoding="utf-8")).model_copy(update={"dataset_version": version})
        quality_path.write_text(quality.model_dump_json(indent=2), encoding="utf-8")
        manifest_path = folder / "_manifest.json"
        file_hashes = {path.name: digest(path.read_bytes()) for path in folder.iterdir() if path.is_file() and path.name != "_manifest.json"}
        manifest = DatasetManifest(dataset_version=version, etl_version=ETL_VERSION, run_ids=[item.run_id for item in retained], file_hashes=file_hashes)
        manifest_path.write_text(manifest.model_dump_json(indent=2), encoding="utf-8")
        # 在切换当前链接前复用 ETL 的完整校验，确保删除重建不会发布半成品。
        self._verify_rebuilt_snapshot(folder)
        return version

    @staticmethod
    def _verify_rebuilt_snapshot(folder: Path) -> None:
        manifest = DatasetManifest.model_validate_json((folder / "_manifest.json").read_text(encoding="utf-8"))
        required = {f"{name}.csv" for name in ("jobs", "job_details", "job_skills", "crawl_runs")} | {"_schema.json", "_quality.json"}
        if set(manifest.file_hashes) != required:
            raise MarketDataError("删除重建后的 manifest 文件清单不完整")
        for name, expected in manifest.file_hashes.items():
            if digest((folder / name).read_bytes()) != expected:
                raise MarketDataError(f"删除重建后的数据校验失败：{name}")
        load_market_schema(folder)

    def _remove_versions_containing(self, run_id: str, *, keep: Path | None) -> None:
        versions_root = self.database_root / ".jobs_market_versions"
        if not versions_root.is_dir():
            return
        for path in versions_root.iterdir():
            if keep is not None and path.resolve() == keep.resolve():
                continue
            if not path.is_dir() or path.is_symlink():
                continue
            manifest_path = path / "_manifest.json"
            if not manifest_path.exists():
                continue
            try:
                manifest = DatasetManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("market_data_version_manifest_invalid", extra={"path": str(manifest_path), "error": str(exc)})
                continue
            if run_id in manifest.run_ids:
                shutil.rmtree(path)

    def _remove_archive(self, run_id: str) -> None:
        etl_archive = self.etl_raw_root / run_id
        self._remove_legacy_scraper_outputs(etl_archive)
        for path in (etl_archive, self.scraper_result_root / run_id):
            if not path.exists():
                continue
            if not path.is_dir() or path.is_symlink():
                raise MarketDataError(f"拒绝删除非目录采集归档：{path}")
            shutil.rmtree(path)

    def _remove_legacy_scraper_outputs(self, etl_archive: Path) -> None:
        """清理旧版 scraper 放在 job-result 顶层、没有 run_id 目录的同源文件。"""
        if not etl_archive.is_dir() or not self.scraper_result_root.is_dir():
            return
        expected = (
            ("boss_jobs_", etl_archive / "jobs.json"),
            ("boss_details_", etl_archive / "details.json"),
        )
        for prefix, archive_path in expected:
            embedded_detail_ids = self._embedded_detail_identities(etl_archive) if prefix == "boss_details_" and not archive_path.is_file() else set()
            if not archive_path.is_file() and not embedded_detail_ids:
                continue
            for path in self.scraper_result_root.glob(f"{prefix}*.json"):
                if not path.is_file() or path.is_symlink():
                    continue
                try:
                    same_bytes = archive_path.is_file() and digest(path.read_bytes()) == digest(archive_path.read_bytes())
                    same_ids = archive_path.is_file() and self._same_job_identities(path, archive_path)
                    same_embedded_ids = bool(embedded_detail_ids) and self._file_job_identities(path) == embedded_detail_ids
                    if same_bytes or same_ids or same_embedded_ids:
                        path.unlink()
                except (OSError, json.JSONDecodeError) as exc:
                    raise MarketDataError(f"删除旧版 scraper 原始文件失败：{path}") from exc

    @staticmethod
    def _same_job_identities(left: Path, right: Path) -> bool:
        left_ids = MarketDataSummaryService._file_job_identities(left)
        right_ids = MarketDataSummaryService._file_job_identities(right)
        return bool(left_ids) and left_ids == right_ids

    @staticmethod
    def _file_job_identities(path: Path) -> set[str]:
        payload = json.loads(path.read_text(encoding="utf-8"))
        rows = payload
        if isinstance(payload, dict):
            rows = payload.get("jobs", payload.get("details", []))
        return MarketDataSummaryService._rows_job_identities(rows)

    @staticmethod
    def _embedded_detail_identities(etl_archive: Path) -> set[str]:
        jobs_path = etl_archive / "jobs.json"
        if not jobs_path.is_file():
            return set()
        payload = json.loads(jobs_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return set()
        return MarketDataSummaryService._rows_job_identities(payload.get("details", []))

    @staticmethod
    def _rows_job_identities(rows: object) -> set[str]:
        if not isinstance(rows, list):
            return set()
        return {
            str(item.get("job_id") or item.get("job_link") or item.get("link"))
            for item in rows
            if isinstance(item, dict) and (item.get("job_id") or item.get("job_link") or item.get("link"))
        }

    def get(self) -> MarketDataSummary:
        raw_job_count, raw_detail_count, raw_latest_scraped_at = self._raw_result_summary()
        folder = self.database_root / "jobs_market"
        if not folder.is_dir() or not any(folder.glob("*.csv")):
            return MarketDataSummary(
                available=False,
                raw_available=raw_job_count > 0,
                raw_job_count=raw_job_count,
                raw_detail_count=raw_detail_count,
                raw_latest_scraped_at=raw_latest_scraped_at,
                raw_pending_publish=raw_job_count > 0,
            )

        schema = load_market_schema(folder.resolve())
        with self.engine.connect("jobs_market") as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*)::BIGINT AS job_count,
                    MIN(latest_scraped_at) AS first_scraped_at,
                    MAX(latest_scraped_at) AS latest_scraped_at
                FROM jobs
                """
            ).fetchone()
            detail_row = connection.execute(
                """
                SELECT COUNT(DISTINCT CASE
                    WHEN detail_status = 'complete' AND length(trim(jd_text)) > 0
                    THEN job_id END)::BIGINT AS detail_count
                FROM job_details
                """
            ).fetchone()
            job_count = int(row[0] or 0)
            detail_count = int(detail_row[0] or 0)
            breakdowns = {
                result_name: self._breakdown(connection, field, job_count)
                for field, result_name in self._BREAKDOWN_FIELDS.items()
            }

        published_latest = self._as_utc(row[2])
        raw_pending_publish = self._raw_is_newer(
            raw_job_count=raw_job_count,
            raw_latest_scraped_at=raw_latest_scraped_at,
            published_job_count=job_count,
            published_latest_scraped_at=published_latest,
        )
        return MarketDataSummary(
            available=job_count > 0,
            dataset_version=schema.dataset_version,
            job_count=job_count,
            detail_count=detail_count,
            detail_coverage=(detail_count / job_count) if job_count else 0,
            first_scraped_at=self._as_utc(row[1]),
            latest_scraped_at=published_latest,
            top_cities=breakdowns["top_cities"],
            top_keywords=breakdowns["top_keywords"],
            top_industries=breakdowns["top_industries"],
            raw_available=raw_job_count > 0,
            raw_job_count=raw_job_count,
            raw_detail_count=raw_detail_count,
            raw_latest_scraped_at=raw_latest_scraped_at,
            raw_pending_publish=raw_pending_publish,
        )

    def _raw_result_summary(self) -> tuple[int, int, datetime | None]:
        """读取最新 scraper 结果的数量，仅用于状态提示，不把原始数据接入查询。"""
        result_root = self.database_root.parent / "local" / "boss_zhipin" / "job-result"
        jobs_path = self._latest_result_file(result_root, "boss_jobs_")
        if jobs_path is None:
            return 0, 0, None
        try:
            payload = json.loads(jobs_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("market_data_raw_jobs_unreadable", extra={"path": str(jobs_path), "error": str(exc)})
            return 0, 0, None
        if not isinstance(payload, dict) or not isinstance(payload.get("jobs"), list):
            logger.warning("market_data_raw_jobs_invalid", extra={"path": str(jobs_path)})
            return 0, 0, None

        scraped_at = self._parse_raw_timestamp(payload.get("scraped_at"), jobs_path)
        details_path = self._latest_result_file(result_root, "boss_details_")
        detail_count = self._raw_detail_count(details_path) if details_path else 0
        return len(payload["jobs"]), detail_count, scraped_at

    @staticmethod
    def _latest_result_file(root: Path, prefix: str) -> Path | None:
        if not root.is_dir():
            return None
        candidates: list[tuple[float, str, Path]] = []
        for path in root.glob(f"{prefix}*.json"):
            if not path.is_file():
                continue
            try:
                candidates.append((path.stat().st_mtime, path.name, path))
            except OSError as exc:
                logger.warning("market_data_raw_file_stat_failed", extra={"path": str(path), "error": str(exc)})
        return max(candidates, default=None, key=lambda item: (item[0], item[1]))[2] if candidates else None

    @staticmethod
    def _raw_detail_count(path: Path) -> int:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("market_data_raw_details_unreadable", extra={"path": str(path), "error": str(exc)})
            return 0
        if not isinstance(payload, list):
            logger.warning("market_data_raw_details_invalid", extra={"path": str(path)})
            return 0
        return sum(
            1
            for item in payload
            if isinstance(item, dict) and str(item.get("jd") or item.get("jd_text") or "").strip()
        )

    @staticmethod
    def _parse_raw_timestamp(value: object, path: Path) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            logger.warning("market_data_raw_timestamp_invalid", extra={"path": str(path)})
            return None
        try:
            return normalize_time(datetime.fromisoformat(value))
        except ValueError as exc:
            logger.warning("market_data_raw_timestamp_invalid", extra={"path": str(path), "error": str(exc)})
            return None

    @staticmethod
    def _raw_is_newer(
        *,
        raw_job_count: int,
        raw_latest_scraped_at: datetime | None,
        published_job_count: int,
        published_latest_scraped_at: datetime | None,
    ) -> bool:
        if raw_job_count <= 0:
            return False
        if published_job_count <= 0 or published_latest_scraped_at is None:
            return True
        if raw_latest_scraped_at is not None and raw_latest_scraped_at > published_latest_scraped_at:
            return True
        return raw_job_count != published_job_count

    @staticmethod
    def _breakdown(connection, field: str, total: int) -> list[MarketDataBreakdown]:
        # 字段名来自固定白名单，不接受请求参数，因此不会形成动态 SQL 注入入口。
        rows = connection.execute(
            f'''
            SELECT COALESCE(NULLIF(TRIM("{field}"), ''), '未标注') AS item_label,
                   COUNT(*)::BIGINT AS item_count
            FROM jobs
            GROUP BY 1
            ORDER BY item_count DESC, item_label ASC
            LIMIT 5
            '''
        ).fetchall()
        return [
            MarketDataBreakdown(
                label=str(label),
                count=int(count),
                ratio=(int(count) / total) if total else 0,
            )
            for label, count in rows
        ]

    @staticmethod
    def _as_utc(value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=timezone.utc)
