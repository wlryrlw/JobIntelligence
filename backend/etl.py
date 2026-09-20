"""在项目根目录运行：uv run python backend/etl.py --jobs <列表JSON> --details <详情JSON>。"""
from __future__ import annotations

import argparse
from pathlib import Path

from app.etl.pipeline import run_etl


ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser(description="岗位 JSON → 版本化 CSV / Schema / 数据质量报告")
    parser.add_argument("--jobs", type=Path, required=True)
    parser.add_argument("--details", type=Path)
    parser.add_argument("--database-root", type=Path, default=ROOT / "data" / "databases")
    parser.add_argument("--raw-root", type=Path, default=ROOT / "data" / "local" / "etl")
    args = parser.parse_args()
    report = run_etl(args.jobs, args.details, args.database_root, args.raw_root)
    print(report.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
