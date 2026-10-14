from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.querying.duckdb_engine import DuckDbEngine

from eval.dataset import load_dataset
from eval.console import localize_console_keys
from eval.judge import JudgeClient, JudgeConfig
from eval.judge_models import (
    JudgeCaseResult,
    JudgeMode,
    JudgeRunResult,
    JudgeVerdict,
    JudgeSummary,
)
from eval.runner import _prepare_database, _scope


JUDGE_RUNNER_VERSION = "1.1.0"
RESULTS_ROOT = Path(__file__).resolve().parent / "results"

DEFAULT_SINGLE_PROMPT = """你是一个严格的 SQL 答案评测器。
请根据用户问题和最终 SQL，判断最终 SQL 是否正确回答了用户问题。
重点检查查询对象、字段、过滤条件、聚合、分组、排序和安全边界。
只返回 JSON，不要返回 Markdown 或额外文本。"""

DEFAULT_REFERENCE_PROMPT = """你是一个严格的 SQL 参考答案评测器。
请根据用户问题、最终 SQL、参考 SQL 以及两者的执行结果，判断最终 SQL 是否与参考答案语义等价。
SQL 写法可以不同，但不能放宽用户问题的字段、过滤、聚合、分组或排序口径。
只返回 JSON，不要返回 Markdown 或额外文本。"""


class JudgeEvaluator(Protocol):
    async def evaluate(self, system_prompt: str, payload: dict[str, object]) -> JudgeVerdict:
        ...


def _bounded(value: str | None, limit: int = 500) -> str | None:
    if value is None:
        return None
    return value[:limit]


def _execution_payload(execution: object) -> dict[str, object]:
    return {
        "success": bool(execution.success),
        "columns": list(execution.columns)[:50],
        "rows": list(execution.rows)[:20],
        "error": _bounded(execution.error),
    }


def _prompt_with_contract(prompt: str, mode: JudgeMode) -> str:
    reference_instruction = (
        "本次输入包含 reference_answer 和 reference_execution，请进行参考答案对照评测。"
        if mode == "reference"
        else "本次没有提供参考答案，请只根据用户问题和最终答案独立评分。"
    )
    return f"""{prompt.strip()}

{reference_instruction}
请严格只返回一个 JSON 对象，格式如下：
{{
  "score": 0,
  "label": "correct|partially_correct|incorrect|uncertain",
  "error_type": "none|wrong_table|wrong_field|wrong_filter|wrong_aggregation|wrong_group_by|wrong_join|wrong_order|wrong_format|other",
  "reason": "不超过500字的简短依据"
}}
score 只能是整数 0、1、2：
- 0：错误，不能回答用户问题，或 SQL 执行失败且无法证明结果正确；
- 1：部分正确，核心意图基本正确但存在字段、过滤、聚合、排序等问题；
- 2：正确，SQL 语义和查询结果都满足用户问题。
只有 score=2 才算通过。不要编造输入中没有的事实，不要输出隐藏思维过程。"""


def _load_prompt(
    *,
    mode: JudgeMode,
    prompt: str | None,
    prompt_file: Path | None,
) -> str:
    if prompt and prompt_file:
        raise ValueError("--prompt 和 --prompt-file 只能二选一")
    if prompt:
        return prompt
    if prompt_file:
        if not prompt_file.is_file():
            raise FileNotFoundError(f"Judge Prompt 文件不存在：{prompt_file}")
        return prompt_file.read_text(encoding="utf-8")
    return DEFAULT_REFERENCE_PROMPT if mode == "reference" else DEFAULT_SINGLE_PROMPT


