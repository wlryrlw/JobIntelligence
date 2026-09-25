from __future__ import annotations

import re
from collections.abc import Iterable

from ..database import SCHEMA
from ..etl.schema import LABELS

_AGGREGATE_RE = re.compile(r"^(avg|average|sum|min|max|count)\s*\(\s*(.*?)\s*\)$", re.IGNORECASE)
_AGGREGATE_ALIAS_RE = re.compile(r"^(avg|average|sum|min|max|count)_(.+)$", re.IGNORECASE)
_AGGREGATE_LABELS = {
    "avg": "平均",
    "average": "平均",
    "sum": "合计",
    "min": "最低",
    "max": "最高",
    "count": "数量",
}


class ColumnLabelResolver:
    """将内部 SQL 列名转换为面向用户的中文列名，同时保留原始列名用于取值。"""

    def __init__(self) -> None:
        self.labels = {
            str(field["name"]): str(field.get("label") or field["name"])
            for table in SCHEMA
            for field in table.get("fields", [])
            if field.get("name")
        }
        self.labels.update({str(name): str(label) for name, label in LABELS.items()})

    def resolve(self, columns: Iterable[str]) -> dict[str, str]:
        return {column: self._resolve_one(column) for column in columns}

    def _resolve_one(self, column: str) -> str:
        raw = column.strip()
        if not raw or any("\u4e00" <= char <= "\u9fff" for char in raw):
            return raw
        if raw in self.labels:
            return self.labels[raw]
        match = _AGGREGATE_RE.match(raw) or _AGGREGATE_ALIAS_RE.match(raw)
        if match:
            operation, inner = match.groups()
            if operation.lower() == "count" and inner in {"", "*", "1", "rows", "job_id"}:
                return "岗位数量"
            inner_label = "岗位" if inner in {"", "*", "1"} else self._resolve_one(inner)
            return f"{_AGGREGATE_LABELS[operation.lower()]}{inner_label}"
        if raw.lower() in {"count", "count(*)", "count(1)", "row_count", "job_count"}:
            return "岗位数量"
        return raw


_RESOLVER = ColumnLabelResolver()


def column_labels(columns: Iterable[str]) -> dict[str, str]:
    return _RESOLVER.resolve(columns)
