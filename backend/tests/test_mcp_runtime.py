import tempfile
import unittest
from pathlib import Path

from app.etl.pipeline import run_etl
from app.mcp_runtime import LocalMcpClient, create_local_mcp_server
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController, AccessScope


class McpRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_root = Path(self.temp_dir.name) / "databases"
        fixture = Path(__file__).resolve().parents[2] / "data" / "fixtures" / "jobs_seed.json"
        run_etl(fixture, None, database_root, Path(self.temp_dir.name) / "raw")
        server = create_local_mcp_server(DuckDbEngine(database_root))
        self.client = LocalMcpClient(server)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_tool_list_contains_only_jobs_market_database_tool(self) -> None:
        self.assertEqual(self.client.server.name, "offeragent-local-tools")
        self.assertEqual(self.client.server.title, "OfferAgent本地数据库工具服务")
        tools = self.client.list_tools()
        by_name = {tool["name"]: tool for tool in tools}

        self.assertEqual(
            {name for name in by_name if name.startswith("query_")},
            {"query_jobs_market"},
        )
        database_tool = by_name["query_jobs_market"]
        self.assertEqual(database_tool["input_schema"]["required"], ["sql"])
        self.assertIn("success", database_tool["output_schema"]["properties"])
        self.assertTrue(database_tool["annotations"]["readOnlyHint"])
        self.assertNotIn("current_datetime", by_name)
        self.assertNotIn("resolve_date_range", by_name)
        self.assertNotIn("build_bar_chart", by_name)

    def test_database_tool_executes_read_only_sql(self) -> None:
        result = self.client.call_tool(
            "query_jobs_market",
            {"sql": "SELECT city, COUNT(job_id) AS job_count FROM jobs GROUP BY city"},
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["database"], "jobs_market")
        self.assertEqual(result["row_count"], 5)

    def test_database_tool_is_hidden_for_user_without_database_access(self) -> None:
        scope = AccessController().resolve("unknown-user")
        client = LocalMcpClient(
            create_local_mcp_server(
                DuckDbEngine(Path(self.temp_dir.name) / "databases"),
                scope,
            )
        )

        self.assertEqual(client.list_tools(), [])
        with self.assertRaises(RuntimeError):
            client.call_tool("query_jobs_market", {"sql": "SELECT job_id FROM jobs"})

    def test_execution_rejects_select_star_and_write_sql(self) -> None:
        select_star = self.client.call_tool(
            "query_jobs_market", {"sql": "SELECT * FROM jobs"}
        )
        update = self.client.call_tool(
            "query_jobs_market", {"sql": "UPDATE jobs SET city = '上海'"}
        )

        self.assertFalse(select_star["success"])
        self.assertIn("SELECT *", select_star["error"])
        self.assertFalse(update["success"])
        self.assertIn("SELECT/WITH", update["error"])

    def test_execution_rechecks_table_permission_inside_cte(self) -> None:
        scope = AccessScope(
            user_id="restricted-test-user",
            roles=("test",),
            allowed_databases=frozenset({"jobs_market"}),
            allowed_tables=frozenset({"jobs_market.jobs"}),
        )
        client = LocalMcpClient(
            create_local_mcp_server(
                DuckDbEngine(Path(self.temp_dir.name) / "databases"),
                scope,
            )
        )

        result = client.call_tool(
            "query_jobs_market",
            {
                "sql": (
                    "WITH details AS (SELECT job_id FROM job_details) "
                    "SELECT job_id FROM details"
                )
            },
        )

        self.assertFalse(result["success"])
        self.assertIn("无权访问", result["error"])


if __name__ == "__main__":
    unittest.main()
