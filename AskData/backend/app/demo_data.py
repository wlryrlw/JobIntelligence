from __future__ import annotations

import csv
import random
from datetime import date, timedelta
from pathlib import Path

from .config import BASE_DIR


REGIONS = ["华东", "华南", "华北", "西南"]
CUSTOMER_LEVELS = ["战略客户", "重点客户", "普通客户"]
CATEGORIES = ["数据产品", "企业服务", "智能硬件", "安全服务"]


def seed_demo_data(seed: int = 20260815, database_dir: Path | None = None) -> dict[str, int]:
    """生成可复现的 CSV 示例数据。"""
    randomizer = random.Random(seed)
    folder = database_dir or BASE_DIR / "data" / "databases" / "askdata_mock"
    folder.mkdir(parents=True, exist_ok=True)
    customers = _customers(randomizer)
    products = _products()
    current_orders = _orders(
        randomizer,
        start_id=10_001,
        count=240,
        start_date=date(2026, 8, 1),
        day_span=31,
        customers=customers,
        products=products,
    )
    history_orders = _orders(
        randomizer,
        start_id=20_001,
        count=480,
        start_date=date(2026, 6, 1),
        day_span=61,
        customers=customers,
        products=products,
    )

    sales_targets = [
        (1, "2026-07", "华东", 1_900_000, "林晓"),
        (2, "2026-07", "华南", 1_550_000, "周岚"),
        (3, "2026-07", "华北", 1_350_000, "宋言"),
        (4, "2026-07", "西南", 1_100_000, "陈川"),
        (5, "2026-08", "华东", 2_200_000, "林晓"),
        (6, "2026-08", "华南", 1_800_000, "周岚"),
        (7, "2026-08", "华北", 1_600_000, "宋言"),
        (8, "2026-08", "西南", 1_300_000, "陈川"),
    ]

    _write_csv(folder / "customers.csv", ["customer_id", "customer_name", "customer_level", "region"], customers)
    _write_csv(folder / "products.csv", ["product_id", "product_name", "category"], products)
    order_columns = [
        "order_id", "region", "category", "order_amount", "paid_amount",
        "status", "order_date", "customer_id", "product_id",
    ]
    _write_csv(folder / "orders_current.csv", order_columns, current_orders)
    _write_csv(folder / "orders_history.csv", order_columns, history_orders)
    _write_csv(
        folder / "sales_targets.csv",
        ["target_id", "target_month", "region", "target_amount", "owner_name"],
        sales_targets,
    )
    return {
        "customers": len(customers),
        "products": len(products),
        "orders_current": len(current_orders),
        "orders_history": len(history_orders),
        "sales_targets": len(sales_targets),
    }


def _write_csv(path: Path, columns: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def _customers(randomizer: random.Random) -> list[tuple[int, str, str, str]]:
    prefixes = [
        "远景", "云帆", "星河", "海岳", "北辰", "南岭", "新程", "华耀", "澄明", "启元",
        "锐达", "安澜", "青禾", "鼎盛", "卓远", "同创", "恒信", "博源", "嘉禾", "光启",
        "飞鸿", "中科", "朗新", "合众", "德润", "天成", "金石", "智联", "融通", "万象",
    ]
    suffixes = ["科技", "数据", "制造", "零售", "网络", "集团"]
    output = []
    for index, prefix in enumerate(prefixes, 101):
        level = randomizer.choices(CUSTOMER_LEVELS, weights=[2, 4, 6], k=1)[0]
        region = REGIONS[(index - 101) % len(REGIONS)]
        output.append((index, f"{prefix}{randomizer.choice(suffixes)}", level, region))
    return output


def _products() -> list[tuple[int, str, str]]:
    names = [
        ("智能分析平台", "数据产品"),
        ("实时指标中心", "数据产品"),
        ("经营驾驶舱", "数据产品"),
        ("企业协同套件", "企业服务"),
        ("客户运营服务", "企业服务"),
        ("数据治理咨询", "企业服务"),
        ("边缘计算终端", "智能硬件"),
        ("智能采集网关", "智能硬件"),
        ("工业传感套件", "智能硬件"),
        ("零信任接入", "安全服务"),
        ("数据脱敏服务", "安全服务"),
        ("安全审计平台", "安全服务"),
    ]
    return [(index, name, category) for index, (name, category) in enumerate(names, 101)]


def _orders(
    randomizer: random.Random,
    *,
    start_id: int,
    count: int,
    start_date: date,
    day_span: int,
    customers: list[tuple[int, str, str, str]],
    products: list[tuple[int, str, str]],
) -> list[tuple]:
    output = []
    for offset in range(count):
        customer_id, _, customer_level, region = randomizer.choice(customers)
        product_id, _, category = randomizer.choice(products)
        level_multiplier = {"战略客户": 1.8, "重点客户": 1.25, "普通客户": 0.8}[customer_level]
        order_amount = randomizer.uniform(8_000, 120_000) * level_multiplier
        if offset and offset % 79 == 0:
            order_amount *= 3.5
        status = randomizer.choices(
            ["已支付", "已取消", "已退款"], weights=[84, 9, 7], k=1
        )[0]
        paid_amount = (
            order_amount * randomizer.uniform(0.86, 1.0) if status == "已支付" else 0
        )
        order_date = start_date + timedelta(days=randomizer.randrange(day_span))
        output.append(
            (
                start_id + offset,
                region,
                category,
                round(order_amount, 2),
                round(paid_amount, 2),
                status,
                order_date.isoformat(),
                customer_id,
                product_id,
            )
        )
    return output
