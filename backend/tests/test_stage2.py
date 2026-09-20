
from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

from app.contracts import QueryPlan, SqlErrorKind
from app.coordinator import Coordinator
from app.models import ChartSpec
from app.querying.charting import chart_spec_to_echarts
from app.querying.query_agent import QueryAgent
from app.querying.schema_catalog import build_schema_catalog
from app.security import AccessController


class FakeModel:
    def __init__(self, payloads):
        self.payloads = iter(payloads)
        self.calls = []

    def chat_json(self, system, user):
        self.calls.append((system, user))
        return next(self.payloads)


def test_schema_catalog_is_full_and_records_prompt_scale():
    scope = AccessController().resolve("local_user")
    catalog = build_schema_catalog("jobs_market", access_scope=scope)
    assert {table.name for table in catalog.tables} == {"jobs", "job_details", "job_skills", "crawl_runs"}
    assert len(catalog.fields) == 66
    assert catalog.prompt_char_count == len(catalog.render())
    assert catalog.prompt_token_count >= catalog.prompt_char_count / 4
    rendered = catalog.render()
    assert "salary_min_k" in rendered
    assert "jd_text" in rendered
    assert "岗位市场" in rendered
    assert "synthetic" not in rendered


def test_query_agent_repairs_unknown_field_and_emits_bounded_events():
    model = FakeModel([
        {"sql": "SELECT missing_column FROM jobs"},
        {"sql": "SELECT job_id FROM jobs LIMIT 1"},
    ])
    events = []
    agent = QueryAgent(model)
    result = agent.run(
        QueryPlan(query="查询岗位编号", database="jobs_market"),
        access_scope=AccessController().resolve("local_user"),
        event_sink=lambda event_type, payload: events.append((event_type, payload)),
    )
    assert result.status == "completed"
    assert len(result.attempts) == 2
    assert result.attempts[0].error_kind == SqlErrorKind.UNKNOWN_FIELD
    assert result.attempts[0].retryable is True
    assert result.attempts[1].execution.value == "succeeded"
    assert len([event for event in events if event[0] == "query.sql_executed"]) == 2
    assert any(event[0] == "query.sql_repair" for event in events)
    assert len(model.calls) == 2


def test_query_agent_does_not_retry_security_error():
    model = FakeModel([{"sql": "SELECT * FROM jobs"}])
    result = QueryAgent(model).run(
        QueryPlan(query="查询岗位", database="jobs_market"),
        access_scope=AccessController().resolve("local_user"),
    )
    assert result.status == "failed"
    assert len(result.attempts) == 1
    assert result.attempts[0].error_kind == SqlErrorKind.SECURITY
    assert result.attempts[0].retryable is False
    assert len(model.calls) == 1


def test_query_agent_caps_repair_attempts_at_three():
    model = FakeModel([
        {"sql": "SELECT missing_a FROM jobs"},
        {"sql": "SELECT missing_b FROM jobs"},
        {"sql": "SELECT missing_c FROM jobs"},
        {"sql": "SELECT job_id FROM jobs"},
    ])
    result = QueryAgent(model).run(
        QueryPlan(query="查询岗位编号", database="jobs_market"),
        access_scope=AccessController().resolve("local_user"),
    )
    assert result.status == "failed"
    assert len(result.attempts) == 3
    assert len(model.calls) == 3
    assert all(item.retryable for item in result.attempts)


def test_chart_specs_map_to_deterministic_echarts_options():
    rows = [
        {"month": "2026-07", "salary": 20},
        {"month": "2026-08", "salary": 25},
    ]
    line = chart_spec_to_echarts(
        ChartSpec(
            type="line", title="趋势", source_task_id="t", category_field="month", value_field="salary"
        ),
        rows,
    )
    assert line["series"][0]["type"] == "line"
    assert line["xAxis"]["data"] == ["2026-07", "2026-08"]
    histogram = chart_spec_to_echarts(
        ChartSpec(
            type="histogram", title="分布", source_task_id="t",
            category_field="month", value_field="salary", bins=4
        ),
        rows,
    )
    assert histogram["series"][0]["type"] == "bar"
    assert sum(histogram["series"][0]["data"]) == 2


def test_coordinator_requires_city_before_resource_gate():
    decision = Coordinator().decide(
        __import__("app.contracts", fromlist=["UserRequest"]).UserRequest(
            task_id="t", text="查询 Python 工程师岗位"
        ),
        action="database_query",
    )
    assert decision.action == "clarify"
    assert decision.clarification is not None
    assert decision.clarification.parameter == "city"


def test_coordinator_reads_only_published_fresh_runs(tmp_path: Path):
    folder = tmp_path / "jobs_market"
    folder.mkdir()
    with (folder / "crawl_runs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=[
            "run_id", "keyword", "city", "status", "dataset_version", "finished_at", "started_at"
        ])
        writer.writeheader()
        writer.writerow({
            "run_id": "run-1", "keyword": "Python 工程师", "city": "上海",
            "status": "succeeded", "dataset_version": "ds-1",
            "finished_at": "2026-09-18T00:00:00+00:00", "started_at": "2026-09-17T00:00:00+00:00",
        })
    availability = Coordinator(folder, freshness_days=7).inspect_dataset(
        __import__("app.contracts", fromlist=["TargetJobRequest"]).TargetJobRequest(
            keyword="Python 工程师", city="上海"
        ),
        now=datetime(2026, 9, 19, tzinfo=timezone.utc),
    )
    assert availability.available is True
    assert availability.dataset_version == "ds-1"
