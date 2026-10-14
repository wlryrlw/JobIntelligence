from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.config import settings
from app.contracts import TargetJobRequest
from app.rag.models import RagRetrievalVariant
from app.rag.service import JdRagService

from .dataset import (
    DEFAULT_DATABASE_ROOT,
    DEFAULT_GOLDEN_SET,
    build_coverage,
    load_golden_set,
    load_job_rows,
    _source_digest,
    validate_golden_set,
)
from .metrics import score_case, summarize
from .models import RagEvalVariant, RetrievalRunResult


RUNNER_VERSION = "1.0.0"
DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "results" / "rag_retrieval.json"
VARIANT_MAP: dict[RagEvalVariant, RagRetrievalVariant] = {
    "A": "bm25",
    "B": "embedding",
    "D": "full",
}


def run_retrieval_evaluation(
    *,
    service: JdRagService,
    dataset_path: Path = DEFAULT_GOLDEN_SET,
    variants: tuple[RagEvalVariant, ...] = ("A", "B", "D"),
    top_k: int = 20,
) -> RetrievalRunResult:
    if top_k < 20:
        raise ValueError("检索评测 top_k 至少为 20，才能同时计算 Recall@20")
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
    if "D" in variants and not service.config.rag_rerank_enabled:
        raise RuntimeError(
            "消融组 D 定义为 BM25 + Embedding + RRF + Rerank；请先设置 "
            "RAG_RERANK_ENABLED=true 并填写 RAG_RERANK_MODEL。"
        )

    metrics = []
    summaries = []
    for variant in variants:
        variant_metrics = []
        for case in dataset.cases:
            target = TargetJobRequest(**case.target.model_dump(mode="json"))
            hits = service.retrieve_variant(
                case.query,
                target,
                variant=VARIANT_MAP[variant],
                top_k=top_k,
                dataset_version=coverage.dataset_version,
            )
            variant_metrics.append(score_case(case, hits, variant))
        metrics.extend(variant_metrics)
        summaries.append(summarize(variant_metrics, variant))

    return RetrievalRunResult(
        runner_version=RUNNER_VERSION,
        dataset=dataset.name,
        dataset_size=len(dataset.cases),
        dataset_version=coverage.dataset_version,
        top_k=top_k,
        summaries=summaries,
        cases=metrics,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行岗位 JD RAG A/B/D 检索评测")
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--variant", choices=("A", "B", "D", "all"), default="all")
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    variants: tuple[RagEvalVariant, ...]
    if args.variant == "all":
        variants = ("A", "B", "D")
    else:
        variants = (args.variant,)
    result = run_retrieval_evaluation(
        service=JdRagService(settings, database_root=args.database_root),
        dataset_path=args.dataset,
        variants=variants,
        top_k=args.top_k,
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
