from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter
from pathlib import Path

from pydantic import ValidationError

from .models import DataCoverage, RagEvalCase, RagEvalDataset


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATABASE_ROOT = REPO_ROOT / "data" / "databases"
DEFAULT_GOLDEN_SET = Path(__file__).resolve().parent / "golden_set.jsonl"
DEFAULT_COVERAGE = Path(__file__).resolve().parent / "data_coverage.json"


def jobs_market_dir(database_root: Path) -> Path:
    resolved = database_root.resolve()
    return resolved if resolved.name == "jobs_market" else resolved / "jobs_market"


def load_job_rows(database_root: Path = DEFAULT_DATABASE_ROOT) -> list[dict[str, str]]:
    folder = jobs_market_dir(database_root)
    path = folder / "jobs.csv"
    if not path.is_file():
        raise FileNotFoundError(f"岗位快照不存在：{path}")
    with path.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ValueError(f"岗位快照为空：{path}")
    required = {"job_id", "city", "experience", "keyword", "dataset_version"}
    missing = required - set(rows[0])
    if missing:
        raise ValueError(f"岗位快照缺少字段：{', '.join(sorted(missing))}")
    return rows


def _source_digest(database_root: Path) -> str:
    folder = jobs_market_dir(database_root)
    digest = hashlib.sha256()
    for filename in ("jobs.csv", "job_details.csv"):
        path = folder / filename
        if not path.is_file():
            raise FileNotFoundError(f"岗位数据文件不存在：{path}")
        digest.update(filename.encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def build_coverage(
    rows: list[dict[str, str]],
    *,
    jd_count: int,
    document_count: int,
    source_digest: str,
) -> DataCoverage:
    versions = {row["dataset_version"] for row in rows if row["dataset_version"]}
    if len(versions) != 1:
        raise ValueError(f"岗位快照必须只有一个 dataset_version，当前为：{sorted(versions)}")
    city_counts = Counter(row["city"] or "未知" for row in rows)
    experience_counts = Counter(row["experience"] or "未知" for row in rows)
    keyword_counts = Counter(row["keyword"] or "未知" for row in rows)
    city_experience_counts = Counter(
        f"{row['city'] or '未知'}|{row['experience'] or '未知'}" for row in rows
    )
    return DataCoverage(
        source_digest=source_digest,
        dataset_version=next(iter(versions)),
        job_count=len(rows),
        jd_count=jd_count,
        document_count=document_count,
        city_counts=dict(sorted(city_counts.items())),
        experience_counts=dict(sorted(experience_counts.items())),
        keyword_counts=dict(sorted(keyword_counts.items())),
        city_experience_counts=dict(sorted(city_experience_counts.items())),
    )


def load_golden_set(path: Path = DEFAULT_GOLDEN_SET) -> RagEvalDataset:
    if not path.is_file():
        raise FileNotFoundError(f"RAG Golden Set 不存在：{path}")
    cases: list[RagEvalCase] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            case = RagEvalCase.model_validate(json.loads(raw_line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"RAG Golden Set 第 {line_number} 行无效：{path}") from exc
        if case.case_id in seen:
            raise ValueError(f"RAG Golden Set 存在重复 case_id：{case.case_id}")
        seen.add(case.case_id)
        cases.append(case)
    return RagEvalDataset(name="golden_set", cases=cases)


def validate_golden_set(
    dataset: RagEvalDataset,
    rows: list[dict[str, str]],
    coverage: DataCoverage,
) -> None:
    jobs = {row["job_id"]: row for row in rows}
    for case in dataset.cases:
        if case.source_dataset_version != coverage.dataset_version:
            raise ValueError(
                f"case {case.case_id} 的 dataset_version={case.source_dataset_version} "
                f"与当前数据={coverage.dataset_version} 不一致；请重建评测集"
            )
        if any(job_id not in jobs for job_id in case.relevant_job_ids):
            unknown = sorted(job_id for job_id in case.relevant_job_ids if job_id not in jobs)
            raise ValueError(f"case {case.case_id} 引用了不存在的 job_id：{unknown}")
        if case.answerable != bool(case.relevant_job_ids):
            raise ValueError(
                f"case {case.case_id} 的 answerable 必须与 relevant_job_ids 是否为空一致"
            )
        for job_id in case.relevant_job_ids:
            row = jobs[job_id]
            if case.target.city and row["city"] != case.target.city:
                raise ValueError(f"case {case.case_id} 的 relevant job 不满足 city 过滤：{job_id}")
            if case.target.experience and row["experience"] != case.target.experience:
                raise ValueError(
                    f"case {case.case_id} 的 relevant job 不满足 experience 过滤：{job_id}"
                )


def write_coverage(coverage: DataCoverage, path: Path = DEFAULT_COVERAGE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(coverage.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
