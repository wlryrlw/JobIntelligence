from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from app.contracts import QueryPlan, SchemaCatalog
from app.etl.pipeline import run_etl
from app.querying.duckdb_engine import DuckDbEngine
from app.querying.models import SqlExecution
from app.querying.query_agent import QueryAgent
from app.querying.schema_catalog import SchemaCatalogBuilder
from app.security import AccessScope

from eval.dataset import DATA_ROOT, load_dataset
from eval.console import localize_console_keys
from eval.models import (
    BenchmarkResult,
    CaseMetrics,
    EvalCase,
    EvalDataset,
)


RUNNER_VERSION = "1.0.0"
DEFAULT_COST_PER_1K_TOKENS = 0.001
RESULTS_ROOT = Path(__file__).resolve().parent / "results"
TABLES = frozenset({"jobs_market.jobs", "jobs_market.job_details", "jobs_market.job_skills", "jobs_market.crawl_runs"})


@dataclass(frozen=True)
class SchemaReferences:
    tables: frozenset[str]
    fields: frozenset[str]


class TimedDuckDbEngine(DuckDbEngine):
    """在评测边界记录每次 SQL 执行耗时，不改变生产引擎契约。"""

    def __init__(self, database_root: Path) -> None:
        super().__init__(database_root)
        self.execution_times_ms: list[float] = []
        self.execution_successes = 0

    def execute(
        self,
        database: str,
        sql: str,
        access_scope: AccessScope | None = None,
    ) -> SqlExecution:
        started = time.perf_counter()
        result = super().execute(database, sql, access_scope)
        self.execution_times_ms.append((time.perf_counter() - started) * 1000)
        if result.success:
            self.execution_successes += 1
        return result


def _scope() -> AccessScope:
    return AccessScope(user_id="eval_runner", roles=("local",), allowed_databases=frozenset({"jobs_market"}), allowed_tables=TABLES)


def _normalise_rows(columns: list[str], rows: list[dict[str, Any]]) -> tuple[tuple[str, ...], tuple[tuple[Any, ...], ...]]:
    def value(item: Any) -> Any:
        if isinstance(item, float):
            return round(item, 8)
        if isinstance(item, list):
            return tuple(item)
        if isinstance(item, dict):
            return tuple(sorted((str(key), value(raw)) for key, raw in item.items()))
        return item

    return tuple(columns), tuple(tuple(value(row.get(column)) for column in columns) for row in rows)


def _percentile(values: list[float], percentile: float) -> float:
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1))
    return round(ordered[index], 3) if ordered else 0.0


def _normalise_schema_ref(value: str) -> str:
    parts = [part.strip().strip('"') for part in value.split(".") if part.strip()]
    if len(parts) >= 2:
        return ".".join(parts[-2:]).lower()
    return value.strip().strip('"').lower()


def _schema_references(sql: str, catalog: SchemaCatalog) -> SchemaReferences:
    """从 SQL 中提取实际使用的表/字段，用于离线 Schema 使用评测。"""

    if not sql.strip():
        return SchemaReferences(frozenset(), frozenset())
    try:
        statement = sqlglot.parse_one(sql, read="duckdb")
    except (ParseError, ValueError):
        return SchemaReferences(frozenset(), frozenset())

    cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
    table_nodes = [
        table
        for table in statement.find_all(exp.Table)
        if table.name not in cte_names
    ]
    tables = {_normalise_schema_ref(table.name) for table in table_nodes}
    aliases = {
        table.alias_or_name.lower(): _normalise_schema_ref(table.name)
        for table in table_nodes
    }
    table_fields = {
        _normalise_schema_ref(table.name): {field.name.lower() for field in table.fields}
        for table in catalog.tables
    }
    select_aliases = {
        projection.alias.lower()
        for select in statement.find_all(exp.Select)
        for projection in select.expressions
        if projection.alias
    }
    fields: set[str] = set()
    for column in statement.find_all(exp.Column):
        name = column.name.strip().strip('"').lower()
        if not name or name == "*" or name in select_aliases:
            continue
        qualifier = column.table.strip().strip('"').lower() if column.table else ""
        if qualifier:
            table = aliases.get(qualifier, _normalise_schema_ref(qualifier))
            fields.add(f"{table}.{name}")
            continue
        candidates = [table for table in tables if name in table_fields.get(table, set())]
        if len(candidates) == 1:
            fields.add(f"{candidates[0]}.{name}")
        elif len(tables) == 1:
            fields.add(f"{next(iter(tables))}.{name}")
        else:
            fields.add(f"?.{name}")
    return SchemaReferences(frozenset(tables), frozenset(fields))