def _summary(cases: Sequence[JudgeCaseResult]) -> JudgeSummary:
    completed = [case for case in cases if case.status == "completed"]
    passed = [case for case in completed if case.passed]
    scores = [case.score for case in completed if case.score is not None]
    return JudgeSummary(
        requested_count=len(cases),
        completed_count=len(completed),
        failed_count=sum(case.status == "failed" for case in cases),
        passed_count=len(passed),
        score_0_count=sum(score == 0 for score in scores),
        score_1_count=sum(score == 1 for score in scores),
        score_2_count=sum(score == 2 for score in scores),
        pass_rate=round(len(passed) / len(completed), 6) if completed else 0.0,
        average_score=round(sum(scores) / len(scores), 6) if scores else 0.0,
    )


def _metrics_for_console(result: JudgeRunResult) -> dict[str, object]:
    """只输出 Judge 汇总指标，不在控制台展开每个 case 的详细结果。"""
    payload = result.model_dump(mode="json")
    payload.pop("cases", None)
    return localize_console_keys(payload)


async def _evaluate_with_progress(
    judge: JudgeEvaluator,
    system_prompt: str,
    payload: dict[str, object],
    *,
    progress: Callable[[str], None],
    progress_message: str,
) -> JudgeVerdict:
    """等待外部 Judge API 时定期输出心跳，避免长时间没有可见反馈。"""
    task = asyncio.create_task(judge.evaluate(system_prompt, payload))
    waited_seconds = 0
    try:
        while True:
            done, _ = await asyncio.wait((task,), timeout=5)
            if done:
                return task.result()
            waited_seconds += 5
            progress(
                f"{progress_message}，Judge API 仍未返回（已等待约 {waited_seconds} 秒）"
            )
    except asyncio.CancelledError:
        task.cancel()
        raise


