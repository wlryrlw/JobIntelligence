from __future__ import annotations

import math
from collections.abc import Iterable

from app.rag.models import RetrievedJd

from .models import RagEvalCase, RagEvalVariant, RetrievalCaseMetric, RetrievalVariantSummary, RetrievedJobRank


def _ranked_hits(hits: Iterable[RetrievedJd]) -> tuple[list[str], list[RetrievedJobRank]]:
    ranked_job_ids: list[str] = []
    ranked: list[RetrievedJobRank] = []
    seen: set[str] = set()
    for index, hit in enumerate(hits, start=1):
        if hit.job_id in seen:
            continue
        seen.add(hit.job_id)
        ranked_job_ids.append(hit.job_id)
        ranked.append(
            RetrievedJobRank(
                job_id=hit.job_id,
                chunk_id=hit.chunk_id,
                city=hit.city,
                experience=hit.experience,
                rank=len(ranked) + 1,
            )
        )
    return ranked_job_ids, ranked


def _recall(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / len(relevant)


def _precision(ranked: list[str], relevant: set[str], k: int) -> float:
    return len(set(ranked[:k]) & relevant) / k


def _mrr(ranked: list[str], relevant: set[str], k: int) -> float:
    for index, job_id in enumerate(ranked[:k], start=1):
        if job_id in relevant:
            return 1 / index
    return 0.0


def _ndcg(ranked: list[str], relevant: set[str], k: int) -> float:
    def gain(index: int, job_id: str) -> float:
        return (1.0 if job_id in relevant else 0.0) / math.log2(index + 2)

    actual = sum(gain(index, job_id) for index, job_id in enumerate(ranked[:k]))
    ideal = sum(1.0 / math.log2(index + 2) for index in range(min(k, len(relevant))))
    return actual / ideal if ideal else 0.0


def score_case(
    case: RagEvalCase,
    hits: list[RetrievedJd],
    variant: RagEvalVariant,
) -> RetrievalCaseMetric:
    ranked_job_ids, ranked_hits = _ranked_hits(hits)
    relevant = set(case.relevant_job_ids)
    filter_violation = any(
        (case.target.city and hit.city != case.target.city)
        or (case.target.experience and hit.experience != case.target.experience)
        for hit in hits
    )
    if not case.answerable:
        return RetrievalCaseMetric(
            case_id=case.case_id,
            variant=variant,
            answerable=False,
            relevant_count=0,
            ranked_job_ids=ranked_job_ids,
            hits=ranked_hits,
            no_data_correct=not ranked_job_ids,
            filter_violation=filter_violation,
        )
    return RetrievalCaseMetric(
        case_id=case.case_id,
        variant=variant,
        answerable=True,
        relevant_count=len(relevant),
        ranked_job_ids=ranked_job_ids,
        hits=ranked_hits,
        recall_at_5=_recall(ranked_job_ids, relevant, 5),
        recall_at_10=_recall(ranked_job_ids, relevant, 10),
        recall_at_20=_recall(ranked_job_ids, relevant, 20),
        precision_at_8=_precision(ranked_job_ids, relevant, 8),
        mrr_at_10=_mrr(ranked_job_ids, relevant, 10),
        ndcg_at_10=_ndcg(ranked_job_ids, relevant, 10),
        filter_violation=filter_violation,
    )


def _average(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None


def summarize(
    cases: list[RetrievalCaseMetric],
    variant: RagEvalVariant,
) -> RetrievalVariantSummary:
    answerable = [case for case in cases if case.answerable]
    no_data = [case for case in cases if not case.answerable]
    return RetrievalVariantSummary(
        variant=variant,
        total_cases=len(cases),
        answerable_cases=len(answerable),
        no_data_cases=len(no_data),
        recall_at_5=_average([case.recall_at_5 for case in answerable if case.recall_at_5 is not None]),
        recall_at_10=_average([case.recall_at_10 for case in answerable if case.recall_at_10 is not None]),
        recall_at_20=_average([case.recall_at_20 for case in answerable if case.recall_at_20 is not None]),
        precision_at_8=_average([case.precision_at_8 for case in answerable if case.precision_at_8 is not None]),
        mrr_at_10=_average([case.mrr_at_10 for case in answerable if case.mrr_at_10 is not None]),
        ndcg_at_10=_average([case.ndcg_at_10 for case in answerable if case.ndcg_at_10 is not None]),
        no_data_accuracy=_average([1.0 if case.no_data_correct else 0.0 for case in no_data]),
        filter_violation_rate=round(
            sum(1.0 for case in cases if case.filter_violation) / len(cases), 6
        ),
    )
