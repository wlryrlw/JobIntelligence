from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from .models import EvalCase, EvalDataset


DATA_ROOT = Path(__file__).resolve().parent / "data"


def load_dataset(name: str, root: Path = DATA_ROOT) -> EvalDataset:
    if name not in {"seed", "eval_dev", "eval_test"}:
        raise ValueError("dataset 必须是 seed、eval_dev 或 eval_test")
    path = root / f"{name}.jsonl"
    if not path.is_file():
        raise FileNotFoundError(f"评测数据集不存在：{path}")
    cases: list[EvalCase] = []
    seen: set[str] = set()
    for line_number, raw_line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw_line.strip():
            continue
        try:
            case = EvalCase.model_validate(json.loads(raw_line))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise ValueError(f"评测数据集 {path} 第 {line_number} 行无效") from exc
        for index in range(1, case.repeat + 1):
            case_id = case.case_id if case.repeat == 1 else f"{case.case_id}-{index:02d}"
            if case_id in seen:
                raise ValueError(f"评测数据集存在重复 case_id：{case_id}")
            seen.add(case_id)
            cases.append(case.model_copy(update={"case_id": case_id, "repeat": 1}))
    dataset = EvalDataset(name=name, cases=cases)
    if name != "seed" and len(dataset.cases) < 10:
        raise ValueError(f"{name} 至少需要 10 个 case，当前只有 {len(dataset.cases)} 个")
    return dataset


def load_all_datasets(root: Path = DATA_ROOT) -> dict[str, EvalDataset]:
    datasets = {name: load_dataset(name, root) for name in ("seed", "eval_dev", "eval_test")}
    all_ids = [case.case_id for dataset in datasets.values() for case in dataset.cases]
    if len(all_ids) != len(set(all_ids)):
        raise ValueError("三路评测数据集之间存在重复 case_id")
    if len(datasets["eval_dev"].cases) + len(datasets["eval_test"].cases) < 50:
        raise ValueError("eval_dev + eval_test 至少需要 50 个 NL2SQL case")
    return datasets
