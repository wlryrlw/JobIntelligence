from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.contracts import QueryPlan
from app.etl.pipeline import run_etl
from app.querying.duckdb_engine import DuckDbEngine
from app.querying.query_agent import QueryAgent
from app.querying.schema_catalog import SchemaCatalogBuilder
from app.security import AccessScope

from eval.dataset import DATA_ROOT, load_dataset
from eval.models import BenchmarkResult, CaseMetrics, EvalCase, EvalDataset


RUNNER_VERSION = "1.0.0"
DEFAULT_COST_PER_1K_TOKENS = 0.001
TABLES = frozenset({"jobs_market.jobs", "jobs_market.job_details", "jobs_market.job_skills", "jobs_market.crawl_runs"})


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


def _make_agent(database_root: Path, case: EvalCase, max_attempts: int, few_shot: bool) -> QueryAgent:
    return QueryAgent(
        model_client=object(),
        engine=DuckDbEngine(database_root),
        catalog_builder=SchemaCatalogBuilder(database_root),
        max_attempts=max_attempts,
        sql_generator=lambda _query, _catalog, previous: {
            "sql": _sql_for_case(case, previous, few_shot),
            "explanation": "deterministic eval fixture",
        },
    )


def _is_correct(result: Any, expected: Any) -> bool:
    return result.status == "completed" and _normalise_rows(result.columns, result.rows) == _normalise_rows(expected.columns, expected.rows)


def run_benchmark(dataset: EvalDataset, *, correction_enabled: bool = True, few_shot_enabled: bool = True, model_name: str = "deterministic-fixture-model", cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS) -> BenchmarkResult:
    if cost_per_1k_tokens < 0:
        raise ValueError("cost_per_1k_tokens 不能小于 0")
    scope = _scope()
    with tempfile.TemporaryDirectory(prefix="offeragent-eval-") as temporary:
        database_root = _prepare_database(Path(temporary))
        engine = DuckDbEngine(database_root)
        metrics: list[CaseMetrics] = []
        for case in dataset.cases:
            expected = engine.execute("jobs_market", case.expected_sql, scope)
            if not expected.success:
                raise RuntimeError(f"评测期望 SQL 无法执行：{case.case_id}: {expected.error}")
            first_result = _make_agent(database_root, case, 1, few_shot_enabled).run(QueryPlan(query=case.query, database="jobs_market"), access_scope=scope)
            final_agent = _make_agent(database_root, case, 3 if correction_enabled else 1, few_shot_enabled)
            started = time.perf_counter()
            final_result = final_agent.run(QueryPlan(query=case.query, database="jobs_market"), access_scope=scope)
            elapsed = (time.perf_counter() - started) * 1000
            first_correct = _is_correct(first_result, expected)
            final_correct = _is_correct(final_result, expected)
            attempts = len(final_result.attempts)
            prompt_tokens = final_result.schema.prompt_token_count if final_result.schema else 0
            estimated_tokens = prompt_tokens * max(1, attempts) + max(1, math.ceil(len(case.query) / 4))
            metrics.append(CaseMetrics(
                case_id=case.case_id,
                first_pass_correct=first_correct,
                final_correct=final_correct,
                recovered=not first_correct and final_correct,
                attempts=attempts,
                latency_ms=elapsed,
                estimated_tokens=estimated_tokens,
                estimated_cost=estimated_tokens / 1000 * cost_per_1k_tokens,
                status=final_result.status,
                error_kind=final_result.error_kind.value if final_result.error_kind else None,
            ))
    size = len(metrics)
    first_count = sum(item.first_pass_correct for item in metrics)
    final_count = sum(item.final_correct for item in metrics)
    recovered_count = sum(item.recovered for item in metrics)
    total_attempts = sum(item.attempts for item in metrics)
    total_tokens = sum(item.estimated_tokens for item in metrics)
    return BenchmarkResult(
        runner_version=RUNNER_VERSION,
        dataset=dataset.name,
        dataset_size=size,
        model=model_name,
        correction_enabled=correction_enabled,
        few_shot_enabled=few_shot_enabled,
        cost_per_1k_tokens=cost_per_1k_tokens,
        first_pass_accuracy=round(first_count / size, 6),
        final_accuracy=round(final_count / size, 6),
        recovery_rate=round(recovered_count / max(1, size - first_count), 6),
        average_attempts=round(total_attempts / size, 6),
        p50_latency_ms=_percentile([item.latency_ms for item in metrics], 0.50),
        p95_latency_ms=_percentile([item.latency_ms for item in metrics], 0.95),
        total_estimated_tokens=total_tokens,
        total_estimated_cost=round(total_tokens / 1000 * cost_per_1k_tokens, 8),
        cases=metrics,
    )


def run_ablation(dataset: EvalDataset, *, cost_per_1k_tokens: float = DEFAULT_COST_PER_1K_TOKENS) -> list[BenchmarkResult]:
    return [run_benchmark(dataset, correction_enabled=correction, few_shot_enabled=few_shot, cost_per_1k_tokens=cost_per_1k_tokens) for correction, few_shot in ((True, True), (False, True), (True, False), (False, False))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 OfferAgent 离线 NL2SQL 评测")
    parser.add_argument("--dataset", choices=("seed", "eval_dev", "eval_test"), default="eval_test")
    parser.add_argument("--correction", choices=("on", "off"), default="on")
    parser.add_argument("--few-shot", choices=("on", "off"), default="on")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--cost-per-1k-tokens", type=float, default=DEFAULT_COST_PER_1K_TOKENS)
    args = parser.parse_args(argv)
    dataset = load_dataset(args.dataset)
    payload: Any = [item.model_dump(mode="json") for item in run_ablation(dataset, cost_per_1k_tokens=args.cost_per_1k_tokens)] if args.ablation else run_benchmark(dataset, correction_enabled=args.correction == "on", few_shot_enabled=args.few_shot == "on", cost_per_1k_tokens=args.cost_per_1k_tokens).model_dump(mode="json")
    encoded = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded, encoding="utf-8")
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
