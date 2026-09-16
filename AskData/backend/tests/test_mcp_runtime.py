import tempfile
import unittest
from pathlib import Path

from app.demo_data import seed_demo_data
from app.mcp_runtime import LocalMcpClient, create_local_mcp_server
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController, AccessScope


class McpRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        database_root = Path(self.temp_dir.name) / "databases"
        seed_demo_data(database_dir=database_root / "askdata_mock")
        server = create_local_mcp_server(DuckDbEngine(database_root))
        self.client = LocalMcpClient(server)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_tool_list_contains_schema_and_one_tool_per_database(self) -> None:
        tools = self.client.list_tools()
        by_name = {tool["name"]: tool for tool in tools}
        databases = set(AccessController().resolve(None).allowed_databases)

        self.assertEqual(
            {name for name in by_name if name.startswith("query_")},
            {f"query_{database}" for database in databases},
        )
        database_tool = by_name["query_askdata_mock"]
        self.assertEqual(database_tool["input_schema"]["required"], ["sql"])
        self.assertIn("success", database_tool["output_schema"]["properties"])
        self.assertTrue(database_tool["annotations"]["readOnlyHint"])

    def test_database_tool_executes_read_only_sql(self) -> None:
        result = self.client.call_tool(
            "query_askdata_mock",
            {
                "sql": (
                    "SELECT region, COUNT(*) AS order_count "
                    "FROM orders_current GROUP BY region"
                )
            },
        )

        self.assertTrue(result["success"])
        self.assertEqual(result["database"], "askdata_mock")
        self.assertEqual(result["row_count"], 4)

    def test_time_tool_returns_explicit_date_range(self) -> None:
        result = self.client.call_tool(
            "resolve_date_range",
            {"expression": "本月", "timezone_name": "Asia/Shanghai"},
        )

        self.assertRegex(result["start_date"], r"^\d{4}-\d{2}-01$")
        self.assertRegex(result["end_date"], r"^\d{4}-\d{2}-\d{2}$")
        self.assertEqual(result["timezone"], "Asia/Shanghai")

    def test_presentation_tool_returns_frontend_spec_without_data(self) -> None:
        result = self.client.call_tool(
            "build_bar_chart",
            {
                "title": "各地区销售额",
                "source_task_id": "task-1",
                "category_field": "销售地区",
                "value_field": "销售额",
                "max_items": 8,
            },
        )

        self.assertEqual(result["type"], "bar")
        self.assertEqual(result["source_task_id"], "task-1")
        self.assertNotIn("rows", result)

    def test_database_tool_is_hidden_for_user_without_database_access(self) -> None:
        scope = AccessController().resolve("unknown-user")
        client = LocalMcpClient(
            create_local_mcp_server(
                DuckDbEngine(Path(self.temp_dir.name) / "databases"),
                scope,
            )
        )

        names = {tool["name"] for tool in client.list_tools()}

        self.assertNotIn("query_askdata_mock", names)
        with self.assertRaises(RuntimeError):
            client.call_tool("query_askdata_mock", {"sql": "SELECT order_id FROM orders_current"})

    def test_execution_rejects_select_star_and_write_sql(self) -> None:
        select_star = self.client.call_tool(
            "query_askdata_mock",
            {"sql": "SELECT * FROM orders_current"},
        )
        update = self.client.call_tool(
            "query_askdata_mock",
            {"sql": "UPDATE orders_current SET region = '华东'"},
        )
        multiple = self.client.call_tool(
            "query_askdata_mock",
            {
                "sql": (
                    "UPDATE orders_current SET region = '华东'; "
                    "SELECT order_id FROM orders_current"
                )
            },
        )

        self.assertFalse(select_star["success"])
        self.assertIn("SELECT *", select_star["error"])
        self.assertFalse(update["success"])
        self.assertIn("SELECT/WITH", update["error"])
        self.assertFalse(multiple["success"])
        self.assertIn("一条SQL", multiple["error"])

    def test_execution_rechecks_table_permission_inside_cte(self) -> None:
        scope = AccessScope(
            user_id="restricted-test-user",
            roles=("test",),
            allowed_databases=frozenset({"askdata_mock"}),
            allowed_tables=frozenset({
                "orders_current", "customers", "products", "sales_targets"
            }),
        )
        client = LocalMcpClient(
            create_local_mcp_server(
                DuckDbEngine(Path(self.temp_dir.name) / "databases"),
                scope,
            )
        )

        result = client.call_tool(
            "query_askdata_mock",
            {
                "sql": (
                    "WITH history AS (SELECT order_id FROM orders_history) "
                    "SELECT order_id FROM history"
                )
            },
        )

        self.assertFalse(result["success"])
        self.assertIn("无权访问", result["error"])


if __name__ == "__main__":
    unittest.main()
