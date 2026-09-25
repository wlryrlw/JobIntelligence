from __future__ import annotations

"""受控的 BOSS 岗位采集编排。

本模块只负责任务契约、进程生命周期、进度事件和 ETL 编排；Chrome/CDP
行为仍由 integrations/boss_zhipin 中的 scraper 实现。所有外部输入在这里
先通过 Pydantic 校验，worker 只以参数数组启动固定脚本，不接受 shell 字符串。
"""

import asyncio
import json
import os
import re
import signal
import sys
import time
import uuid
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import BASE_DIR
from .contracts import CrawlStatus
from .etl.models import QualityReport
from .etl.pipeline import EtlError, run_etl


PROJECT_ROOT = BASE_DIR.parent
SCRAPER_PATH = PROJECT_ROOT / "integrations" / "boss_zhipin" / "scripts" / "boss_cdp_raw.py"
CITY_CODES_PATH = PROJECT_ROOT / "integrations" / "boss_zhipin" / "data" / "city_codes.json"
RESULT_ROOT = PROJECT_ROOT / "data" / "local" / "boss_zhipin" / "job-result"
DATABASE_ROOT = PROJECT_ROOT / "data" / "databases"
ETL_RAW_ROOT = PROJECT_ROOT / "data" / "local" / "etl"
RUNTIME_ROOT = BASE_DIR / "data" / "local" / "runtime" / "crawl"
MAX_DIAGNOSTIC_CHARS = 4000
MAX_SCRAPER_SECONDS = 4 * 60 * 60

FilterCode = Literal["scale", "stage", "salary", "experience", "degree", "industry"]


class CrawlFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scale: str | None = Field(default=None, max_length=20)
    stage: str | None = Field(default=None, max_length=20)
    salary: str | None = Field(default=None, max_length=20)
    experience: str | None = Field(default=None, max_length=20)
    degree: str | None = Field(default=None, max_length=20)
    industry: str | None = Field(default=None, max_length=20)


class CrawlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keyword: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=40)
    pages: int = Field(default=3, ge=1, le=10)
    detail: bool = True
    max_details: int | None = Field(default=None, ge=1, le=200)
    filters: CrawlFilters = Field(default_factory=CrawlFilters)
    source: Literal["chat", "frontend"] = "frontend"
    confirmation: bool = False
    auto_close_chrome: bool = False
    run_smoke_test: bool = False

    @field_validator("keyword", "city")
    @classmethod
    def strip_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("关键词和城市不能为空")
        return value

    @field_validator("city")
    @classmethod
    def validate_city(cls, value: str) -> str:
        if value.isdigit() and value not in _city_codes().values():
            raise ValueError("城市代码不在 BOSS 城市码表中")
        if not value.isdigit() and value not in _city_codes():
            raise ValueError("城市不在 BOSS 城市码表中")
        return value

    @model_validator(mode="after")
    def validate_filter_codes(self) -> "CrawlRequest":
        allowed = {
            "scale": {"301", "302", "303", "304", "305", "306"},
            "stage": {"801", "802", "803", "804", "805", "806", "807", "808"},
            "salary": {"402", "403", "404", "405", "406", "407"},
            "experience": {"101", "102", "103", "104", "105", "106", "107", "108"},
            "degree": {"202", "203", "204", "205", "206", "208", "209"},
        }
        for name, values in allowed.items():
            value = getattr(self.filters, name)
            if value and value not in values:
                raise ValueError(f"筛选参数 {name} 不是受支持的代码")
        industry = self.filters.industry
        if industry and not re.fullmatch(r"\d{4,6}", industry):
            raise ValueError("行业筛选代码格式无效")
        return self


