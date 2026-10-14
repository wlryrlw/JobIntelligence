from __future__ import annotations

from eval.dataset import load_all_datasets, load_dataset
from eval.runner import run_ablation, run_benchmark


def test_stage4_datasets_are_separated_and_replayable() -> None:
    datasets = load_all_datasets()
    assert {name: len(dataset.cases) for name, dataset in datasets.items()} == {
        "seed": 6,
        "eval_dev": 24,
        "eval_test": 100,
    }


def test_stage4_runner_reports_accuracy_recovery_and_latency() -> None:
    result = run_benchmark(load_dataset("seed"))
    assert result.dataset_size == 6
    assert result.first_pass_accuracy < result.final_accuracy == 1
    assert result.recovery_rate == 1
    assert result.first_execution_success_rate < result.final_execution_success_rate == 1
    assert result.first_query_result_success_rate == result.first_pass_accuracy
    assert result.final_query_result_success_rate == result.final_accuracy
    assert result.final_schema_table_precision == result.final_schema_table_recall == result.final_schema_table_f1 == 1
    assert result.final_schema_precision == result.final_schema_recall == result.final_schema_f1 == 1
    assert result.total_sql_executions == result.successful_sql_executions + 2
    assert result.average_sql_execution_ms > 0
    assert result.p95_sql_execution_ms >= result.p50_sql_execution_ms > 0
    assert result.p50_latency_ms >= 0
    assert result.p95_latency_ms >= result.p50_latency_ms
    assert result.total_estimated_tokens > 0


def test_stage4_smoke_suite_selects_only_marked_cases() -> None:
    result = run_benchmark(load_dataset("eval_test"), suite="smoke")
    assert result.suite == "smoke"
    assert result.dataset_size == 10
    assert all(case.smoke for case in result.cases)
    assert result.final_execution_success_rate == 1


def test_stage4_ablation_has_four_reproducible_configurations() -> None:
    results = run_ablation(load_dataset("eval_test"))
    assert {(item.correction_enabled, item.few_shot_enabled) for item in results} == {
        (True, True),
        (False, True),
        (True, False),
        (False, False),
    }
    assert results[0].final_accuracy > results[-1].final_accuracy
