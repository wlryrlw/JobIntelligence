from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.crawling import (
    SCRAPER_PATH,
    CrawlRequest,
    CrawlRequestError,
    CrawlRunSnapshot,
    CrawlRunManager,
    CrawlRunStore,
)


def test_crawl_request_bounds_and_argv_whitelist(tmp_path: Path) -> None:
    request = CrawlRequest(
        keyword=" AI Agent ",
        city="上海",
        pages=2,
        max_details=10,
        filters={"salary": "406", "degree": "203"},
        confirmation=True,
        auto_close_chrome=True,
    )
    manager = CrawlRunManager(store=CrawlRunStore(tmp_path / "runtime"), scraper_path=SCRAPER_PATH)
    argv = manager.build_argv(request, tmp_path / "jobs.json", tmp_path / "details.json")
    assert argv[:4] == [argv[0], str(SCRAPER_PATH), "--keyword", "AI Agent"]
    assert "--format" in argv and "json" in argv
    assert "--output" in argv and str(tmp_path / "jobs.json") in argv
    assert "--detail-output" in argv and str(tmp_path / "details.json") in argv
    assert ["--salary", "406"] == argv[argv.index("--salary") : argv.index("--salary") + 2]
    assert ["--degree", "203"] == argv[argv.index("--degree") : argv.index("--degree") + 2]
    assert "--close-chrome" in argv
    assert "--cdp-port" not in argv
    assert "--allow-dom-fallback" not in argv
    assert "--merge" not in argv

    with pytest.raises(ValidationError):
        CrawlRequest(keyword="x", city="上海", pages=11)
    with pytest.raises(ValidationError):
        CrawlRequest(keyword="x", city="上海", max_details=201)


def test_crawl_store_replays_only_new_events(tmp_path: Path) -> None:
    from app.crawling import CrawlEvent
    from datetime import datetime, timezone

    store = CrawlRunStore(tmp_path / "runtime")
    now = datetime.now(timezone.utc)
    for sequence in (1, 2):
        store.append(CrawlEvent(event_id=f"run-{sequence}", run_id="run", sequence=sequence, event_type="crawl.progress", timestamp=now, payload={"sequence": sequence}))
    assert [event.sequence for event in store.events("run", after_sequence=1)] == [2]


def test_scraper_output_is_exposed_as_detailed_progress(tmp_path: Path) -> None:
    manager = CrawlRunManager(
        store=CrawlRunStore(tmp_path / "runtime"),
        scraper_path=SCRAPER_PATH,
    )
    snapshot = CrawlRunSnapshot(
        run_id="progress-run",
        owner_id="local_user",
        status="running",
        phase="list",
        request=CrawlRequest(keyword="AI Agent", city="上海", pages=3, confirmation=True),
        requested_pages=3,
    )

    manager._handle_progress(snapshot, "--- [2/3 页, 7 条已抓] ---", 0.0)
    assert snapshot.current_page == 2
    assert snapshot.completed_pages == 0
    assert snapshot.job_count == 7
    assert "第 2/3 页" in snapshot.current_message

    manager._handle_progress(snapshot, "  ✓ Agent Engineer | 30-50K | 上海 | 招聘者", 0.0)
    assert snapshot.job_count == 8
    assert snapshot.current_item == "Agent Engineer | 30-50K | 上海 | 招聘者"

    manager._handle_progress(snapshot, "  本页 10 条, 新增 9, 累计 16", 0.0)
    assert snapshot.completed_pages == 2
    assert snapshot.page_job_count == 10
    assert snapshot.new_job_count == 9
    assert snapshot.job_count == 16

    wait_info = manager._handle_progress(snapshot, "  翻页等待 12s...", 0.0)
    assert wait_info == (12, "翻页等待")
    assert snapshot.waiting_seconds_remaining == 12
    assert snapshot.waiting_seconds_total == 12

    manager._handle_progress(snapshot, "=== 抓取岗位详情 (16 个) ===", 0.0)
    manager._handle_progress(snapshot, "[3/16] Demo Company - Agent Engineer", 0.0)
    manager._handle_progress(snapshot, "  JD: 782 字 (8s)", 0.0)
    assert snapshot.detail_total == 16
    assert snapshot.current_detail == 3
    assert snapshot.detail_count == 1
    assert snapshot.waiting_seconds_remaining is None


@pytest.mark.asyncio
async def test_login_failure_becomes_blocked_without_scraper_retry(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def control(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        return 1, "未登录，请先在专用 Chrome 中登录"

    manager = CrawlRunManager(
        store=CrawlRunStore(tmp_path / "runtime"),
        scraper_path=SCRAPER_PATH,
        command_runner=control,
    )
    snapshot = await manager.create(
        CrawlRequest(keyword="AI Agent", city="上海", confirmation=True),
        owner_id="local_user",
    )
    await asyncio.sleep(0.05)
    blocked = manager.get(snapshot.run_id, owner_id="local_user")
    assert blocked is not None
    assert blocked.status == "blocked"
    assert blocked.failure is not None and blocked.failure.code == "login_required"
    assert calls == [["--check"]]
    assert [event.event_type for event in manager.events(snapshot.run_id, owner_id="local_user")] == [
        "crawl.created",
        "crawl.blocked",
    ]


@pytest.mark.asyncio
async def test_start_browser_uses_nonblocking_setup_with_login_page(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def control(args: list[str]) -> tuple[int, str]:
        calls.append(args)
        return 0, "已打开 BOSS 登录页"

    manager = CrawlRunManager(
        store=CrawlRunStore(tmp_path / "runtime"),
        scraper_path=SCRAPER_PATH,
        command_runner=control,
    )

    result = await manager.start_browser(confirmation=True)

    assert result.status == "started"
    assert "打开 BOSS 登录页" in result.message
    assert calls == [["--setup-chrome", "--no-wait-login"]]


@pytest.mark.asyncio
async def test_confirmation_is_required_before_side_effect(tmp_path: Path) -> None:
    manager = CrawlRunManager(store=CrawlRunStore(tmp_path / "runtime"), scraper_path=SCRAPER_PATH)
    with pytest.raises(CrawlRequestError, match="确认"):
        await manager.create(CrawlRequest(keyword="AI", city="上海"), owner_id="local_user")


def test_chat_crawl_request_requires_confirmation_and_returns_run_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi.testclient import TestClient

    from app.api import routes
    from app.main import app

    async def fake_create(request: CrawlRequest, *, owner_id: str) -> CrawlRunSnapshot:
        now = datetime.now(timezone.utc)
        return CrawlRunSnapshot(
            run_id="chat-run-001",
            owner_id=owner_id,
            status="queued",
            phase="preflight",
            request=request,
            requested_pages=request.pages,
            started_at=now,
            updated_at=now,
            sequence=1,
            current_message="已排队",
        )

    monkeypatch.setattr(routes.service.crawl_manager, "create", fake_create)
    session_id = "chat-crawl-confirmation-test"
    with TestClient(app) as client:
        pending = client.post("/api/query", json={"session_id": session_id, "query": "请抓取上海的 AI Agent 岗位，最多 2 页"})
        assert pending.status_code == 200
        assert pending.json()["status"] == "waiting_clarification"
        assert pending.json()["route"] == "market_data_refresh"
        started = client.post("/api/query", json={"session_id": session_id, "query": "开始采集"})
        assert started.status_code == 200
        assert started.json()["status"] == "completed"
        assert started.json()["crawl_run_id"] == "chat-run-001"