def _expected_schema(case: EvalCase, catalog: SchemaCatalog) -> SchemaReferences:
    if case.expected_schema is None:
        return _schema_references(case.expected_sql, catalog)
    return SchemaReferences(
        frozenset(_normalise_schema_ref(item) for item in case.expected_schema.tables),
        frozenset(_normalise_schema_ref(item) for item in case.expected_schema.fields),
    )


def _schema_score(predicted: frozenset[str], expected: frozenset[str]) -> tuple[float, float, float]:
    if not predicted and not expected:
        return 1.0, 1.0, 1.0
    overlap = len(predicted & expected)
    precision = overlap / len(predicted) if predicted else 0.0
    recall = overlap / len(expected) if expected else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return round(precision, 6), round(recall, 6), round(f1, 6)


def _result_sql(result: Any) -> str:
    if result.sql:
        return result.sql
    return result.attempts[-1].sql if result.attempts else ""


def _select_suite(dataset: EvalDataset, suite: str) -> EvalDataset:
    if suite == "full":
        return dataset
    if suite != "smoke":
        raise ValueError("suite 必须是 full 或 smoke")
    cases = [case for case in dataset.cases if case.smoke]
    if not cases:
        raise ValueError(f"数据集 {dataset.name} 没有标记为 smoke 的 case")
    return dataset.model_copy(update={"cases": cases})


def _prepare_database(root: Path) -> Path:
    database_root = root / "databases"
    run_etl(DATA_ROOT.parents[1] / "data" / "fixtures" / "jobs_seed.json", None, database_root, root / "raw")
    return database_root


def _sql_for_case(case: EvalCase, previous: dict[str, Any] | None, few_shot: bool) -> str:
    if previous:
        return case.repair_sql
    if few_shot and case.few_shot_sql:
        return case.few_shot_sql
    return case.first_sql


def _make_agent(
    database_root: Path,
    case: EvalCase,
    max_attempts: int,
    few_shot: bool,
    engine: DuckDbEngine | None = None,
) -> QueryAgent:
    return QueryAgent(
        model_client=object(),
        engine=engine or DuckDbEngine(database_root),
        catalog_builder=SchemaCatalogBuilder(database_root),
        max_attempts=max_attempts,
        sql_generator=lambda _query, _catalog, previous: {
            "sql": _sql_for_case(case, previous, few_shot),
            "explanation": "deterministic eval fixture",
        },
    )


def _is_correct(result: Any, expected: Any) -> bool:
    return result.status == "completed" and _normalise_rows(result.columns, result.rows) == _normalise_rows(expected.columns, expected.rows)


