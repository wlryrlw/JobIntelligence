from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings
from app.contracts import TargetJobRequest
from app.career.agent import CareerAgent
from app.career.profile import JobProfileBuilder
from app.rag.service import JdRagService

from .dataset import (
    DEFAULT_DATABASE_ROOT,
    DEFAULT_GOLDEN_SET,
    _source_digest,
    build_coverage,
    load_golden_set,
    load_job_rows,
    validate_golden_set,
)
from .models import RagAnswerCase, RagEvidence, RagAnswerDataset


DEFAULT_OUTPUT = Path(__file__).resolve().parent / "answers.jsonl"


def generate_answers(
    *,
    service: JdRagService,
    dataset_path: Path = DEFAULT_GOLDEN_SET,
    top_k: int = 8,
) -> RagAnswerDataset:
    dataset = load_golden_set(dataset_path)
    rows = load_job_rows(service.database_root)
    documents = service.load_documents()
    coverage = build_coverage(
        rows,
        jd_count=len({document.job_id for document in documents}),
        document_count=len(documents),
        source_digest=_source_digest(service.database_root),
    )
    validate_golden_set(dataset, rows, coverage)
    profile_builder = JobProfileBuilder(service.database_root, use_llm=False)
    career_agent = CareerAgent(profile_builder=profile_builder, use_llm=False)
    answers: list[RagAnswerCase] = []
    for case in dataset.cases:
        target = TargetJobRequest(**case.target.model_dump(mode="json"))
        hits = service.retrieve_variant(
            case.query,
            target,
            variant="full",
            top_k=top_k,
            dataset_version=coverage.dataset_version,
        )
        if case.answerable and not hits:
            raise RuntimeError(
                f"Golden Set case {case.case_id} 标记为 answerable，但完整检索没有返回证据"
            )
        if not case.answerable and hits:
            raise RuntimeError(
                f"Golden Set case {case.case_id} 标记为 no_data，但完整检索返回了证据"
            )
        if hits:
            profile = career_agent.build_profile(target)
            answer = career_agent.answer_job_requirement(case.query, profile, hits)
            evidence = [
                RagEvidence(
                    chunk_id=hit.chunk_id,
                    job_id=hit.job_id,
                    title=hit.title,
                    company=hit.company,
                    city=hit.city,
                    experience=hit.experience,
                    snippet=hit.text[:1200],
                )
                for hit in hits
            ]
        else:
            answer = (
                f"当前岗位库没有满足“{case.target.city or '目标城市'} "
                f"{case.target.experience or '目标经验'}”的可用 Agent JD 样本，"
                "因此不能基于当前数据总结该范围的岗位要求。"
            )
            evidence = []
        answers.append(
            RagAnswerCase(
                case_id=case.case_id,
                query=case.query,
                answer=answer,
                evidence=evidence,
                required_aspects=case.required_aspects,
                answerable=case.answerable,
            )
        )
    return RagAnswerDataset(name="answer_set", cases=answers)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="使用当前 CareerAgent 生成 RAG Judge 答案集")
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--top-k", type=int, default=8)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)
    if args.top_k < 1:
        raise ValueError("--top-k 必须大于 0")
    dataset = generate_answers(
        service=JdRagService(settings, database_root=args.database_root),
        dataset_path=args.dataset,
        top_k=args.top_k,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "\n".join(
            json.dumps(case.model_dump(mode="json"), ensure_ascii=False)
            for case in dataset.cases
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已生成 {len(dataset.cases)} 个 RAG Judge 答案：{args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
