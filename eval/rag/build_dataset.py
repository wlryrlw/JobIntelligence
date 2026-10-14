from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))

from app.rag.service import JdRagService

from .dataset import (
    DEFAULT_COVERAGE,
    DEFAULT_DATABASE_ROOT,
    DEFAULT_GOLDEN_SET,
    _source_digest,
    build_coverage,
    load_golden_set,
    load_job_rows,
    validate_golden_set,
    write_coverage,
)


def build_current_coverage(database_root: Path):
    rows = load_job_rows(database_root)
    service = JdRagService(database_root=database_root)
    documents = service.load_documents()
    coverage = build_coverage(
        rows,
        jd_count=len({document.job_id for document in documents}),
        document_count=len(documents),
        source_digest=_source_digest(database_root),
    )
    validate_golden_set(load_golden_set(), rows, coverage)
    return coverage


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="根据当前岗位快照生成并校验 RAG 数据覆盖报告")
    parser.add_argument("--database-root", type=Path, default=DEFAULT_DATABASE_ROOT)
    parser.add_argument("--golden-set", type=Path, default=DEFAULT_GOLDEN_SET)
    parser.add_argument("--output", type=Path, default=DEFAULT_COVERAGE)
    args = parser.parse_args(argv)

    rows = load_job_rows(args.database_root)
    documents = JdRagService(database_root=args.database_root).load_documents()
    coverage = build_coverage(
        rows,
        jd_count=len({document.job_id for document in documents}),
        document_count=len(documents),
        source_digest=_source_digest(args.database_root),
    )
    validate_golden_set(load_golden_set(args.golden_set), rows, coverage)
    write_coverage(coverage, args.output)
    print(json.dumps(coverage.model_dump(mode="json"), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
