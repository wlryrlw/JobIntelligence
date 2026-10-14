from __future__ import annotations

from typing import Any


CONSOLE_KEY_TRANSLATIONS: dict[str, str] = {
    "runner_version": "runner版本",
    "dataset": "数据集",
    "dataset_size": "数据集规模",
    "model": "模型",
    "correction_enabled": "纠错是否开启",
    "few_shot_enabled": "few-shot是否开启",
    "cost_per_1k_tokens": "每千tokens成本",
    "suite": "评测套件",
    "first_pass_accuracy": "首轮准确率",
    "final_accuracy": "最终准确率",
    "first_execution_success_rate": "首轮执行成功率",
    "final_execution_success_rate": "最终执行成功率",
    "first_query_result_success_rate": "首轮查询结果成功率",
    "final_query_result_success_rate": "最终查询结果成功率",
    "recovery_rate": "纠错恢复率",
    "average_attempts": "平均尝试次数",
    "average_sql_execution_ms": "平均SQL执行耗时（毫秒）",
    "p50_sql_execution_ms": "SQL执行耗时P50（毫秒）",
    "p95_sql_execution_ms": "SQL执行耗时P95（毫秒）",
    "p50_latency_ms": "端到端延迟P50（毫秒）",
    "p95_latency_ms": "端到端延迟P95（毫秒）",
    "total_sql_executions": "SQL执行总次数",
    "successful_sql_executions": "成功SQL执行次数",
    "first_schema_table_precision": "首轮schema表precision",
    "first_schema_table_recall": "首轮schema表recall",
    "first_schema_table_f1": "首轮schema表F1",
    "final_schema_table_precision": "最终schema表precision",
    "final_schema_table_recall": "最终schema表recall",
    "final_schema_table_f1": "最终schema表F1",
    "first_schema_precision": "首轮schema字段precision",
    "first_schema_recall": "首轮schema字段recall",
    "first_schema_f1": "首轮schema字段F1",
    "final_schema_precision": "最终schema字段precision",
    "final_schema_recall": "最终schema字段recall",
    "final_schema_f1": "最终schema字段F1",
    "total_estimated_tokens": "估算tokens总数",
    "total_estimated_cost": "估算成本",
    "cases": "case明细",
    "mode": "评测模式",
    "api_base_url": "API基础URL",
    "prompt_version": "Prompt版本",
    "summaries": "汇总指标",
    "requested_count": "请求评测case数",
    "completed_count": "完成评测case数",
    "failed_count": "失败case数",
    "passed_count": "通过case数",
    "score_0_count": "score=0的case数",
    "score_1_count": "score=1的case数",
    "score_2_count": "score=2的case数",
    "pass_rate": "通过率",
    "average_score": "平均score",
}


def localize_console_keys(value: Any) -> Any:
    """递归为控制台指标字段追加中文说明，不改变 JSON 文件中的字段名。"""
    if isinstance(value, list):
        return [localize_console_keys(item) for item in value]
    if isinstance(value, dict):
        localized: dict[str, Any] = {}
        for key, item in value.items():
            translation = CONSOLE_KEY_TRANSLATIONS.get(key, "字段")
            localized[f"{key}（{translation}）"] = localize_console_keys(item)
        return localized
    return value
