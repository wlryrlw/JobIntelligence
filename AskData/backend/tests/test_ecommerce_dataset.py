from __future__ import annotations

import unittest

from app.database import SCHEMA
from app.querying.duckdb_engine import DuckDbEngine
from app.retrieval.graph import SchemaGraphBuilder
from app.security import AccessController


class EcommerceDatasetTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.context = DuckDbEngine().connect("ecommerce_ops")
        cls.connection = cls.context.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.context.__exit__(None, None, None)

    def test_all_tables_are_loaded(self) -> None:
        count = self.connection.execute(
            """
            SELECT COUNT(*)
            FROM information_schema.tables
            WHERE table_schema = 'main'
            """
        ).fetchone()[0]
        self.assertEqual(count, 50)

    def test_three_layer_schema_is_loaded(self) -> None:
        tables = [table for table in SCHEMA if table.get("database") == "ecommerce_ops"]
        fields = [field for table in tables for field in table["fields"]]

        self.assertEqual(len(tables), 50)
        self.assertEqual(len(fields), 392)
        self.assertTrue(all(table["id"].startswith("ecommerce_ops.") for table in tables))
        self.assertTrue(all(table.get("name") for table in tables))
        self.assertTrue(all(field.get("data_profile") for field in fields))
        self.assertTrue(all(field.get("index_content", {}).get("keyword_text") for field in fields))
        self.assertTrue(all(field.get("index_content", {}).get("vector_text") for field in fields))
        self.assertTrue(all(field.get("index_content", {}).get("rerank_text") for field in fields))

    def test_schema_graph_uses_physical_sql_names(self) -> None:
        hits = [
            {
                "doc_id": "ecommerce_ops.orders.paid_amount",
                "database_id": "ecommerce_ops",
                "table_id": "ecommerce_ops.orders",
                "field_name": "paid_amount",
                "field_label": "实付金额",
                "field_type": "数值",
                "field_description": "订单实付金额",
                "field_role": "metric",
                "score": 0.95,
            },
            {
                "doc_id": "ecommerce_ops.product_categories.category_name",
                "database_id": "ecommerce_ops",
                "table_id": "ecommerce_ops.product_categories",
                "field_name": "category_name",
                "field_label": "类目名称",
                "field_type": "文本",
                "field_description": "商品类目名称",
                "field_role": "dimension",
                "score": 0.94,
            },
        ]
        graph = SchemaGraphBuilder().build(
            hits,
            AccessController().resolve("demo_current_sales"),
        )
        table_names = {table["name"] for table in graph["tables"]}

        self.assertTrue({"orders", "order_items", "products", "product_categories"}.issubset(table_names))
        self.assertIn("orders.paid_amount", SchemaGraphBuilder.context_text(graph))
        self.assertTrue(all(join.get("left_table_name") and join.get("right_table_name") for join in graph["joins"]))

    def test_ecommerce_database_has_database_level_access(self) -> None:
        scope = AccessController().resolve("demo_current_sales")
        ecommerce_tables = {
            table["id"] for table in SCHEMA if table.get("database") == "ecommerce_ops"
        }

        self.assertTrue(scope.allows_database("ecommerce_ops"))
        self.assertEqual(ecommerce_tables, ecommerce_tables & scope.allowed_tables)

        result = DuckDbEngine().execute(
            "ecommerce_ops",
            "SELECT order_status, COUNT(order_id) AS order_count FROM orders GROUP BY order_status",
            scope,
        )
        self.assertTrue(result.success, result.error)

    def test_core_fact_table_scale_and_date_range(self) -> None:
        result = self.connection.execute(
            """
            SELECT
                COUNT(*) AS order_count,
                COUNT(DISTINCT user_id) AS buyer_count,
                MIN(ordered_at) AS first_order_at,
                MAX(ordered_at) AS last_order_at,
                ROUND(SUM(paid_amount), 2) AS paid_amount
            FROM orders
            """
        ).fetchone()
        self.assertEqual(result[0], 12000)
        self.assertGreater(result[1], 4000)
        self.assertEqual(str(result[2].date()), "2025-01-01")
        self.assertEqual(str(result[3].date()), "2026-08-22")
        self.assertGreater(result[4], 0)

    def test_product_sales_multi_table_join(self) -> None:
        rows = self.connection.execute(
            """
            SELECT
                c.category_name,
                COUNT(DISTINCT o.order_id) AS order_count,
                ROUND(SUM(oi.paid_amount), 2) AS sales_amount
            FROM orders o
            JOIN order_items oi ON o.order_id = oi.order_id
            JOIN products p ON oi.product_id = p.product_id
            JOIN product_categories c ON p.category_id = c.category_id
            JOIN stores s ON o.store_id = s.store_id
            WHERE o.paid_amount > 0
            GROUP BY c.category_name
            ORDER BY sales_amount DESC
            """
        ).fetchall()
        self.assertEqual(len(rows), 32)
        self.assertTrue(all(row[1] > 0 and row[2] > 0 for row in rows))

    def test_advertising_roi(self) -> None:
        result = self.connection.execute(
            """
            SELECT
                SUM(impressions) AS impressions,
                SUM(clicks) AS clicks,
                SUM(conversions) AS conversions,
                ROUND(SUM(attributed_revenue) / SUM(ad_cost), 4) AS roi
            FROM ad_daily_stats
            """
        ).fetchone()
        self.assertGreater(result[0], result[1])
        self.assertGreater(result[1], result[2])
        self.assertGreater(result[3], 0)

    def test_refund_rate_by_store(self) -> None:
        rows = self.connection.execute(
            """
            WITH store_orders AS (
                SELECT store_id, COUNT(*) AS order_count
                FROM orders
                WHERE paid_amount > 0
                GROUP BY store_id
            ), store_refunds AS (
                SELECT o.store_id, COUNT(*) AS refund_count
                FROM refunds r
                JOIN orders o ON r.order_id = o.order_id
                GROUP BY o.store_id
            )
            SELECT
                s.store_id,
                so.order_count,
                COALESCE(sr.refund_count, 0) AS refund_count,
                ROUND(COALESCE(sr.refund_count, 0) * 1.0 / so.order_count, 4) AS refund_rate
            FROM stores s
            JOIN store_orders so ON s.store_id = so.store_id
            LEFT JOIN store_refunds sr ON s.store_id = sr.store_id
            """
        ).fetchall()
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(0 <= row[3] <= 1 for row in rows))

    def test_store_target_attainment(self) -> None:
        rows = self.connection.execute(
            """
            WITH actual AS (
                SELECT store_id, SUM(gmv) AS actual_gmv
                FROM daily_store_metrics
                WHERE stat_date BETWEEN DATE '2026-08-01' AND DATE '2026-08-22'
                GROUP BY store_id
            )
            SELECT
                t.store_id,
                t.gmv_target,
                ROUND(a.actual_gmv, 2) AS actual_gmv,
                ROUND(a.actual_gmv / t.gmv_target, 4) AS attainment_rate
            FROM sales_targets t
            JOIN actual a ON t.store_id = a.store_id
            WHERE t.target_month = '2026-08'
            """
        ).fetchall()
        self.assertEqual(len(rows), 80)
        self.assertTrue(all(row[1] > 0 and row[2] > 0 for row in rows))


if __name__ == "__main__":
    unittest.main()
