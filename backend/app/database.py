from __future__ import annotations

"""OfferAgent 的岗位市场 Schema 契约。

真实数据由 ETL 发布到 ``data/databases/jobs_market``；本模块只负责加载已发布
Schema，并在本地快照尚不存在时提供同一份岗位数据契约，保证服务导入和权限计算
仍然是确定的。非岗位业务的静态 Schema 不属于 OfferAgent，不在这里保留。
"""

from pathlib import Path
from typing import Any


DEFAULT_DATABASE = "jobs_market"
DATABASE_LABELS = {
    DEFAULT_DATABASE: "岗位市场数据源",
}


def physical_table_name(table: dict[str, Any]) -> str:
    """返回 SQL 使用的物理表名，而不是 Schema 内部唯一 ID。"""

    return str(table.get("name") or table["id"])


SCHEMA: list[dict[str, Any]] = []
RELATIONS: list[dict[str, Any]] = []


def _load_generated_schema() -> None:
    """加载岗位市场发布 Schema；没有快照时回退到岗位 ETL 契约。"""

    from .etl.schema import declared_schema, load_market_schema
    from .models import SchemaTable

    data_root = Path(__file__).resolve().parents[2] / "data" / "databases"
    schema_path = data_root / "jobs_market" / "_schema.json"
    if schema_path.exists():
        payload = load_market_schema(schema_path.parent).model_dump(mode="json")
    else:
        payload = declared_schema().model_dump(mode="json")

    for table in payload["tables"]:
        SchemaTable.model_validate(table)
        if table.get("database") != DEFAULT_DATABASE:
            raise ValueError(f"岗位 Schema 数据库标识不一致：{table.get('id')}")
    SCHEMA.extend(payload["tables"])
    RELATIONS.extend(payload.get("relations", []))


_load_generated_schema()


__all__ = ["DATABASE_LABELS", "DEFAULT_DATABASE", "RELATIONS", "SCHEMA", "physical_table_name"]
