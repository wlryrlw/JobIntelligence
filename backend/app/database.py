from __future__ import annotations

"""静态 Schema 定义。

业务数据位于 ``data/databases/<database>/*.csv``；本模块仅定义表、字段和表间关系。
"""

import json
from pathlib import Path


def _field(
    name: str,
    label: str,
    field_type: str,
    description: str,
    aliases: list[str],
    role: str,
    aggregation: str = "none",
) -> dict:
    return {
        "name": name,
        "label": label,
        "type": field_type,
        "description": description,
        "aliases": aliases,
        "role": role,
        "aggregation": aggregation,
    }


ORDER_FIELDS = [
    _field("order_id", "订单编号", "整数", "订单的唯一编号，可用于订单计数", ["订单号", "订单数", "笔数"], "identifier", "count"),
    _field("region", "销售地区", "文本", "订单归属的销售大区", ["地区", "区域", "大区"], "dimension", "group"),
    _field("category", "产品类别", "文本", "订单中产品所属的业务品类", ["品类", "类别", "产品类型"], "dimension", "group"),
    _field("order_amount", "订单金额", "数值", "优惠和退款处理前的订单原始金额", ["应收金额", "原价金额"], "metric", "sum"),
    _field("paid_amount", "实付金额", "数值", "客户实际支付金额，可用于计算实际成交金额", ["销售额", "成交额", "收入", "实收"], "metric", "sum"),
    _field("status", "订单状态", "文本", "订单当前支付或退款状态", ["支付状态", "退款状态", "取消状态"], "filter", "group"),
    _field("order_date", "下单日期", "日期", "订单创建日期，格式为 YYYY-MM-DD", ["时间", "日期", "成交日期"], "time", "group"),
    _field("customer_id", "客户编号", "整数", "关联客户信息表的客户标识", ["客户ID", "企业编号"], "foreign_key"),
    _field("product_id", "产品编号", "整数", "关联产品信息表的产品标识", ["产品ID", "商品编号"], "foreign_key"),
]


SCHEMA = [
    {
        "id": "orders_current",
        "label": "当前订单明细",
        "database": "askdata_mock",
        "domain": "销售订单",
        "description": "2026年8月当前订单交易明细，适合本月、当前和近期销售查询",
        "business_terms": ["本月订单", "当前销售", "实时成交"],
        "primary_key": ["order_id"],
        "fields": ORDER_FIELDS,
    },
    {
        "id": "orders_history",
        "label": "历史订单明细",
        "database": "askdata_mock",
        "domain": "销售订单",
        "description": "2026年6月至7月历史归档订单，适合历史趋势和跨月对比",
        "business_terms": ["历史订单", "往期销售", "归档成交"],
        "primary_key": ["order_id"],
        "fields": ORDER_FIELDS,
    },
    {
        "id": "customers",
        "label": "客户信息",
        "database": "askdata_mock",
        "domain": "客户经营",
        "description": "客户名称、客户等级和所在地区，用于客户维度分析",
        "business_terms": ["客户画像", "客户分层", "企业客户"],
        "primary_key": ["customer_id"],
        "fields": [
            _field("customer_id", "客户编号", "整数", "客户唯一标识，可与订单关联", ["客户ID", "企业编号"], "identifier"),
            _field("customer_name", "客户名称", "文本", "企业客户的展示名称", ["客户", "企业名称", "公司名称"], "dimension", "group"),
            _field("customer_level", "客户等级", "文本", "战略、重点或普通客户分层", ["客户层级", "客户级别"], "dimension", "group"),
            _field("region", "客户地区", "文本", "客户注册或主要经营所在大区", ["客户区域", "所在地区"], "dimension", "group"),
        ],
    },
    {
        "id": "products",
        "label": "产品信息",
        "database": "askdata_mock",
        "domain": "产品经营",
        "description": "产品名称和产品类别，用于产品维度分析",
        "business_terms": ["产品目录", "商品信息", "产品线"],
        "primary_key": ["product_id"],
        "fields": [
            _field("product_id", "产品编号", "整数", "产品唯一标识，可与订单关联", ["产品ID", "商品编号"], "identifier"),
            _field("product_name", "产品名称", "文本", "具体产品或服务名称", ["产品", "商品", "服务名称"], "dimension", "group"),
            _field("category", "产品类别", "文本", "产品所属业务品类", ["产品线", "品类", "产品类型"], "dimension", "group"),
        ],
    },
    {
        "id": "sales_targets",
        "label": "地区销售目标",
        "database": "askdata_mock",
        "domain": "销售计划",
        "description": "按月份和地区制定的销售目标，用于目标完成率和实际销售对比",
        "business_terms": ["业绩目标", "销售预算", "目标完成率"],
        "primary_key": ["target_id"],
        "fields": [
            _field("target_id", "目标编号", "整数", "销售目标记录的唯一编号", ["计划编号"], "identifier"),
            _field("target_month", "目标月份", "日期", "销售目标对应月份，格式为 YYYY-MM", ["月份", "考核月份"], "time", "group"),
            _field("region", "销售地区", "文本", "目标所属销售大区", ["地区", "区域", "大区"], "dimension", "group"),
            _field("target_amount", "销售目标", "数值", "该地区当月计划完成的销售金额", ["目标额", "业绩目标", "销售预算"], "metric", "sum"),
            _field("owner_name", "区域负责人", "文本", "负责该地区销售目标的人员", ["负责人", "区域经理"], "dimension", "group"),
        ],
    },
]