def run_benchmark(
    dataset: EvalDataset,
    *,
    correction_enabled: bool = True,
    few_shot_enabled: bool = True,
    model_name: str = "deterministic-fixture-model",
    cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS,
    suite: str = "full",
) -> BenchmarkResult:
    if cost_per_1k_tokens < 0:
        raise ValueError("cost_per_1k_tokens 不能小于 0")
    selected_dataset = _select_suite(dataset, suite)
    scope = _scope()
    with tempfile.TemporaryDirectory(prefix="offeragent-eval-") as temporary:
        database_root = _prepare_database(Path(temporary))
        engine = DuckDbEngine(database_root)
        metrics: list[CaseMetrics] = []
        sql_execution_times: list[float] = []
        for case in selected_dataset.cases:
            expected = engine.execute("jobs_market", case.expected_sql, scope)
            if not expected.success:
                raise RuntimeError(f"评测期望 SQL 无法执行：{case.case_id}: {expected.error}")
            first_engine = TimedDuckDbEngine(database_root)
            first_result = _make_agent(
                database_root, case, 1, few_shot_enabled, first_engine
            ).run(QueryPlan(query=case.query, database="jobs_market"), access_scope=scope)
            final_engine = TimedDuckDbEngine(database_root)
            final_agent = _make_agent(
                database_root,
                case,
                3 if correction_enabled else 1,
                few_shot_enabled,
                final_engine,
            )
            started = time.perf_counter()
            final_result = final_agent.run(QueryPlan(query=case.query, database="jobs_market"), access_scope=scope)
            elapsed = (time.perf_counter() - started) * 1000
            first_correct = _is_correct(first_result, expected)
            final_correct = _is_correct(final_result, expected)
            attempts = len(final_result.attempts)
            prompt_tokens = final_result.schema.prompt_token_count if final_result.schema else 0
            estimated_tokens = prompt_tokens * max(1, attempts) + max(1, math.ceil(len(case.query) / 4))
            catalog = final_result.schema or first_result.schema
            if catalog is None:
                raise RuntimeError(f"评测未返回 Schema Catalog：{case.case_id}")
            expected_schema = _expected_schema(case, catalog)
            first_schema = _schema_references(_result_sql(first_result), catalog)
            final_schema = _schema_references(_result_sql(final_result), catalog)
            first_table_precision, first_table_recall, first_table_f1 = _schema_score(
                first_schema.tables, expected_schema.tables
            )
            final_table_precision, final_table_recall, final_table_f1 = _schema_score(
                final_schema.tables, expected_schema.tables
            )
            first_field_precision, first_field_recall, first_field_f1 = _schema_score(
                first_schema.fields, expected_schema.fields
            )
            final_field_precision, final_field_recall, final_field_f1 = _schema_score(
                final_schema.fields, expected_schema.fields
            )
            sql_execution_times.extend(final_engine.execution_times_ms)
            metrics.append(CaseMetrics(
                case_id=case.case_id,
                category=case.category,
                smoke=case.smoke,
                first_pass_correct=first_correct,
                final_correct=final_correct,
                first_execution_success=first_result.status == "completed",
                final_execution_success=final_result.status == "completed",
                recovered=not first_correct and final_correct,
                attempts=attempts,
                sql_execution_count=len(final_engine.execution_times_ms),
                sql_execution_success_count=final_engine.execution_successes,
                sql_execution_time_ms=round(sum(final_engine.execution_times_ms), 3),
                latency_ms=elapsed,
                estimated_tokens=estimated_tokens,
                estimated_cost=estimated_tokens / 1000 * cost_per_1k_tokens,
                first_schema_table_precision=first_table_precision,
                first_schema_table_recall=first_table_recall,
                first_schema_table_f1=first_table_f1,
                final_schema_table_precision=final_table_precision,
                final_schema_table_recall=final_table_recall,
                final_schema_table_f1=final_table_f1,
                first_schema_precision=first_field_precision,
                first_schema_recall=first_field_recall,
                first_schema_f1=first_field_f1,
                final_schema_precision=final_field_precision,
                final_schema_recall=final_field_recall,
                final_schema_f1=final_field_f1,
                status=final_result.status,
                error_kind=final_result.error_kind.value if final_result.error_kind else None,
            ))
    size = len(metrics)
    first_count = sum(item.first_pass_correct for item in metrics)
    final_count = sum(item.final_correct for item in metrics)
    first_execution_count = sum(item.first_execution_success for item in metrics)
    final_execution_count = sum(item.final_execution_success for item in metrics)
    recovered_count = sum(item.recovered for item in metrics)
    total_attempts = sum(item.attempts for item in metrics)
    total_tokens = sum(item.estimated_tokens for item in metrics)
    def average(values: list[float]) -> float:
        return round(sum(values) / len(values), 6) if values else 0.0
    return BenchmarkResult(
        runner_version=RUNNER_VERSION,
        dataset=selected_dataset.name,
        dataset_size=size,
        model=model_name,
        correction_enabled=correction_enabled,
        few_shot_enabled=few_shot_enabled,
        cost_per_1k_tokens=cost_per_1k_tokens,
        suite=suite,
        first_pass_accuracy=round(first_count / size, 6),
        final_accuracy=round(final_count / size, 6),
        first_execution_success_rate=round(first_execution_count / size, 6),
        final_execution_success_rate=round(final_execution_count / size, 6),
        first_query_result_success_rate=round(first_count / size, 6),
        final_query_result_success_rate=round(final_count / size, 6),
        recovery_rate=round(recovered_count / max(1, size - first_count), 6),
        average_attempts=round(total_attempts / size, 6),
        average_sql_execution_ms=average(sql_execution_times),
        p50_sql_execution_ms=_percentile(sql_execution_times, 0.50),
        p95_sql_execution_ms=_percentile(sql_execution_times, 0.95),
        p50_latency_ms=_percentile([item.latency_ms for item in metrics], 0.50),
        p95_latency_ms=_percentile([item.latency_ms for item in metrics], 0.95),
        total_sql_executions=sum(item.sql_execution_count for item in metrics),
        successful_sql_executions=sum(item.sql_execution_success_count for item in metrics),
        first_schema_table_precision=average([item.first_schema_table_precision for item in metrics]),
        first_schema_table_recall=average([item.first_schema_table_recall for item in metrics]),
        first_schema_table_f1=average([item.first_schema_table_f1 for item in metrics]),
        final_schema_table_precision=average([item.final_schema_table_precision for item in metrics]),
        final_schema_table_recall=average([item.final_schema_table_recall for item in metrics]),
        final_schema_table_f1=average([item.final_schema_table_f1 for item in metrics]),
        first_schema_precision=average([item.first_schema_precision for item in metrics]),
        first_schema_recall=average([item.first_schema_recall for item in metrics]),
        first_schema_f1=average([item.first_schema_f1 for item in metrics]),
        final_schema_precision=average([item.final_schema_precision for item in metrics]),
        final_schema_recall=average([item.final_schema_recall for item in metrics]),
        final_schema_f1=average([item.final_schema_f1 for item in metrics]),
        total_estimated_tokens=total_tokens,
        total_estimated_cost=round(total_tokens / 1000 * cost_per_1k_tokens, 8),
        cases=metrics,
    )