@lru_cache(maxsize=1)
def _city_codes() -> dict[str, str]:
    try:
        values = json.loads(CITY_CODES_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError("无法加载 BOSS 城市码表") from exc
    if not isinstance(values, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in values.items()):
        raise ValueError("BOSS 城市码表格式无效")
    return values


class CrawlIntentDraft(BaseModel):
    """对话入口的保守抽取结果；缺字段时只用于澄清，不会启动采集。"""

    model_config = ConfigDict(extra="forbid")

    keyword: str | None = Field(default=None, max_length=200)
    city: str | None = Field(default=None, max_length=40)
    pages: int = Field(default=3, ge=1, le=10)
    detail: bool = True


def parse_crawl_intent(text: str) -> CrawlIntentDraft | None:
    """从明确的中文采集请求提取最小参数，不把普通查询误判为爬虫。"""

    if not any(token in text for token in ("抓取", "爬取", "采集", "更新岗位", "刷新岗位", "重新搜索岗位")):
        return None
    city: str | None = None
    try:
        values = json.loads(CITY_CODES_PATH.read_text(encoding="utf-8"))
        city_names = sorted((str(name) for name in values), key=len, reverse=True)
        city = next((name for name in city_names if name in text and name != "全国"), None)
    except (OSError, json.JSONDecodeError):
        city = None
    code = re.search(r"\b(10\d{7})\b", text)
    city = city or (code.group(1) if code else None)
    quoted = re.search(r"[\"“「]([^\"”」]{1,80})[\"”」]", text)
    keyword = quoted.group(1).strip() if quoted else None
    if not keyword:
        known = re.search(r"(?i)\b(ai\s*agent|agent|python|java|go|rust|rag|llm|golang)\b", text)
        keyword = known.group(1).strip() if known else None
    if not keyword:
        between = re.search(r"(?:的|搜索|抓取|采集|爬取)\s*([\u4e00-\u9fffA-Za-z0-9+#./ -]{1,40}?)\s*(?:岗位|职位)", text)
        keyword = between.group(1).strip() if between else None
    pages_match = re.search(r"(\d+)\s*页", text)
    detail = not any(token in text for token in ("不含详情", "不抓详情", "不需要详情", "不要详情"))
    requested_pages = int(pages_match.group(1)) if pages_match else 3
    return CrawlIntentDraft(keyword=keyword, city=city, pages=min(10, max(1, requested_pages)), detail=detail)


class CrawlPreflightRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_smoke_test: bool = False


class CrawlBrowserStartRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    confirmation: bool = False


class CrawlWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str


class CrawlFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    diagnostic: str | None = None


class CrawlRunSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str
    owner_id: str
    status: Literal[
        "queued", "running", "blocked", "etl_running", "succeeded", "failed", "cancelled"
    ]
    phase: Literal["preflight", "login", "list", "detail", "etl", "publish", "terminal"]
    request: CrawlRequest
    requested_pages: int
    completed_pages: int = 0
    current_page: int | None = None
    page_job_count: int = 0
    new_job_count: int = 0
    job_count: int = 0
    detail_total: int | None = None
    current_detail: int | None = None
    detail_count: int = 0
    current_item: str | None = None
    waiting_seconds_remaining: int | None = None
    waiting_seconds_total: int | None = None
    waiting_reason: str | None = None
    elapsed_seconds: float = 0
    current_message: str = ""
    warnings: list[CrawlWarning] = Field(default_factory=list)
    dataset_version: str | None = None
    scraper_version: str = "unknown"
    etl_version: str | None = None
    failure: CrawlFailure | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    sequence: int = 0


class CrawlEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    run_id: str
    sequence: int
    event_type: str
    timestamp: datetime
    payload: dict[str, object] = Field(default_factory=dict)


class CrawlPreflightResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ready", "cdp_unavailable", "login_required", "risk_control", "failed"]
    message: str
    check_passed: bool = False
    smoke_test_passed: bool | None = None


class CrawlBrowserResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["started", "stopped", "failed"]
    message: str


class CrawlOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cities: list[str]
    max_pages: int = 10
    max_details: int = 200
    filters: dict[str, list[dict[str, str]]]


class CrawlRequestError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class CrawlRunStore:
    """与已有 Runtime Store 相同的本地原子快照 + append-only 事件存储。"""

    def __init__(self, root: Path = RUNTIME_ROOT) -> None:
        self.root = root
        self.snapshot_dir = root / "runs"
        self.event_dir = root / "events"
        self.snapshot_dir.mkdir(parents=True, exist_ok=True)
        self.event_dir.mkdir(parents=True, exist_ok=True)

    def save(self, snapshot: CrawlRunSnapshot) -> None:
        target = self.snapshot_dir / f"{snapshot.run_id}.json"
        temp = target.with_suffix(".json.tmp")
        temp.write_text(snapshot.model_dump_json(), encoding="utf-8")
        os.replace(temp, target)

    def get(self, run_id: str) -> CrawlRunSnapshot | None:
        path = self.snapshot_dir / f"{run_id}.json"
        if not path.exists():
            return None
        try:
            return CrawlRunSnapshot.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"采集任务快照损坏：{run_id}") from exc

    def append(self, event: CrawlEvent) -> None:
        path = self.event_dir / f"{event.run_id}.jsonl"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(event.model_dump_json() + "\n")

    def events(self, run_id: str, after_sequence: int = 0) -> list[CrawlEvent]:
        path = self.event_dir / f"{run_id}.jsonl"
        if not path.exists():
            return []
        events: list[CrawlEvent] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            event = CrawlEvent.model_validate_json(line)
            if event.sequence > after_sequence:
                events.append(event)
        return events