async def run_judge(
    dataset_name: str,
    *,
    mode: JudgeMode,
    prompt: str,
    config: JudgeConfig,
    evaluator: JudgeEvaluator | None = None,
    progress: Callable[[str], None] | None = None,
) -> JudgeRunResult:
    dataset = load_dataset(dataset_name)
    judge = evaluator or JudgeClient(config)
    system_prompt = _prompt_with_contract(prompt, mode)
    prompt_version = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]
    cases: list[JudgeCaseResult] = []
    total_cases = len(dataset.cases)

    if progress is not None:
        progress(
            f"开始 Judge 评测：dataset={dataset.name}，mode={mode}，共 {total_cases} 个 case。"
        )

    with tempfile.TemporaryDirectory(prefix="jobintel-judge-") as temporary:
        database_root = _prepare_database(Path(temporary))
        engine = DuckDbEngine(database_root)
        scope = _scope()
        reference_results: dict[str, dict[str, object]] = {}
        for index, case in enumerate(dataset.cases, start=1):
            if progress is not None:
                progress(f"[{index}/{total_cases}] 正在调用 Judge：{case.case_id}")
            if mode == "reference":
                reference = engine.execute("jobs_market", case.expected_sql, scope)
                if not reference.success:
                    raise RuntimeError(
                        f"Judge 参考 SQL 无法执行：{case.case_id}: {reference.error}"
                    )
                reference_results[case.case_id] = _execution_payload(reference)

            # 当前确定性数据契约中的 repair_sql 是纠错流程结束后的最终 SQL。
            # Judge 对外只暴露一个 final answer，不把首轮、修复或 few-shot 变体作为评测维度。
            final_sql = case.repair_sql
            final_result = engine.execute("jobs_market", final_sql, scope)
            payload: dict[str, object] = {
                "mode": mode,
                "case_id": case.case_id,
                "category": case.category,
                "query": case.query,
                "final_answer": final_sql,
                "final_execution": _execution_payload(final_result),
            }
            if mode == "reference":
                payload.update(
                    {
                        "reference_answer": case.expected_sql,
                        "reference_execution": reference_results[case.case_id],
                    }
                )

            started = time.perf_counter()
            try:
                if progress is None:
                    verdict = await judge.evaluate(system_prompt, payload)
                else:
                    verdict = await _evaluate_with_progress(
                        judge,
                        system_prompt,
                        payload,
                        progress=progress,
                        progress_message=f"[{index}/{total_cases}] 正在等待 Judge：{case.case_id}",
                    )
            except (RuntimeError, TypeError, ValueError) as exc:
                cases.append(
                    JudgeCaseResult(
                        case_id=case.case_id,
                        status="failed",
                        final_execution_success=final_result.success,
                        reference_execution_success=(
                            reference_results[case.case_id]["success"]
                            if mode == "reference"
                            else None
                        ),
                        latency_ms=(time.perf_counter() - started) * 1000,
                        error=f"{type(exc).__name__}: {_bounded(str(exc), 900)}",
                    )
                )
                if progress is not None:
                    progress(
                        f"[{index}/{total_cases}] Judge 调用失败：{case.case_id}，"
                        f"{type(exc).__name__}"
                    )
                continue

            cases.append(
                JudgeCaseResult(
                    case_id=case.case_id,
                    status="completed",
                    score=verdict.score,
                    passed=verdict.score == 2,
                    label=verdict.label,
                    error_type=verdict.error_type,
                    reason=verdict.reason,
                    final_execution_success=final_result.success,
                    reference_execution_success=(
                        reference_results[case.case_id]["success"]
                        if mode == "reference"
                        else None
                    ),
                    latency_ms=(time.perf_counter() - started) * 1000,
                )
            )
            if progress is not None:
                progress(
                    f"[{index}/{total_cases}] Judge 完成：{case.case_id}，score={verdict.score}"
                )

    summaries = [_summary(cases)]
    if progress is not None:
        progress(f"Judge 评测完成：{len(cases)}/{total_cases} 个 case 已处理。")
    return JudgeRunResult(
        runner_version=JUDGE_RUNNER_VERSION,
        dataset=dataset.name,
        dataset_size=len(dataset.cases),
        mode=mode,
        model=config.model,
        api_base_url=config.base_url,
        prompt_version=prompt_version,
        summaries=summaries,
        cases=cases,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 JobIntel 的可选 LLM-as-judge 评测")
    parser.add_argument("--dataset", choices=("seed", "eval_dev", "eval_test"), default="eval_test")
    parser.add_argument("--mode", choices=("single", "reference"), default="reference")
    parser.add_argument("--api-key", default=None, help="可选；更推荐使用 JUDGE_API_KEY")
    parser.add_argument("--api-url", "--base-url", dest="base_url", default=None)
    parser.add_argument("--model", default=None)
    prompt_group = parser.add_mutually_exclusive_group()
    prompt_group.add_argument("--prompt")
    prompt_group.add_argument("--prompt-file", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)

    mode: JudgeMode = args.mode
    env_prompt = os.getenv("JUDGE_PROMPT") or None
    env_prompt_file = os.getenv("JUDGE_PROMPT_FILE")
    prompt_value = args.prompt if args.prompt is not None else (
        None if args.prompt_file else env_prompt
    )
    prompt_file = args.prompt_file or (
        None if args.prompt else Path(env_prompt_file) if env_prompt_file else None
    )
    prompt = _load_prompt(
        mode=mode,
        prompt=prompt_value,
        prompt_file=prompt_file,
    )
    config = JudgeConfig.from_env(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )

    def report_progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)

    try:
        result = asyncio.run(
            run_judge(
                args.dataset,
                mode=mode,
                prompt=prompt,
                config=config,
                progress=report_progress,
            )
        )
    except KeyboardInterrupt:
        print(
            "已取消 Judge 评测；如需快速验证配置，请运行：make eval-judge-smoke",
            file=sys.stderr,
            flush=True,
        )
        return 130
    encoded = json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
    output_path = args.output or RESULTS_ROOT / f"judge_{args.dataset}_{args.mode}.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(encoded, encoding="utf-8")
    print("LLM-as-judge 评测指标：")
    print(json.dumps(_metrics_for_console(result), ensure_ascii=False, indent=2))
    print(f"完整 Judge 结果已写入：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