def run_ablation(
    dataset: EvalDataset,
    *,
    cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS,
    suite: str = "full",
) -> list[BenchmarkResult]:
    return [
        run_benchmark(
            dataset,
            correction_enabled=correction,
            few_shot_enabled=few_shot,
            cost_per_1k_tokens=cost_per_1k_tokens,
            suite=suite,
        )
        for correction, few_shot in ((True, True), (False, True), (True, False), (False, False))
    ]


def _strip_case_details(payload: Any) -> Any:
    """只保留整体指标，避免控制台打印每个 case 的完整明细。"""
    if isinstance(payload, list):
        return [_strip_case_details(item) for item in payload]
    if isinstance(payload, dict):
        return {
            key: value
            for key, value in payload.items()
            if key != "cases"
        }
    raise TypeError("评测结果必须是对象或对象列表")


def _metrics_for_console(payload: Any) -> Any:
    return localize_console_keys(_strip_case_details(payload))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 OfferAgent 离线 NL2SQL 评测")
    parser.add_argument("--dataset", choices=("seed", "eval_dev", "eval_test"), default="eval_test")
    parser.add_argument("--correction", choices=("on", "off"), default="on")
    parser.add_argument("--few-shot", choices=("on", "off"), default="on")
    parser.add_argument("--suite", choices=("full", "smoke"), default="full")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cost-per-1k-tokens", type=float, default=DEFAULT_COST_PER_1K_TOKENS)
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)
    payload: Any = (
        [
            item.model_dump(mode="json")
            for item in run_ablation(
                dataset,
                cost_per_1k_tokens=args.cost_per_1k_tokens,
                suite=args.suite,
            )
        ]
        if args.ablation
        else run_benchmark(
            dataset,
            correction_enabled=args.correction == "on",
            few_shot_enabled=args.few_shot == "on",
            cost_per_1k_tokens=args.cost_per_1k_tokens,
            suite=args.suite,
        ).model_dump(mode="json")
    )
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    output_path = args.output or RESULTS_ROOT / (
        f"{args.dataset}_{args.suite}_ablation.json"
        if args.ablation
        else f"{args.dataset}_{args.suite}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    print("确定性评测指标：")
    print(json.dumps(_metrics_for_console(payload), ensure_ascii=False, indent=2))
    print(f"完整评测结果已写入：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