class CrawlRunManager:
    """单机单 profile 的采集任务管理器。"""

    def __init__(
        self,
        *,
        store: CrawlRunStore | None = None,
        scraper_path: Path = SCRAPER_PATH,
        command_runner: Callable[..., Awaitable[tuple[int, str]]] | None = None,
    ) -> None:
        self.store = store or CrawlRunStore()
        self.scraper_path = scraper_path.resolve()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._processes: dict[str, asyncio.subprocess.Process] = {}
        self._active_run_id: str | None = None
        self._lock = asyncio.Lock()
        self._command_runner = command_runner

    def options(self) -> CrawlOptions:
        try:
            values = json.loads(CITY_CODES_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError("无法加载 BOSS 城市码表") from exc
        if not isinstance(values, dict):
            raise RuntimeError("BOSS 城市码表格式无效")
        filters = {
            "scale": [
                {"code": "301", "label": "0-20人"}, {"code": "302", "label": "20-99人"},
                {"code": "303", "label": "100-499人"}, {"code": "304", "label": "500-999人"},
                {"code": "305", "label": "1000-9999人"}, {"code": "306", "label": "10000人以上"},
            ],
            "stage": [{"code": str(code), "label": label} for code, label in ((801, "未融资"), (802, "天使轮"), (803, "A轮"), (804, "B轮"), (805, "C轮"), (806, "D轮及以上"), (807, "已上市"), (808, "不需要融资"))],
            "salary": [{"code": str(code), "label": label} for code, label in ((402, "3K以下"), (403, "3-5K"), (404, "5-10K"), (405, "10-20K"), (406, "20-50K"), (407, "50K以上"))],
            "experience": [{"code": str(code), "label": label} for code, label in ((108, "在校生"), (102, "应届生"), (101, "经验不限"), (103, "1年以内"), (104, "1-3年"), (105, "3-5年"), (106, "5-10年"), (107, "10年以上"))],
            "degree": [{"code": str(code), "label": label} for code, label in ((209, "初中及以下"), (208, "中专/中技"), (206, "高中"), (202, "大专"), (203, "本科"), (204, "硕士"), (205, "博士"))],
            "industry": [{"code": str(code), "label": label} for code, label in ((1001, "互联网"), (1002, "电子商务"), (1003, "金融"), (1004, "教育培训"), (1005, "医疗健康"))],
        }
        return CrawlOptions(cities=list(values), filters=filters)

    async def preflight(self, *, run_smoke_test: bool = False) -> CrawlPreflightResult:
        try:
            check_code, check_output = await self._run_control(["--check"])
        except OSError as exc:
            return CrawlPreflightResult(status="failed", message="无法启动 scraper，请检查 Python 依赖和脚本路径", check_passed=False)
        if check_code != 0:
            status = self._classify_output(check_output)
            return CrawlPreflightResult(
                status=status,
                message=self._preflight_message(status),
                check_passed=False,
            )
        if not run_smoke_test:
            return CrawlPreflightResult(status="ready", message="Chrome CDP、依赖和登录态检查通过", check_passed=True)
        try:
            smoke_code, smoke_output = await self._run_control(["--smoke-test"])
        except OSError:
            return CrawlPreflightResult(status="failed", message="无法启动 smoke test，请检查 Python 依赖和脚本路径", check_passed=True, smoke_test_passed=False)
        if smoke_code != 0:
            status = self._classify_output(smoke_output)
            return CrawlPreflightResult(
                status=status,
                message=self._preflight_message(status),
                check_passed=True,
                smoke_test_passed=False,
            )
        return CrawlPreflightResult(
            status="ready", message="环境检查和 BOSS 搜索 smoke test 均通过", check_passed=True, smoke_test_passed=True
        )

    async def start_browser(self, *, confirmation: bool) -> CrawlBrowserResult:
        if not confirmation:
            raise CrawlRequestError("confirmation_required", "启动专用 Chrome 前需要明确确认")
        try:
            code, _ = await self._run_control(["--setup-chrome", "--no-wait-login"])
        except OSError:
            return CrawlBrowserResult(status="failed", message="无法启动 scraper，请检查 Python 依赖和脚本路径")
        if code != 0:
            return CrawlBrowserResult(status="failed", message="专用 Chrome 启动失败，请查看本地诊断或手动运行 --setup-chrome")
        return CrawlBrowserResult(status="started", message="专用 Chrome CDP 已启动，并已打开 BOSS 登录页；请在该窗口中完成登录后再检查环境")

    async def stop_browser(self) -> CrawlBrowserResult:
        try:
            code, _ = await self._run_control(["--stop-chrome"])
        except OSError:
            return CrawlBrowserResult(status="failed", message="无法启动 scraper，请检查 Python 依赖和脚本路径")
        if code != 0:
            return CrawlBrowserResult(status="failed", message="关闭专用 Chrome 失败，请确认 scraper 环境可用")
        return CrawlBrowserResult(status="stopped", message="已关闭 BOSS 专用 Chrome，不影响主 Chrome")

    async def create(self, request: CrawlRequest, *, owner_id: str) -> CrawlRunSnapshot:
        if not request.confirmation:
            raise CrawlRequestError("confirmation_required", "创建采集任务前需要确认将访问 BOSS 直聘")
        async with self._lock:
            if self._active_run_id:
                current = self.store.get(self._active_run_id)
                if current and current.status in {"queued", "running", "blocked", "etl_running"}:
                    raise CrawlRequestError("active_run_exists", f"已有采集任务运行中：{current.run_id}")
                self._active_run_id = None
            run_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc)
            snapshot = CrawlRunSnapshot(
                run_id=run_id,
                owner_id=owner_id,
                status=CrawlStatus.QUEUED.value,
                phase="preflight",
                request=request,
                requested_pages=request.pages,
                started_at=now,
                updated_at=now,
                current_message="已排队，准备检查 Chrome CDP 和登录态",
                sequence=1,
            )
            self.store.save(snapshot)
            self._active_run_id = run_id
            self._emit(snapshot, "crawl.created", {"message": snapshot.current_message})
            self._tasks[run_id] = asyncio.create_task(self._run(run_id), name=f"crawl-{run_id}")
            return snapshot

    def get(self, run_id: str, *, owner_id: str) -> CrawlRunSnapshot | None:
        snapshot = self.store.get(run_id)
        if snapshot and snapshot.owner_id != owner_id:
            raise PermissionError("无权访问该采集任务")
        return snapshot

    def events(self, run_id: str, *, owner_id: str, after_sequence: int = 0) -> list[CrawlEvent]:
        snapshot = self.get(run_id, owner_id=owner_id)
        if snapshot is None:
            return []
        return self.store.events(run_id, after_sequence=after_sequence)

    async def cancel(self, run_id: str, *, owner_id: str) -> CrawlRunSnapshot:
        snapshot = self.get(run_id, owner_id=owner_id)
        if snapshot is None:
            raise KeyError(run_id)
        if snapshot.status in {"succeeded", "failed", "cancelled"}:
            return snapshot
        process = self._processes.get(run_id)
        if process and process.returncode is None:
            self._terminate_process(process)
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        return self._update(
            snapshot,
            status="cancelled",
            phase="terminal",
            message="已取消采集任务，既有数据集保持不变",
            event_type="crawl.cancelled",
        )

    async def resume(self, run_id: str, *, owner_id: str) -> CrawlRunSnapshot:
        snapshot = self.get(run_id, owner_id=owner_id)
        if snapshot is None:
            raise KeyError(run_id)
        if snapshot.status != "blocked":
            raise CrawlRequestError("not_resumable", "只有等待人工处理的阻塞任务可以恢复")
        async with self._lock:
            if self._active_run_id and self._active_run_id != run_id:
                raise CrawlRequestError("active_run_exists", f"已有采集任务运行中：{self._active_run_id}")
            self._active_run_id = run_id
            resumed = self._update(snapshot, status="queued", phase="preflight", message="已请求恢复，重新检查环境", event_type="crawl.resumed")
            self._tasks[run_id] = asyncio.create_task(self._run(run_id), name=f"crawl-{run_id}-resume")
            return resumed

    async def shutdown(self) -> None:
        for process in list(self._processes.values()):
            if process.returncode is None:
                self._terminate_process(process)
        tasks = [task for task in self._tasks.values() if not task.done()]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run(self, run_id: str) -> None:
        snapshot = self.store.get(run_id)
        if snapshot is None:
            return
        started = time.monotonic()
        try:
            preflight = await self.preflight(run_smoke_test=snapshot.request.run_smoke_test)
            if preflight.status != "ready":
                self._update(
                    snapshot,
                    status="blocked" if preflight.status in {"login_required", "cdp_unavailable", "risk_control"} else "failed",
                    phase="login" if preflight.status == "login_required" else "preflight",
                    message=preflight.message,
                    failure=CrawlFailure(code=preflight.status, message=preflight.message),
                    event_type="crawl.blocked" if preflight.status in {"login_required", "cdp_unavailable", "risk_control"} else "crawl.failed",
                )
                return
            snapshot = self._update(snapshot, status="running", phase="list", message="环境检查通过，开始采集岗位列表", event_type="crawl.started")
            jobs_path, details_path = self._paths(run_id, snapshot.request.detail)
            argv = self.build_argv(snapshot.request, jobs_path, details_path)
            code, diagnostic = await asyncio.wait_for(
                self._run_scraper(run_id, argv, snapshot, started),
                timeout=MAX_SCRAPER_SECONDS,
            )
            if code != 0:
                status = self._classify_output(diagnostic)
                blocked = status in {"login_required", "cdp_unavailable", "risk_control"}
                self._update(
                    snapshot,
                    status="blocked" if blocked else "failed",
                    phase="login" if status == "login_required" else "terminal",
                    message=self._preflight_message(status) if blocked else "scraper 执行失败，未发布新数据",
                    failure=CrawlFailure(code=status if blocked else "worker_crashed", message=self._preflight_message(status) if blocked else "scraper 非正常退出", diagnostic=diagnostic[-1000:]),
                    event_type="crawl.blocked" if blocked else "crawl.failed",
                )
                return
            if not jobs_path.exists():
                raise CrawlRequestError("empty_result", "scraper 未生成岗位列表文件，未发布新数据")
            snapshot = self._update(snapshot, status="etl_running", phase="etl", message="采集完成，正在校验并发布岗位数据", event_type="crawl.etl_started")
            report = await asyncio.to_thread(run_etl, jobs_path, details_path if snapshot.request.detail else None, DATABASE_ROOT, ETL_RAW_ROOT)
            self._update(
                snapshot,
                status="succeeded",
                phase="terminal",
                message=f"数据集已发布：{report.dataset_version}",
                job_count=report.total_jobs,
                detail_count=report.input_details - report.rejected_details - report.orphan_details,
                dataset_version=report.dataset_version,
                etl_version="1.0.0",
                warnings=[CrawlWarning(code="detail_partial", message=f"有 {report.missing_detail_count} 个岗位缺少有效 JD")]
                if snapshot.request.detail and report.missing_detail_count
                else [],
                event_type="crawl.dataset_published",
            )
        except asyncio.TimeoutError:
            process = self._processes.get(run_id)
            if process and process.returncode is None:
                self._terminate_process(process)
            self._update(snapshot, status="failed", phase="terminal", message="scraper 超过最长运行时间，未发布新数据", failure=CrawlFailure(code="worker_timeout", message="scraper 超过最长运行时间，未发布新数据"), event_type="crawl.failed")
        except asyncio.CancelledError:
            raise
        except CrawlRequestError as exc:
            self._update(snapshot, status="failed", phase="terminal", message=exc.message, failure=CrawlFailure(code=exc.code, message=exc.message), event_type="crawl.failed")
        except (EtlError, OSError, ValueError, RuntimeError) as exc:
            message = "ETL 或数据发布失败，旧数据集保持不变"
            self._update(snapshot, status="failed", phase="terminal", message=message, failure=CrawlFailure(code="etl_failed", message=message, diagnostic=str(exc)[:1000]), event_type="crawl.failed")
        finally:
            self._processes.pop(run_id, None)
            self._tasks.pop(run_id, None)
            current = self.store.get(run_id)
            if current:
                current.elapsed_seconds = round(time.monotonic() - started, 1)
                current.updated_at = datetime.now(timezone.utc)
                self.store.save(current)
            if self._active_run_id == run_id:
                self._active_run_id = None

    def build_argv(self, request: CrawlRequest, jobs_path: Path, details_path: Path | None) -> list[str]:
        if self.scraper_path != SCRAPER_PATH.resolve() and not self.scraper_path.is_file():
            raise CrawlRequestError("worker_unavailable", "scraper 路径不存在")
        argv = [sys.executable, str(self.scraper_path), "--keyword", request.keyword, "--city", request.city, "--pages", str(request.pages), "--format", "json", "--output", str(jobs_path)]
        if request.detail and details_path:
            argv.extend(["--detail-output", str(details_path)])
        else:
            argv.append("--no-detail")
        if request.max_details is not None:
            argv.extend(["--max-details", str(request.max_details)])
        for name in ("scale", "stage", "salary", "experience", "degree", "industry"):
            value = getattr(request.filters, name)
            if value:
                argv.extend([f"--{name}", value])
        if request.auto_close_chrome:
            argv.append("--close-chrome")
        return argv

    async def _run_scraper(self, run_id: str, argv: list[str], snapshot: CrawlRunSnapshot, started: float) -> tuple[int, str]:
        scraper_env = os.environ.copy()
        # scraper 的命令行提示本身就是进度协议；关闭 pipe 缓冲才能让每行
        # 在产生后立即进入 SSE，而不是等到进程结束或缓冲区填满。
        scraper_env["PYTHONUNBUFFERED"] = "1"
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            env=scraper_env,
        )
        self._processes[run_id] = process
        lines: list[str] = []
        if process.stdout is None:
            raise RuntimeError("无法读取 scraper 输出")
        wait_deadline: float | None = None
        wait_total: int | None = None
        wait_reason: str | None = None
        last_wait_remaining: int | None = None
        last_output_at = time.monotonic()
        last_heartbeat_at = last_output_at
        while True:
            try:
                raw = await asyncio.wait_for(process.stdout.readline(), timeout=1.0)
            except asyncio.TimeoutError:
                now = time.monotonic()
                if wait_deadline is not None and wait_total is not None and wait_reason:
                    remaining = max(0, int((wait_deadline - now) + 0.999))
                    if remaining != last_wait_remaining:
                        self._update(
                            snapshot,
                            status="running",
                            phase=snapshot.phase,
                            message=f"{wait_reason}，剩余约 {remaining} 秒",
                            elapsed_seconds=round(now - started, 1),
                            waiting_seconds_remaining=remaining,
                            waiting_seconds_total=wait_total,
                            waiting_reason=wait_reason,
                            event_type="crawl.waiting",
                        )
                        last_wait_remaining = remaining
                    if remaining == 0:
                        wait_deadline = None
                        wait_total = None
                        wait_reason = None
                elif now - last_heartbeat_at >= 5:
                    silent_seconds = int(now - last_output_at)
                    self._update(
                        snapshot,
                        status="running",
                        phase=snapshot.phase,
                        message=f"正在处理当前阶段，已等待 {silent_seconds} 秒…",
                        elapsed_seconds=round(now - started, 1),
                        event_type="crawl.heartbeat",
                    )
                    last_heartbeat_at = now
                continue
            if not raw:
                break
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                lines.append(line)
                if len(lines) > 100:
                    lines.pop(0)
                wait_info = self._handle_progress(snapshot, line, started)
                if wait_info:
                    wait_total, wait_reason = wait_info
                    wait_deadline = time.monotonic() + wait_total
                    last_wait_remaining = wait_total
                else:
                    wait_deadline = None
                    wait_total = None
                    wait_reason = None
                    last_wait_remaining = None
                last_output_at = time.monotonic()
                last_heartbeat_at = last_output_at
        code = await process.wait()
        diagnostic = "\n".join(lines)[-MAX_DIAGNOSTIC_CHARS:]
        run_dir = RESULT_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "diagnostic.log").write_text(diagnostic, encoding="utf-8")
        return code, diagnostic

    def _handle_progress(self, snapshot: CrawlRunSnapshot, line: str, started: float) -> tuple[int, str] | None:
        elapsed_seconds = round(time.monotonic() - started, 1)
        page = re.search(r"---\s*\[(\d+)\/(\d+)\s*页", line)
        page_summary = re.search(r"本页\s*(\d+)\s*条,\s*新增\s*(\d+),\s*累计\s*(\d+)", line)
        detail_header = re.search(r"抓取岗位详情\s*\((\d+)\s*个\)", line)
        detail_item = re.search(r"\[(\d+)\/(\d+)\]\s*(.*)$", line)
        completed_detail = re.search(r"JD:\s*(\d+)\s*字", line)
        wait_seconds = re.search(r"等待\s+(\d+(?:\.\d+)?)s", line)
        job_line = line.lstrip().startswith("✓ ")
        waiting = "等待" in line or "翻页等待" in line
        safe = self._safe_progress_text(line)
        if page:
            current_page = int(page.group(1))
            requested_pages = int(page.group(2))
            already_collected = re.search(r"(\d+)\s*条已抓", line)
            self._update(
                snapshot,
                status="running",
                phase="list",
                message=f"正在采集第 {current_page}/{requested_pages} 页（已完成 {snapshot.completed_pages}/{requested_pages} 页）",
                current_page=current_page,
                page_job_count=0,
                new_job_count=0,
                job_count=int(already_collected.group(1)) if already_collected else snapshot.job_count,
                current_item=None,
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.list_started",
            )
        elif page_summary:
            page_jobs, new_jobs, total_jobs = (int(value) for value in page_summary.groups())
            self._update(
                snapshot,
                status="running",
                phase="list",
                message=f"第 {snapshot.current_page or snapshot.completed_pages}/{snapshot.requested_pages} 页完成：本页 {page_jobs} 条，新增 {new_jobs} 条，累计 {total_jobs} 条",
                completed_pages=snapshot.current_page or snapshot.completed_pages,
                page_job_count=page_jobs,
                new_job_count=new_jobs,
                job_count=total_jobs,
                current_item=None,
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.list_progress",
            )
        elif job_line:
            current_item = safe[2:].strip() if safe.startswith("✓ ") else safe
            self._update(
                snapshot,
                status="running",
                phase="list",
                message=f"已采集岗位 {snapshot.job_count + 1} 条：{current_item}",
                job_count=snapshot.job_count + 1,
                current_item=current_item[:180],
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.job_progress",
            )
        elif detail_header:
            total_details = int(detail_header.group(1))
            self._update(
                snapshot,
                status="running",
                phase="detail",
                message=f"准备采集岗位详情：共 {total_details} 个岗位",
                detail_total=total_details,
                current_detail=0,
                current_item=None,
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.detail_started",
            )
        elif detail_item:
            current_detail, total_details = int(detail_item.group(1)), int(detail_item.group(2))
            current_item = detail_item.group(3).strip()
            self._update(
                snapshot,
                status="running",
                phase="detail",
                message=f"正在采集详情 {current_detail}/{total_details}：{current_item}",
                detail_total=total_details,
                current_detail=current_detail,
                current_item=current_item[:180],
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.detail_item_started",
            )
        elif completed_detail:
            completed = snapshot.detail_count + 1
            self._update(
                snapshot,
                status="running",
                phase="detail",
                message=f"详情 {snapshot.current_detail or completed}/{snapshot.detail_total or '?'} 已完成，JD {completed_detail.group(1)} 字",
                detail_count=completed,
                current_item=None,
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.detail_progress",
            )
        elif waiting:
            seconds = int(float(wait_seconds.group(1))) if wait_seconds else None
            if "翻页等待" in line:
                reason = "翻页等待"
            elif "后抓下一个" in line:
                reason = "详情间隔等待"
            else:
                reason = "页面加载等待"
            self._update(
                snapshot,
                status="running",
                phase=snapshot.phase,
                message=f"{reason}，剩余约 {seconds} 秒" if seconds is not None else safe or "正在等待页面加载或详情间隔",
                current_item=None,
                waiting_seconds_remaining=seconds,
                waiting_seconds_total=seconds,
                waiting_reason=reason,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.waiting",
            )
            return (seconds, reason) if seconds is not None else None
        elif "无数据" in line:
            self._update(
                snapshot,
                status="running",
                phase="list",
                message=f"第 {snapshot.current_page or snapshot.completed_pages}/{snapshot.requested_pages} 页无数据，继续处理",
                page_job_count=0,
                new_job_count=0,
                current_item=None,
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.list_progress",
            )
        elif "详情已保存" in line:
            self._update(
                snapshot,
                status="running",
                phase="detail",
                message="详情采集完成，准备进入 ETL",
                current_item=None,
                waiting_seconds_remaining=None,
                waiting_seconds_total=None,
                waiting_reason=None,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.detail_progress",
            )
        elif safe:
            self._update(
                snapshot,
                status="running",
                phase=snapshot.phase,
                message=safe,
                elapsed_seconds=elapsed_seconds,
                event_type="crawl.progress",
            )
        return None

    @staticmethod
    def _safe_progress_text(line: str) -> str:
        return re.sub(r"(?:/Users|/private|[A-Za-z]:\\)[^\s]+", "[本地路径]", line).strip()[:240]

    def _paths(self, run_id: str, detail: bool) -> tuple[Path, Path | None]:
        run_dir = RESULT_ROOT / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        return run_dir / "boss_jobs.json", run_dir / "boss_details.json" if detail else None

    async def _run_control(self, args: list[str]) -> tuple[int, str]:
        if self._command_runner:
            return await self._command_runner(args)
        process = await asyncio.create_subprocess_exec(sys.executable, str(self.scraper_path), *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        stdout, _ = await process.communicate()
        return process.returncode or 0, stdout.decode("utf-8", errors="replace")[-MAX_DIAGNOSTIC_CHARS:]

    def _update(self, snapshot: CrawlRunSnapshot, *, status: str, phase: str, message: str, event_type: str, failure: CrawlFailure | None = None, **fields: object) -> CrawlRunSnapshot:
        snapshot.status = status  # type: ignore[assignment]
        snapshot.phase = phase  # type: ignore[assignment]
        snapshot.current_message = message
        snapshot.failure = failure
        for key, value in fields.items():
            setattr(snapshot, key, value)
        if status != "running":
            snapshot.waiting_seconds_remaining = None
            snapshot.waiting_seconds_total = None
            snapshot.waiting_reason = None
        snapshot.updated_at = datetime.now(timezone.utc)
        snapshot.sequence += 1
        self.store.save(snapshot)
        self._emit(snapshot, event_type, {
            "status": status,
            "phase": phase,
            "message": message,
            "completed_pages": snapshot.completed_pages,
            "requested_pages": snapshot.requested_pages,
            "current_page": snapshot.current_page,
            "page_job_count": snapshot.page_job_count,
            "new_job_count": snapshot.new_job_count,
            "job_count": snapshot.job_count,
            "detail_total": snapshot.detail_total,
            "current_detail": snapshot.current_detail,
            "detail_count": snapshot.detail_count,
            "current_item": snapshot.current_item,
            "waiting_seconds_remaining": snapshot.waiting_seconds_remaining,
            "waiting_seconds_total": snapshot.waiting_seconds_total,
            "waiting_reason": snapshot.waiting_reason,
            "elapsed_seconds": snapshot.elapsed_seconds,
            "dataset_version": snapshot.dataset_version,
            "failure": snapshot.failure.model_dump(mode="json") if snapshot.failure else None,
        })
        return snapshot

    def _emit(self, snapshot: CrawlRunSnapshot, event_type: str, payload: dict[str, object]) -> None:
        self.store.append(CrawlEvent(event_id=f"{snapshot.run_id}-{snapshot.sequence}", run_id=snapshot.run_id, sequence=snapshot.sequence, event_type=event_type, timestamp=datetime.now(timezone.utc), payload=payload))

    @staticmethod
    def _classify_output(output: str) -> Literal["cdp_unavailable", "login_required", "risk_control", "failed"]:
        lowered = output.lower()
        if any(token in output for token in ("登录", "未登录", "login")):
            return "login_required"
        if any(token in output for token in ("风控", "验证", "安全校验", "code 37", "risk")):
            return "risk_control"
        if any(token in output for token in ("cdp", "端口", "chrome", "无法连接")):
            return "cdp_unavailable"
        return "failed"

    @staticmethod
    def _preflight_message(status: str) -> str:
        return {"ready": "环境检查通过", "cdp_unavailable": "未检测到可用 Chrome CDP，请先启动专用 Chrome", "login_required": "专用 Chrome 尚未完成 BOSS 登录，请人工登录后重新检查", "risk_control": "检测到登录验证或风控页面，请人工处理后重新检查", "failed": "环境检查失败，请查看本地诊断"}.get(status, "采集环境不可用")

    @staticmethod
    def _terminate_process(process: asyncio.subprocess.Process) -> None:
        try:
            if process.pid and hasattr(os, "killpg"):
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
        except ProcessLookupError:
            return
