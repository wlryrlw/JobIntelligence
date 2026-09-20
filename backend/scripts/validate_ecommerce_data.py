from __future__ import annotations

import json
from pathlib import Path

import duckdb


DATABASE_DIR = Path(__file__).resolve().parents[1] / "data" / "databases" / "ecommerce_ops"


def validate(database_dir: Path = DATABASE_DIR) -> dict[str, int]:
    csv_files = sorted(database_dir.glob("*.csv"))
    if len(csv_files) != 50:
        raise ValueError(f"预期50张表，实际发现{len(csv_files)}张")

    connection = duckdb.connect(":memory:")
    try:
        for path in csv_files:
            table = path.stem.replace('"', '""')
            source = path.as_posix().replace("'", "''")
            connection.execute(
                f'CREATE VIEW "{table}" AS SELECT * FROM read_csv_auto(\'{source}\', header=true)'
            )

        checks = {
            "order_items_without_order": """
                SELECT COUNT(*) FROM order_items i
                LEFT JOIN orders o ON i.order_id = o.order_id
                WHERE o.order_id IS NULL
            """,
            "orders_without_user": """
                SELECT COUNT(*) FROM orders o
                LEFT JOIN users u ON o.user_id = u.user_id
                WHERE u.user_id IS NULL
            """,
            "orders_without_store": """
                SELECT COUNT(*) FROM orders o
                LEFT JOIN stores s ON o.store_id = s.store_id
                WHERE s.store_id IS NULL
            """,
            "skus_without_product": """
                SELECT COUNT(*) FROM product_skus s
                LEFT JOIN products p ON s.product_id = p.product_id
                WHERE p.product_id IS NULL
            """,
            "products_without_dimension": """
                SELECT COUNT(*) FROM products p
                LEFT JOIN stores s ON p.store_id = s.store_id
                LEFT JOIN brands b ON p.brand_id = b.brand_id
                LEFT JOIN product_categories c ON p.category_id = c.category_id
                LEFT JOIN suppliers sp ON p.supplier_id = sp.supplier_id
                WHERE s.store_id IS NULL OR b.brand_id IS NULL
                   OR c.category_id IS NULL OR sp.supplier_id IS NULL
            """,
            "shipments_without_order": """
                SELECT COUNT(*) FROM shipments s
                LEFT JOIN orders o ON s.order_id = o.order_id
                WHERE o.order_id IS NULL
            """,
            "refunds_without_order": """
                SELECT COUNT(*) FROM refunds r
                LEFT JOIN orders o ON r.order_id = o.order_id
                WHERE o.order_id IS NULL
            """,
            "reviews_without_order_item": """
                SELECT COUNT(*) FROM product_reviews r
                LEFT JOIN order_items i ON r.order_item_id = i.order_item_id
                WHERE i.order_item_id IS NULL
            """,
            "invalid_order_amount": """
                SELECT COUNT(*) FROM orders
                WHERE ABS(payable_amount - (item_amount - discount_amount + shipping_fee)) > 0.02
                   OR paid_amount < 0
            """,
        }
        violations = {
            name: connection.execute(sql).fetchone()[0]
            for name, sql in checks.items()
        }
        failed = {name: count for name, count in violations.items() if count}
        if failed:
            raise ValueError(f"数据完整性校验失败：{failed}")

        manifest = json.loads((database_dir / "_database_manifest.json").read_text(encoding="utf-8"))
        actual_rows = sum(
            connection.execute(f'SELECT COUNT(*) FROM "{path.stem}"').fetchone()[0]
            for path in csv_files
        )
        if actual_rows != manifest["total_rows"]:
            raise ValueError(
                f"清单记录{manifest['total_rows']}行，实际读取{actual_rows}行"
            )
        return {"tables": len(csv_files), "rows": actual_rows, "checks": len(checks)}
    finally:
        connection.close()


if __name__ == "__main__":
    print(json.dumps(validate(), ensure_ascii=False))
