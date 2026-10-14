from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from eval.judge import JudgeClient, JudgeConfig

from .models import (
    RagAnswerCase,
    RagAnswerDataset,
    RagJudgeCaseResult,
    RagJudgeRunResult,
    RagJudgeSummary,
    RagJudgeVerdict,
)


RUNNER_VERSION = "1.0.0"
DEFAULT_PROMPT = Path(__file__).resolve().parents[1] / "prompts" / "rag_judge.txt"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "rag_judge.json"


class RagJudgeEvaluator(Protocol):
    async def evaluate_json(
        self,
        system_prompt: str,
        payload: dict[str, object],
    ) -> dict[str, object]: ...


def load_answer_dataset(path: Path) -> RagAnswerDataset:
    if not path.is_file():
        raise FileNotFoundError(f"RAG 答案评测集不存在：{path}")
    cases: list[RagAnswerCase] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            case = RagAnswerCase.model_validate(json.loads(raw_line))
        except (json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"RAG 答案评测集第 {line_number} 行无效：{path}") from exc
        if case.case_id in seen:
            raise ValueError(f"RAG 答案评测集存在重复 case_id：{case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return RagAnswerDataset(name="answer_set", cases=cases)


def _prompt_with_contract(prompt: str) -> str:
    return prompt.rstrip() + (
        "\n\n再次提醒：只返回与契约完全一致的 JSON 对象，所有列表元素必须是字符串。"
    )


def _bounded(value: str, limit: int = 900) -> str:
    return value[:limit]


def _summary(cases: Sequence[RagJudgeCaseResult]) -> RagJudgeSummary:
    completed = [case for case in cases if case.status == "completed"]
    faithfulness = [case.faithfulness_score for case in completed if case.faithfulness_score is not None]
    relevance = [case.relevance_score for case in completed if case.relevance_score is not None]
    both_passed = [
        case
        for case in completed
        if case.faithfulness_score == 2 and case.relevance_score == 2
    ]
    return RagJudgeSummary(
        requested_count=len(cases),
        completed_count=len(completed),
        failed_count=len(cases) - len(completed),
        faithfulness_pass_rate=(sum(score == 2 for score in faithfulness) / len(faithfulness)) if faithfulness else 0.0,
        relevance_pass_rate=(sum(score == 2 for score in relevance) / len(relevance)) if relevance else 0.0,
        joint_pass_rate=len(both_passed) / len(completed) if completed else 0.0,
        average_faithfulness=sum(faithfulness) / len(faithfulness) if faithfulness else 0.0,
        average_relevance=sum(relevance) / len(relevance) if relevance else 0.0,
    )


async def run_judge(
    answer_path: Path,
    *,
    prompt: str,
    config: JudgeConfig,
    evaluator: RagJudgeEvaluator | None = None,
    progress: Callable[[str], None] | None = None,
) -> RagJudgeRunResult:
    dataset = load_answer_dataset(answer_path)
    judge = evaluator or JudgeClient(config)
    system_prompt = _prompt_with_contract(prompt)
    prompt_version = hashlib.sha256(system_prompt.encode("utf-8")).hexdigest()[:12]
    cases: list[RagJudgeCaseResult] = []
    if progress is not None:
        progress(f"开始 RAG Judge 评测：共 {len(dataset.cases)} 个 case。")

    for index, case in enumerate(dataset.cases, start=1):
        if progress is not None:
            progress(f"[{index}/{len(dataset.cases)}] 正在调用 Qwen Judge：{case.case_id}")
        started = time.perf_counter()
        payload = case.model_dump(mode="json")
        try:
            raw = await judge.evaluate_json(system_prompt, payload)
            verdict = RagJudgeVerdict.model_validate(raw)
        except (RuntimeError, TypeError, ValueError) as exc:
            cases.append(
                RagJudgeCaseResult(
                    case_id=case.case_id,
                    status="failed",
                    latency_ms=(time.perf_counter() - started) * 1000,
                    error=f"{type(exc).__name__}: {_bounded(str(exc))}",
                )
            )
            if progress is not None:
                progress(f"[{index}/{len(dataset.cases)}] Judge 调用失败：{case.case_id}")
            continue
        cases.append(
            RagJudgeCaseResult(
                case_id=case.case_id,
                status="completed",
                faithfulness_score=verdict.faithfulness_score,
                relevance_score=verdict.relevance_score,
                unsupported_claims=verdict.unsupported_claims,
                missing_aspects=verdict.missing_aspects,
                reason=verdict.reason,
                latency_ms=(time.perf_counter() - started) * 1000,
            )
        )
        if progress is not None:
            progress(
                f"[{index}/{len(dataset.cases)}] Judge 完成：{case.case_id}，"
                f"faithfulness={verdict.faithfulness_score}，relevance={verdict.relevance_score}"
            )

    result = RagJudgeRunResult(
        runner_version=RUNNER_VERSION,
        dataset=dataset.name,
        dataset_size=len(dataset.cases),
        model=config.model,
        api_base_url=config.base_url,
        prompt_version=prompt_version,
        summaries=[_summary(cases)],
        cases=cases,
    )
    if progress is not None:
        progress(f"RAG Judge 评测完成：{len(cases)}/{len(dataset.cases)} 个 case 已处理。")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用已有 Qwen Judge API 评估 RAG 答案")
    parser.add_argument("--answers", type=Path, required=True)
    parser.add_argument("--prompt-file", type=Path, default=DEFAULT_PROMPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--model", default=None)
    args = parser.parse_args(argv)

    config = JudgeConfig.from_env(
        api_key=args.api_key,
        base_url=args.base_url,
        model=args.model,
    )
    prompt = args.prompt_file.read_text(encoding="utf-8")
    result = asyncio.run(
        run_judge(
            args.answers,
            prompt=prompt,
            config=config,
            progress=print,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