RELATIONS = [
    {
        "left_table": "orders_current",
        "left_field": "customer_id",
        "right_table": "customers",
        "right_field": "customer_id",
        "description": "当前订单所属客户",
    },
    {
        "left_table": "orders_history",
        "left_field": "customer_id",
        "right_table": "customers",
        "right_field": "customer_id",
        "description": "历史订单所属客户",
    },
    {
        "left_table": "orders_current",
        "left_field": "product_id",
        "right_table": "products",
        "right_field": "product_id",
        "description": "当前订单对应产品",
    },
    {
        "left_table": "orders_history",
        "left_field": "product_id",
        "right_table": "products",
        "right_field": "product_id",
        "description": "历史订单对应产品",
    },
    {
        "left_table": "orders_current",
        "left_field": "region",
        "right_table": "sales_targets",
        "right_field": "region",
        "description": "当前销售与月度目标按地区进行业务关联，月份需额外对齐",
        "relation_type": "business",
    },
    {
        "left_table": "orders_history",
        "left_field": "region",
        "right_table": "sales_targets",
        "right_field": "region",
        "description": "历史销售与月度目标按地区进行业务关联，月份需额外对齐",
        "relation_type": "business",
    },
]


DATABASE_LABELS = {
    "askdata_mock": "演示数据源",
    "ecommerce_ops": "电商运营数据源",
    "jobs_market": "岗位市场数据源",
}


def physical_table_name(table: dict) -> str:
    return str(table.get("name") or table["id"])


def _load_generated_schema() -> None:
    """扫描生成的 Schema；岗位库位于项目 data，继承示例位于 backend/data。"""
    from .etl.schema import declared_schema, load_market_schema
    from .models import SchemaTable

    backend = Path(__file__).resolve().parents[1]
    roots = (backend / "data" / "databases", backend.parent / "data" / "databases")
    loaded: set[str] = set()
    for root in roots:
        for schema_path in sorted(root.glob("*/_schema.json")):
            database = schema_path.parent.name
            if database.startswith("."):
                continue
            if database in loaded:
                raise ValueError(f"数据库 Schema 重复：{database}")
            if database == "jobs_market":
                payload = load_market_schema(schema_path.parent).model_dump(mode="json")
            else:
                payload = json.loads(schema_path.read_text(encoding="utf-8"))
            tables = payload["tables"]
            for table in tables:
                SchemaTable.model_validate(table)
                if table.get("database") != database:
                    raise ValueError(f"Schema 数据库与目录不一致：{schema_path}")
            SCHEMA.extend(tables)
            RELATIONS.extend(payload.get("relations", []))
            loaded.add(database)
    if "jobs_market" not in loaded:
        # 没有本地数据时仍提供确定的契约；真正查询会报数据库不存在。
        payload = declared_schema().model_dump(mode="json")
        SCHEMA.extend(payload["tables"])
        RELATIONS.extend(payload["relations"])


_load_generated_schema()
