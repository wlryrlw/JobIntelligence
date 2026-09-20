
from pathlib import Path
from typing import Any

from ..contracts import SchemaCatalog, SchemaCatalogField, SchemaCatalogTable
from ..database import RELATIONS, SCHEMA, physical_table_name
from ..etl.schema import load_market_schema
from ..security import AccessScope
from .duckdb_engine import DuckDbEngine

_TYPE_MAP = {"数值": "DOUBLE", "整数": "BIGINT", "布尔": "BOOLEAN", "日期": "TIMESTAMP"}
_SENSITIVE_SAMPLE_FIELDS = {"jd_text", "evidence_text", "job_link", "company", "title"}


class SchemaCatalogBuilder:
    """从已发布 Schema 构建全量、确定性的 SQL Prompt 上下文。"""

    def __init__(self, database_root: Path | None = None) -> None:
        self.engine = DuckDbEngine(database_root)

    def build(self, database: str, access_scope: AccessScope | dict[str, Any] | None = None) -> SchemaCatalog:
        scope = (
            access_scope if isinstance(access_scope, AccessScope)
            else AccessScope.from_dict(access_scope) if access_scope is not None else None
        )
        if scope and not scope.allows_database(database):
            raise PermissionError(f"当前用户无权访问数据库：{database}")
        folder = self.engine._database_folder(database)
        definitions, version, relations = self._definitions(database, folder)
        tables = []
        for raw_table in definitions:
            table_id = str(raw_table.get("id") or raw_table.get("name"))
            if scope and not scope.allows_table(database, table_id):
                continue
            fields = []
            for raw_field in raw_table.get("fields") or []:
                name = str(raw_field.get("name") or "")
                if not name:
                    continue
                profile = raw_field.get("data_profile") or {}
                if hasattr(profile, "model_dump"):
                    profile = profile.model_dump(mode="json")
                samples = [] if name in _SENSITIVE_SAMPLE_FIELDS else [
                    str(item)[:120] for item in (profile.get("sample_values") or [])[:5]
                ]
                fields.append(SchemaCatalogField(
                    table=physical_table_name(raw_table),
                    name=name,
                    label=str(raw_field.get("label") or name),
                    type=str(raw_field.get("type") or "文本"),
                    source_type=str(raw_field.get("source_type") or _TYPE_MAP.get(str(raw_field.get("type") or ""), "VARCHAR")),
                    description=str(raw_field.get("description") or ""),
                    aliases=[str(item) for item in raw_field.get("aliases") or [] if str(item)],
                    role=str(raw_field.get("role") or "dimension"),
                    aggregation=str(raw_field.get("aggregation") or "group"),
                    samples=samples,
                    data_profile=dict(profile),
                ))
            tables.append(SchemaCatalogTable(
                name=physical_table_name(raw_table),
                label=str(raw_table.get("label") or table_id),
                description=str(raw_table.get("description") or ""),
                database=database,
                primary_key=[str(item) for item in raw_table.get("primary_key") or []],
                row_count=int(raw_table.get("row_count") or 0),
                fields=fields,
            ))
        visible_names = {table.name for table in tables}
        visible_ids = {
            value
            for raw in definitions
            if physical_table_name(raw) in visible_names
            for value in (
                str(raw.get("id") or raw.get("name")),
                str(raw.get("id") or raw.get("name")).rsplit(".", 1)[-1],
                physical_table_name(raw),
            )
        }
        catalog_relations = []
        for relation in relations:
            left = str(relation.get("left_table") or "").rsplit(".", 1)[-1]
            right = str(relation.get("right_table") or "").rsplit(".", 1)[-1]
            if left in visible_ids and right in visible_ids:
                catalog_relations.append({
                    "left_table": self._physical_name(definitions, left),
                    "left_field": str(relation.get("left_field") or ""),
                    "right_table": self._physical_name(definitions, right),
                    "right_field": str(relation.get("right_field") or ""),
                    "description": str(relation.get("description") or ""),
                })
        return SchemaCatalog(
            database=database, dataset_version=version, tables=tables, relations=catalog_relations
        ).with_prompt_stats()

    def _definitions(self, database: str, folder: Path):
        if database == "jobs_market" and (folder / "_schema.json").exists():
            market = load_market_schema(folder)
            return (
                [table.model_dump(mode="json") for table in market.tables],
                market.dataset_version,
                [relation.model_dump(mode="json") for relation in market.relations],
            )
        definitions = [table for table in SCHEMA if str(table.get("database") or "askdata_mock") == database]
        names = {str(table.get("id") or table.get("name")) for table in definitions}
        relations = [
            relation for relation in RELATIONS
            if str(relation.get("left_table") or "").rsplit(".", 1)[-1] in names
            and str(relation.get("right_table") or "").rsplit(".", 1)[-1] in names
        ]
        return definitions, "static", relations

    @staticmethod
    def _physical_name(definitions: list[dict[str, Any]], table_id: str) -> str:
        raw = next(
            (
                table for table in definitions
                if table_id in {
                    str(table.get("id") or table.get("name")),
                    str(table.get("id") or table.get("name")).rsplit(".", 1)[-1],
                    physical_table_name(table),
                }
            ),
            None,
        )
        return physical_table_name(raw) if raw else table_id


def build_schema_catalog(
    database: str,
    database_root: Path | None = None,
    access_scope: AccessScope | dict[str, Any] | None = None,
) -> SchemaCatalog:
    return SchemaCatalogBuilder(database_root).build(database, access_scope)


__all__ = ["SchemaCatalogBuilder", "build_schema_catalog"]
