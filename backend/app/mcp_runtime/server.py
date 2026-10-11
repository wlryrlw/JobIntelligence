from __future__ import annotations

from mcp.server import MCPServer
from mcp.types import ToolAnnotations

from ..database import DEFAULT_DATABASE, SCHEMA, physical_table_name
from ..querying.duckdb_engine import DuckDbEngine
from ..security import AccessController, AccessScope
from .tools import build_database_query_tool


READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


def create_local_mcp_server(
    engine: DuckDbEngine | None = None,
    access_scope: AccessScope | None = None,
) -> MCPServer:
    """创建与FastAPI运行在同一进程中的MCP服务。"""
    database_engine = engine or DuckDbEngine()
    scope = access_scope or AccessController().resolve(None)
    server = MCPServer(
        name="offeragent-local-tools",
        title="OfferAgent本地数据库工具服务",
        description="提供按用户权限隔离的本地数据库只读查询工具。",
        instructions="调用数据库工具前先根据Schema图生成一条只读DuckDB SQL。",
    )

    databases = sorted({str(table.get("database") or DEFAULT_DATABASE) for table in SCHEMA})
    for database in databases:
        if not scope.allows_database(database):
            continue
        tables = [
            physical_table_name(table)
            for table in SCHEMA
            if table.get("database") == database
            and scope.allows_table(database, table["id"])
        ]
        handler = build_database_query_tool(database, database_engine, scope)
        server.tool(
            name=f"query_{database}",
            title=f"查询数据库 {database}",
            description=(
                f"在数据库{database}中执行一条只读DuckDB SQL。"
                f"可用表：{', '.join(tables)}。每次调用最多返回200行。"
            ),
            annotations=READ_ONLY,
        )(handler)
    return server
