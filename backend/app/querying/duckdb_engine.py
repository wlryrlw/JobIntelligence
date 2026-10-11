from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterator

import duckdb
import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from ..config import BASE_DIR
from ..database import DEFAULT_DATABASE, SCHEMA, physical_table_name
from ..etl.schema import csv_expression, load_market_schema
from ..security import AccessScope
from .models import SqlExecution
from .sql_errors import classify_sql_error


DATABASE_ROOT = BASE_DIR.parent / "data" / "databases"


class DuckDbEngine:
    """将 CSV 目录映射为只读 DuckDB 数据库。

    CSV 文件名作为 SQL 表名使用。
    """

    def __init__(self, database_root: Path | None = None) -> None:
        self.database_root = database_root or DATABASE_ROOT

    def execute(
        self,
        database: str,
        sql: str,
        access_scope: AccessScope | None = None,
    ) -> SqlExecution:
        try:
            safe_sql = self._validate_sql(database, sql, access_scope)
            with self.connect(database) as connection:
                cursor = connection.execute(safe_sql)
                raw_rows = cursor.fetchmany(201)
                columns = [item[0] for item in cursor.description or []]
                rows = [
                    {column: self._json_value(value) for column, value in zip(columns, row)}
                    for row in raw_rows[:200]
                ]
            return SqlExecution(safe_sql, True, columns, rows)
        except (ValueError, ParseError, duckdb.Error, OSError) as exc:
            kind, retryable = classify_sql_error(exc)
            return SqlExecution(
                sql,
                False,
                error=str(exc),
                error_kind=kind.value,
                retryable=retryable,
            )

    @contextmanager
    def connect(self, database: str) -> Iterator[duckdb.DuckDBPyConnection]:
        """创建内存连接并将 CSV 文件注册为只读视图。"""
        folder = self._database_folder(database)
        connection = duckdb.connect(":memory:")
        try:
            csv_files = sorted(folder.glob("*.csv"))
            if not csv_files:
                raise ValueError(f"数据库文件夹没有CSV表：{folder}")
            market = load_market_schema(folder) if database == "jobs_market" else None
            declared = {table.name: table for table in market.tables} if market else {}
            if market and {p.stem for p in csv_files} != set(declared):
                raise ValueError("岗位 CSV 表清单与 Schema 不一致")
            for csv_path in csv_files:
                table = csv_path.stem
                if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table):
                    raise ValueError(f"CSV文件名不能作为安全表名：{csv_path.name}")
                path = csv_path.resolve().as_posix().replace("'", "''")
                expression = (csv_expression(csv_path, declared[table].fields) if market
                              else f"read_csv_auto('{path}', header=true, sample_size=-1)")
                connection.execute(f'CREATE VIEW "{table}" AS SELECT * FROM {expression}')
            # 文件扫描仅用于可信视图注册；用户 SQL 执行前关闭外部访问。
            connection.execute("SET allowed_paths = ?", [[str(p.resolve()) for p in csv_files]])
            connection.execute("SET enable_external_access = false")
            yield connection
        finally:
            connection.close()

    def validate_sql(
        self,
        database: str,
        sql: str,
        access_scope: AccessScope | None = None,
    ) -> str:
        """公开 SQL 安全校验入口，供 QueryAgent 在执行前记录 validation。"""

        return self._validate_sql(database, sql, access_scope)

    def _validate_sql(
        self,
        database: str,
        sql: str,
        access_scope: AccessScope | None = None,
    ) -> str:
        cleaned = self.clean_sql(sql).rstrip(";").strip()
        statements = [item for item in sqlglot.parse(cleaned, read="duckdb") if item]
        if len(statements) != 1:
            raise ValueError("一次只允许执行一条SQL")
        statement = statements[0]
        if not isinstance(statement, exp.Query):
            raise ValueError("只允许执行SELECT/WITH只读查询")
        forbidden_nodes = {
            "insert", "update", "delete", "merge", "create", "drop", "alter",
            "copy", "attach", "detach", "command", "transaction", "grant", "revoke",
        }
        if any(node.key in forbidden_nodes for node in statement.walk()):
            raise ValueError("SQL包含禁止的写入或管理操作")

        # 查询必须显式列出返回字段。
        for select in statement.find_all(exp.Select):
            if any(
                isinstance(projection, exp.Star)
                or isinstance(projection, exp.Column) and projection.is_star
                for projection in select.expressions
            ):
                raise ValueError("不允许使用SELECT *，必须明确列出查询字段")

        if access_scope and not access_scope.allows_database(database):
            raise ValueError("当前用户无权访问该数据库")

        database_tables = [
            table
            for table in SCHEMA
            if table.get("database", DEFAULT_DATABASE) == database
        ]
        allowed = {
            physical_table_name(table): table["id"]
            for table in database_tables
        }
        for table in statement.find_all(exp.Table):
            if not isinstance(table.this, exp.Identifier) or table.db or table.catalog:
                raise ValueError("只允许查询白名单表，不允许文件函数或跨库访问")
        cte_names = {cte.alias_or_name for cte in statement.find_all(exp.CTE)}
        referenced = {
            table.name
            for table in statement.find_all(exp.Table)
            if table.name not in cte_names
        }
        unknown = {name for name in referenced if name not in allowed}
        if unknown:
            raise ValueError(f"SQL引用了未知数据表：{', '.join(sorted(unknown))}")
        if access_scope:
            denied = {
                table
                for table in referenced
                if not access_scope.allows_table(database, allowed[table])
            }
            if denied:
                raise ValueError("SQL引用了当前用户无权访问的数据表")
        return cleaned

    def _database_folder(self, database: str) -> Path:
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", database):
            raise ValueError("数据库名称不合法")
        folder = (self.database_root / database).resolve()
        root = self.database_root.resolve()
        if root not in folder.parents or not folder.is_dir():
            raise ValueError(f"本地CSV数据库不存在：{database}")
        return folder

    @staticmethod
    def clean_sql(text: str) -> str:
        cleaned = text.strip()
        cleaned = re.sub(r"^```(?:sql)?\s*|\s*```$", "", cleaned, flags=re.I)
        return cleaned.strip()

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, (date, datetime)):
            return value.isoformat()
        if isinstance(value, Decimal):
            return float(value)
        return value
