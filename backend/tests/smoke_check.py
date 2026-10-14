"""手动运行：检查 JobIntel 当前岗位数据契约和 MCP 数据库边界。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database import SCHEMA
from app.mcp_runtime import LocalMcpClient, create_local_mcp_server
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController


if __name__ == "__main__":
    client = LocalMcpClient(
        create_local_mcp_server(DuckDbEngine(), AccessController().resolve("local_user"))
    )
    names = [tool["name"] for tool in client.list_tools()]
    print(
        {
            "database_count": len({table["database"] for table in SCHEMA}),
            "mcp_tools": names,
        }
    )
