from __future__ import annotations

import json
from pathlib import Path

from app.etl.pipeline import run_etl
from app.market_data import MarketDataSummaryService


def publish_fixture(tmp_path: Path) -> Path:
    source = tmp_path / "jobs.json"
    source.write_text(
        json.dumps(
            {
                "scraped_at": "2026-09-23T12:00:00+08:00",
                "keyword": "AI Agent",
                "city": "上海",
                "status": "succeeded",
                "jobs": [
                    {
                        "job_id": "one",
                        "title": "Agent 工程师",
                        "salary": "20-30K",
                        "salary_source": "fixture",
                        "location": "上海·徐汇",
                        "company_industry": "互联网",
                        "tags": "1-3年 | 本科",
                    },
                    {
                        "job_id": "two",
                        "title": "平台工程师",
                        "salary": "25K",
                        "salary_source": "fixture",
                        "location": "上海·浦东",
                        "company_industry": "互联网",
                        "tags": "经验不限 | 硕士",
                    },
                    {
                        "job_id": "three",
                        "title": "数据工程师",
                        "salary": "18K",
                        "salary_source": "fixture",
                        "location": "杭州·西湖",
                        "company_industry": "金融",
                        "tags": "1-3年 | 本科",
                    },
                ],
                "details": [{"job_id": "one", "jd": "负责 Agent 平台开发"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    database_root = tmp_path / "databases"
    run_etl(source, None, database_root, tmp_path / "local" / "etl")
    raw_root = tmp_path / "local" / "boss_zhipin" / "job-result"
    raw_root.mkdir(parents=True)
    payload = json.loads(source.read_text(encoding="utf-8"))
    (raw_root / "boss_jobs_20260923_1200.json").write_text(
        json.dumps({"scraped_at": payload["scraped_at"], "jobs": payload["jobs"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    (raw_root / "boss_details_20260923_1201.json").write_text(
        json.dumps(payload["details"], ensure_ascii=False), encoding="utf-8"
    )
    return database_root


def test_summary_reports_missing_published_snapshot(tmp_path: Path) -> None:
    summary = MarketDataSummaryService(tmp_path / "databases").get()

    assert summary.available is False
    assert summary.job_count == 0
    assert summary.top_cities == []


def test_summary_distinguishes_raw_result_from_unpublished_snapshot(tmp_path: Path) -> None:
    raw_root = tmp_path / "local" / "boss_zhipin" / "job-result"
    raw_root.mkdir(parents=True)
    (raw_root / "boss_jobs_20260923_1200.json").write_text(
        json.dumps(
            {"scraped_at": "2026-09-23T12:00:00+08:00", "jobs": [{"job_id": "one"}, {"job_id": "two"}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    summary = MarketDataSummaryService(tmp_path / "databases").get()

    assert summary.available is False
    assert summary.raw_available is True
    assert summary.raw_job_count == 2
    assert summary.raw_pending_publish is True


def test_summary_reads_current_snapshot_and_fixed_breakdowns(tmp_path: Path) -> None:
    summary = MarketDataSummaryService(publish_fixture(tmp_path)).get()

    assert summary.available is True
    assert summary.job_count == 3
    assert summary.detail_count == 1
    assert summary.detail_coverage == 1 / 3
    assert summary.dataset_version and summary.dataset_version.startswith("ds_")
    assert summary.first_scraped_at == summary.latest_scraped_at
    assert summary.top_cities[0].label == "上海"
    assert summary.top_cities[0].count == 2
    assert summary.top_cities[0].ratio == 2 / 3
    assert summary.top_industries[0].label == "互联网"
    assert summary.top_industries[0].count == 2
    assert summary.top_keywords[0].label == "AI Agent"
    assert summary.raw_available is True
    assert summary.raw_job_count == 3
    assert summary.raw_detail_count == 1
    assert summary.raw_pending_publish is False


def test_delete_last_run_removes_current_snapshot_and_archives(tmp_path: Path) -> None:
    database_root = publish_fixture(tmp_path)
    service = MarketDataSummaryService(database_root)
    runs = service.runs()

    assert len(runs) == 1
    assert runs[0].page_count == 1
    assert runs[0].page_count_estimated is True
    assert runs[0].deletable is True

    result = service.delete_run(runs[0].run_id)

    assert result.deleted is True
    assert service.runs() == []
    summary = service.get()
    assert summary.available is False
    assert summary.job_count == 0
    assert not (database_root / "jobs_market").exists()
    assert not (tmp_path / "local" / "etl" / runs[0].run_id).exists()
    assert list((tmp_path / "local" / "boss_zhipin" / "job-result").glob("*.json")) == []


def test_delete_run_rebuilds_snapshot_from_retained_archives(tmp_path: Path) -> None:
    database_root = publish_fixture(tmp_path)
    second_source = tmp_path / "jobs-second.json"
    second_source.write_text(
        json.dumps(
            {
                "scraped_at": "2026-09-24T12:00:00+08:00",
                "keyword": "Python",
                "city": "杭州",
                "status": "succeeded",
                "jobs": [
                    {
                        "job_id": "four",
                        "title": "Python 工程师",
                        "salary": "20K",
                        "salary_source": "fixture",
                        "location": "杭州·西湖",
                        "company_industry": "金融",
                        "tags": "1-3年 | 本科",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    run_etl(second_source, None, database_root, tmp_path / "local" / "etl")
    service = MarketDataSummaryService(database_root)
    runs = service.runs()
    deleted = next(item for item in runs if item.keyword == "AI Agent")
    retained = next(item for item in runs if item.keyword == "Python")

    result = service.delete_run(deleted.run_id)

    assert result.deleted is True
    assert result.dataset_version and result.dataset_version != deleted.dataset_version
    summary = service.get()
    assert summary.available is True
    assert summary.job_count == 1
    remaining = service.runs()
    assert [item.run_id for item in remaining] == [retained.run_id]
    assert remaining[0].dataset_version == summary.dataset_version
    assert not (tmp_path / "local" / "etl" / deleted.run_id).exists()
