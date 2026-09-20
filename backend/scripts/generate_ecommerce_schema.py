from __future__ import annotations

import json
import re
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb


DATABASE = "ecommerce_ops"
DATABASE_DIR = Path(__file__).resolve().parents[1] / "data" / "databases" / DATABASE
MANIFEST_PATH = DATABASE_DIR / "_database_manifest.json"
SCHEMA_PATH = DATABASE_DIR / "_schema.json"

TABLE_PRIMARY_KEYS = {
    "users": ["user_id"], "user_profiles": ["user_id"], "user_addresses": ["address_id"],
    "user_memberships": ["membership_id"], "member_point_transactions": ["point_transaction_id"],
    "user_tags": ["tag_id"], "user_tag_relations": ["relation_id"],
    "acquisition_channels": ["channel_id"], "user_devices": ["device_id"],
    "user_sessions": ["session_id"], "page_views": ["view_id"], "search_events": ["search_id"],
    "stores": ["store_id"], "store_staff": ["staff_id"], "brands": ["brand_id"],
    "product_categories": ["category_id"], "products": ["product_id"],
    "product_skus": ["sku_id"], "product_prices": ["price_id"],
    "warehouses": ["warehouse_id"], "sku_inventory": ["sku_id", "warehouse_id"],
    "inventory_movements": ["movement_id"], "suppliers": ["supplier_id"],
    "purchase_orders": ["purchase_order_id"], "purchase_order_items": ["purchase_item_id"],
    "marketing_campaigns": ["campaign_id"], "campaign_products": ["campaign_product_id"],
    "coupons": ["coupon_id"], "coupon_receipts": ["receipt_id"], "coupon_usages": ["usage_id"],
    "ad_campaigns": ["ad_campaign_id"], "ad_daily_stats": ["ad_stat_id"],
    "recommendation_exposures": ["exposure_id"], "shopping_carts": ["cart_id"],
    "cart_items": ["cart_item_id"], "favorite_products": ["favorite_id"],
    "orders": ["order_id"], "order_items": ["order_item_id"], "payments": ["payment_id"],
    "shipments": ["shipment_id"], "shipment_tracks": ["track_id"],
    "refunds": ["refund_id"], "refund_items": ["refund_item_id"],
    "product_reviews": ["review_id"], "review_replies": ["reply_id"],
    "service_tickets": ["ticket_id"], "ticket_messages": ["message_id"],
    "daily_product_metrics": ["metric_id"], "daily_store_metrics": ["metric_id"],
    "sales_targets": ["target_id"],
}

TABLE_DOMAINS = {
    "用户运营": {
        "users", "user_profiles", "user_addresses", "user_memberships",
        "member_point_transactions", "user_tags", "user_tag_relations", "user_devices",
    },
    "流量与行为": {"acquisition_channels", "user_sessions", "page_views", "search_events", "recommendation_exposures"},
    "店铺与商品": {"stores", "store_staff", "brands", "product_categories", "products", "product_skus", "product_prices"},
    "供应链与库存": {"warehouses", "sku_inventory", "inventory_movements", "suppliers", "purchase_orders", "purchase_order_items"},
    "营销与广告": {"marketing_campaigns", "campaign_products", "coupons", "coupon_receipts", "coupon_usages", "ad_campaigns", "ad_daily_stats"},
    "交易与履约": {"shopping_carts", "cart_items", "favorite_products", "orders", "order_items", "payments", "shipments", "shipment_tracks"},
    "售后与服务": {"refunds", "refund_items", "product_reviews", "review_replies", "service_tickets", "ticket_messages"},
    "经营分析": {"daily_product_metrics", "daily_store_metrics", "sales_targets"},
}

FIELD_LABELS = {
    "user_id": "用户编号", "nickname": "用户昵称", "email": "电子邮箱", "masked_mobile": "脱敏手机号",
    "register_channel_id": "注册渠道编号", "registered_at": "注册时间", "account_status": "账户状态",
    "register_platform": "注册平台", "mock_name": "模拟姓名", "gender": "性别", "birthday": "出生日期",
    "region": "大区", "province": "省份", "city": "城市", "district": "区县", "occupation": "职业",
    "income_band": "收入区间", "updated_at": "更新时间", "address_id": "地址编号",
    "address_detail": "详细地址", "receiver_name": "收货人", "is_default": "是否默认",
    "membership_id": "会员记录编号", "member_level": "会员等级", "joined_date": "入会日期",
    "expire_date": "到期日期", "available_points": "可用积分", "growth_level": "成长等级",
    "status": "状态", "point_transaction_id": "积分流水编号", "change_type": "变动类型",
    "point_delta": "积分变动值", "occurred_at": "发生时间", "reference_no": "关联业务编号",
    "tag_id": "标签编号", "tag_name": "标签名称", "tag_type": "标签类型", "enabled": "是否启用",
    "relation_id": "关联记录编号", "score": "评分", "assigned_at": "标签分配时间", "source": "来源",
    "channel_id": "渠道编号", "channel_name": "渠道名称", "channel_type": "渠道类型",
    "channel_group": "渠道分组", "device_id": "设备编号", "device_type": "设备类型", "platform": "平台",
    "app_version": "应用版本", "last_active_at": "最后活跃时间", "trusted": "是否可信设备",
    "session_id": "会话编号", "started_at": "开始时间", "ended_at": "结束时间",
    "duration_seconds": "持续秒数", "landing_page": "落地页", "converted": "是否转化",
    "view_id": "浏览记录编号", "page_type": "页面类型", "source_position": "来源位置",
    "viewed_at": "浏览时间", "stay_seconds": "停留秒数", "scroll_depth": "页面浏览深度",
    "search_id": "搜索记录编号", "keyword": "搜索关键词", "result_count": "搜索结果数",
    "clicked": "是否点击", "searched_at": "搜索时间", "store_id": "店铺编号", "store_name": "店铺名称",
    "store_type": "店铺类型", "opened_date": "开店日期", "commission_rate": "平台佣金率",
    "staff_id": "员工编号", "staff_name": "员工姓名", "role_name": "岗位名称", "joined_date": "加入日期",
    "employment_status": "在职状态", "brand_id": "品牌编号", "brand_name": "品牌名称",
    "origin_type": "品牌来源", "category_id": "类目编号", "category_name": "类目名称",
    "parent_category_id": "父级类目编号", "category_level": "类目层级", "sort_order": "排序值",
    "product_id": "商品编号", "product_name": "商品名称", "supplier_id": "供应商编号",
    "product_status": "商品状态", "created_at": "创建时间", "weight_kg": "商品重量",
    "warranty_months": "质保月数", "sku_id": "SKU编号", "sku_name": "SKU名称", "color": "颜色",
    "specification": "规格", "market_price": "市场价", "sale_price": "销售价", "cost_price": "成本价",
    "barcode": "商品条码", "sku_status": "SKU状态", "price_id": "价格记录编号", "price": "价格",
    "effective_from": "生效日期", "effective_to": "失效日期", "price_type": "价格类型",
    "warehouse_id": "仓库编号", "warehouse_name": "仓库名称", "capacity_units": "仓储容量",
    "available_qty": "可用库存量", "reserved_qty": "预占库存量", "safety_stock": "安全库存量",
    "max_stock": "最大库存量", "movement_id": "库存流水编号", "movement_type": "库存变动类型",
    "quantity_delta": "库存变动数量", "source_system": "来源系统", "supplier_name": "供应商名称",
    "supplier_grade": "供应商等级", "lead_time_days": "供货周期天数", "on_time_rate": "准时交付率",
    "purchase_order_id": "采购单编号", "expected_date": "预计到货日期", "purchase_amount": "采购金额",
    "currency": "币种", "purchase_item_id": "采购明细编号", "ordered_qty": "采购数量",
    "received_qty": "到货数量", "unit_cost": "采购单价", "line_amount": "明细金额",
    "campaign_id": "营销活动编号", "campaign_name": "营销活动名称", "campaign_type": "营销活动类型",
    "starts_at": "开始时间", "ends_at": "结束时间", "budget": "预算金额", "sponsor_type": "活动发起方",
    "campaign_product_id": "活动商品编号", "campaign_price": "活动价格", "stock_limit": "活动库存上限",
    "per_user_limit": "单用户限购数", "coupon_id": "优惠券编号", "coupon_name": "优惠券名称",
    "coupon_type": "优惠券类型", "threshold_amount": "使用门槛金额", "discount_amount": "优惠金额",
    "valid_from": "有效开始日期", "valid_to": "有效结束日期", "issue_limit": "发券上限",
    "scope_type": "适用范围类型", "receipt_id": "领券记录编号", "received_at": "领券时间",
    "receipt_status": "领券状态", "usage_id": "核销记录编号", "order_id": "订单编号",
    "used_at": "核销时间", "ad_campaign_id": "广告计划编号", "ad_campaign_name": "广告计划名称",
    "billing_mode": "计费模式", "start_date": "开始日期", "end_date": "结束日期",
    "ad_stat_id": "广告统计编号", "stat_date": "统计日期", "impressions": "曝光次数", "clicks": "点击次数",
    "conversions": "转化次数", "ad_cost": "广告消耗", "attributed_revenue": "广告归因收入", "roi": "投入产出比",
    "exposure_id": "推荐曝光编号", "position_name": "推荐位置", "strategy_name": "推荐策略",
    "exposed_at": "曝光时间", "cart_id": "购物车编号", "cart_status": "购物车状态",
    "cart_item_id": "购物车明细编号", "quantity": "商品数量", "unit_price": "商品单价",
    "added_at": "加入时间", "selected": "是否选中", "favorite_id": "收藏记录编号",
    "favorited_at": "收藏时间", "source_page": "来源页面", "ordered_at": "下单时间",
    "order_status": "订单状态", "item_amount": "商品原始金额", "shipping_fee": "运费",
    "payable_amount": "应付金额", "paid_amount": "实付金额", "order_item_id": "订单明细编号",
    "cost_amount": "成本金额", "payment_id": "支付流水编号", "payment_method": "支付方式",
    "payment_amount": "支付金额", "payment_status": "支付状态", "paid_at": "支付时间",
    "transaction_no": "支付交易号", "shipment_id": "发货单编号", "carrier_name": "承运商名称",
    "tracking_no": "物流单号", "shipped_at": "发货时间", "delivered_at": "签收时间",
    "shipment_status": "物流状态", "package_count": "包裹数量", "track_id": "物流轨迹编号",
    "track_status": "轨迹状态", "location": "物流位置", "refund_id": "退款编号",
    "refund_reason": "退款原因", "refund_amount": "退款金额", "refund_status": "退款状态",
    "applied_at": "申请时间", "completed_at": "完成时间", "refund_item_id": "退款明细编号",
    "refund_quantity": "退款数量", "refund_type": "退款类型", "review_id": "评价编号",
    "rating": "评价星级", "review_content": "评价内容", "has_image": "是否含图片",
    "review_status": "评价状态", "reply_id": "回复编号", "reply_content": "回复内容",
    "replied_at": "回复时间", "reply_type": "回复类型", "ticket_id": "客服工单编号",
    "issue_type": "问题类型", "priority": "优先级", "ticket_status": "工单状态",
    "resolved_at": "解决时间", "source_channel": "来源渠道", "message_id": "消息编号",
    "sender_type": "发送方类型", "message_content": "消息内容", "sent_at": "发送时间",
    "message_type": "消息类型", "metric_id": "指标记录编号", "page_views": "页面浏览次数",
    "unique_visitors": "独立访客数", "buyers": "购买用户数", "paid_orders": "支付订单数",
    "gmv": "商品交易总额", "refund_orders": "退款订单数", "favorite_additions": "新增收藏数",
    "visitors": "访客数", "refund_amount": "退款金额", "net_revenue": "净收入",
    "conversion_rate": "转化率", "service_score": "服务评分", "fulfillment_rate": "履约率",
    "target_id": "目标记录编号", "target_month": "目标月份", "gmv_target": "GMV目标",
    "net_revenue_target": "净收入目标", "order_target": "订单目标", "conversion_rate_target": "转化率目标",
    "owner_name": "目标负责人",
}

FIELD_ALIASES = {
    "user_id": ["用户ID", "会员编号", "客户编号"],
    "order_id": ["订单号", "订单ID", "订单数", "订单量"],
    "paid_amount": ["实付金额", "成交金额", "销售额", "实收金额"],
    "gmv": ["GMV", "成交总额", "商品交易总额", "销售额"],
    "net_revenue": ["净收入", "净销售额"],
    "buyers": ["买家数", "购买人数", "成交用户数"],
    "paid_orders": ["支付订单数", "成交订单数"],
    "conversion_rate": ["转化率", "成交转化率"],
    "refund_amount": ["退款金额", "退货金额"],
    "refund_orders": ["退款订单数", "退货订单数"],
    "impressions": ["曝光量", "展示次数"],
    "clicks": ["点击量", "点击次数"],
    "roi": ["投入产出比", "广告ROI", "投产比"],
    "available_qty": ["可售库存", "现货库存", "库存余量"],
    "category_name": ["类目", "品类", "商品类别"],
    "store_name": ["店铺", "商家", "门店名称"],
    "region": ["地区", "区域", "大区"],
    "stat_date": ["统计日期", "数据日期"],
    "target_month": ["目标月份", "考核月份"],
}

TOKEN_LABELS = {
    "id": "编号", "name": "名称", "type": "类型", "date": "日期", "time": "时间",
    "at": "时间", "amount": "金额", "rate": "率", "count": "数量", "status": "状态",
    "user": "用户", "order": "订单", "product": "商品", "store": "店铺", "sku": "SKU",
    "campaign": "活动", "coupon": "优惠券", "payment": "支付", "refund": "退款",
    "shipment": "物流", "supplier": "供应商", "warehouse": "仓库", "target": "目标",
    "daily": "每日", "available": "可用", "reserved": "预占", "source": "来源",
}

RELATION_SPECS = [
    ("users", "user_id", "user_profiles", "user_id", "用户与画像一一对应"),
    ("users", "user_id", "user_addresses", "user_id", "用户拥有收货地址"),
    ("users", "user_id", "user_memberships", "user_id", "用户对应会员记录"),
    ("users", "user_id", "member_point_transactions", "user_id", "用户产生积分流水"),
    ("users", "user_id", "user_tag_relations", "user_id", "用户关联标签"),
    ("user_tags", "tag_id", "user_tag_relations", "tag_id", "标签分配给用户"),
    ("acquisition_channels", "channel_id", "users", "register_channel_id", "用户通过渠道注册"),
    ("users", "user_id", "user_devices", "user_id", "用户绑定设备"),
    ("users", "user_id", "user_sessions", "user_id", "用户产生访问会话"),
    ("user_devices", "device_id", "user_sessions", "device_id", "会话使用设备"),
    ("acquisition_channels", "channel_id", "user_sessions", "channel_id", "会话来源渠道"),
    ("user_sessions", "session_id", "page_views", "session_id", "会话包含页面浏览"),
    ("users", "user_id", "page_views", "user_id", "用户产生页面浏览"),
    ("products", "product_id", "page_views", "product_id", "商品详情页被浏览"),
    ("user_sessions", "session_id", "search_events", "session_id", "会话包含站内搜索"),
    ("users", "user_id", "search_events", "user_id", "用户产生搜索行为"),
    ("product_categories", "category_id", "search_events", "category_id", "搜索限定商品类目"),
    ("stores", "store_id", "store_staff", "store_id", "员工归属店铺"),
    ("stores", "store_id", "products", "store_id", "商品归属店铺"),
    ("brands", "brand_id", "products", "brand_id", "商品归属品牌"),
    ("product_categories", "category_id", "products", "category_id", "商品归属类目"),
    ("suppliers", "supplier_id", "products", "supplier_id", "商品由供应商供货"),
    ("products", "product_id", "product_skus", "product_id", "商品包含SKU"),
    ("product_skus", "sku_id", "product_prices", "sku_id", "SKU对应价格历史"),
    ("product_skus", "sku_id", "sku_inventory", "sku_id", "SKU对应库存"),
    ("warehouses", "warehouse_id", "sku_inventory", "warehouse_id", "库存位于仓库"),
    ("product_skus", "sku_id", "inventory_movements", "sku_id", "SKU产生库存流水"),
    ("warehouses", "warehouse_id", "inventory_movements", "warehouse_id", "库存流水发生在仓库"),
    ("suppliers", "supplier_id", "purchase_orders", "supplier_id", "采购单对应供应商"),
    ("warehouses", "warehouse_id", "purchase_orders", "warehouse_id", "采购单送达仓库"),
    ("purchase_orders", "purchase_order_id", "purchase_order_items", "purchase_order_id", "采购单包含采购明细"),
    ("product_skus", "sku_id", "purchase_order_items", "sku_id", "采购明细对应SKU"),
    ("marketing_campaigns", "campaign_id", "campaign_products", "campaign_id", "活动包含商品"),
    ("products", "product_id", "campaign_products", "product_id", "商品参与营销活动"),
    ("coupons", "coupon_id", "coupon_receipts", "coupon_id", "用户领取优惠券"),
    ("users", "user_id", "coupon_receipts", "user_id", "领券记录归属用户"),
    ("coupon_receipts", "receipt_id", "coupon_usages", "receipt_id", "领券记录产生核销"),
    ("coupons", "coupon_id", "coupon_usages", "coupon_id", "核销记录对应优惠券"),
    ("orders", "order_id", "coupon_usages", "order_id", "订单使用优惠券"),
    ("users", "user_id", "coupon_usages", "user_id", "核销记录归属用户"),
    ("stores", "store_id", "ad_campaigns", "store_id", "店铺创建广告计划"),
    ("acquisition_channels", "channel_id", "ad_campaigns", "channel_id", "广告计划投放至渠道"),
    ("ad_campaigns", "ad_campaign_id", "ad_daily_stats", "ad_campaign_id", "广告计划产生每日效果"),
    ("users", "user_id", "recommendation_exposures", "user_id", "用户获得推荐曝光"),
    ("products", "product_id", "recommendation_exposures", "product_id", "商品获得推荐曝光"),
    ("users", "user_id", "shopping_carts", "user_id", "用户拥有购物车"),
    ("shopping_carts", "cart_id", "cart_items", "cart_id", "购物车包含商品"),
    ("product_skus", "sku_id", "cart_items", "sku_id", "购物车明细对应SKU"),
    ("users", "user_id", "favorite_products", "user_id", "用户收藏商品"),
    ("products", "product_id", "favorite_products", "product_id", "商品被用户收藏"),
    ("orders", "order_id", "order_items", "order_id", "订单包含商品明细"),
    ("products", "product_id", "order_items", "product_id", "订单明细对应商品"),
    ("product_skus", "sku_id", "order_items", "sku_id", "订单明细对应SKU"),
    ("users", "user_id", "orders", "user_id", "用户提交订单"),
    ("stores", "store_id", "orders", "store_id", "订单归属店铺"),
    ("acquisition_channels", "channel_id", "orders", "channel_id", "订单归因到渠道"),
    ("orders", "order_id", "payments", "order_id", "订单产生支付流水"),
    ("users", "user_id", "payments", "user_id", "支付流水归属用户"),
    ("orders", "order_id", "shipments", "order_id", "订单产生发货单"),
    ("warehouses", "warehouse_id", "shipments", "warehouse_id", "发货单从仓库发出"),
    ("shipments", "shipment_id", "shipment_tracks", "shipment_id", "发货单产生物流轨迹"),
    ("orders", "order_id", "refunds", "order_id", "订单产生退款申请"),
    ("users", "user_id", "refunds", "user_id", "退款申请归属用户"),
    ("refunds", "refund_id", "refund_items", "refund_id", "退款申请包含退款明细"),
    ("order_items", "order_item_id", "refund_items", "order_item_id", "退款明细对应订单明细"),
    ("product_skus", "sku_id", "refund_items", "sku_id", "退款明细对应SKU"),
    ("order_items", "order_item_id", "product_reviews", "order_item_id", "订单明细产生商品评价"),
    ("orders", "order_id", "product_reviews", "order_id", "评价关联订单"),
    ("users", "user_id", "product_reviews", "user_id", "评价由用户提交"),
    ("products", "product_id", "product_reviews", "product_id", "评价对应商品"),
    ("product_reviews", "review_id", "review_replies", "review_id", "评价获得店铺回复"),
    ("stores", "store_id", "review_replies", "store_id", "回复由店铺提交"),
    ("users", "user_id", "service_tickets", "user_id", "用户创建客服工单"),
    ("orders", "order_id", "service_tickets", "order_id", "客服工单关联订单"),
    ("service_tickets", "ticket_id", "ticket_messages", "ticket_id", "客服工单包含消息"),
    ("products", "product_id", "daily_product_metrics", "product_id", "商品对应日运营指标"),
    ("stores", "store_id", "daily_product_metrics", "store_id", "商品日指标归属店铺"),
    ("stores", "store_id", "daily_store_metrics", "store_id", "店铺对应日经营指标"),
    ("stores", "store_id", "sales_targets", "store_id", "店铺对应月度经营目标"),
]


def table_id(table_name: str) -> str:
    return f"{DATABASE}.{table_name}"


def domain_for(table_name: str) -> str:
    return next(domain for domain, tables in TABLE_DOMAINS.items() if table_name in tables)


def label_for(field_name: str) -> str:
    if field_name in FIELD_LABELS:
        return FIELD_LABELS[field_name]
    return "".join(TOKEN_LABELS.get(token, token.upper()) for token in field_name.split("_"))


def normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat(sep=" ")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def schema_type(source_type: str) -> str:
    upper = source_type.upper()
    if any(token in upper for token in ("INT", "HUGEINT")):
        return "整数"
    if any(token in upper for token in ("DOUBLE", "FLOAT", "DECIMAL", "REAL")):
        return "数值"
    if any(token in upper for token in ("DATE", "TIME")):
        return "日期"
    if "BOOL" in upper:
        return "布尔"
    return "文本"


def unit_for(field_name: str) -> str:
    if re.search(r"amount|price|cost|revenue|gmv|budget|fee", field_name):
        return "CNY"
    if re.search(r"rate|ratio|roi|depth", field_name):
        return "比例"
    if field_name.endswith("_seconds"):
        return "秒"
    if field_name.endswith("_days"):
        return "天"
    if field_name.endswith("_kg"):
        return "千克"
    if re.search(r"qty|quantity|stock", field_name):
        return "件"
    return ""


def role_for(table_name: str, field_name: str, field_type: str, foreign_fields: set[tuple[str, str]]) -> tuple[str, str]:
    if field_name in TABLE_PRIMARY_KEYS[table_name]:
        return "identifier", "none"
    if (table_name, field_name) in foreign_fields:
        return "foreign_key", "none"
    if field_type == "日期":
        return "time", "group"
    if field_type in {"整数", "数值"}:
        if re.search(r"rate|ratio|roi|score|depth", field_name):
            return "metric", "avg"
        if re.search(r"amount|price|cost|revenue|gmv|budget|fee|qty|quantity|stock|count|orders|buyers|visitors|impressions|clicks|conversions|points|duration|capacity", field_name):
            return "metric", "sum"
    if re.search(r"status|enabled|trusted|selected|converted|has_image|is_default", field_name):
        return "filter", "group"
    return "dimension", "group"


def relation_payload() -> list[dict[str, str]]:
    return [
        {
            "left_table": table_id(left_table),
            "left_table_name": left_table,
            "left_field": left_field,
            "right_table": table_id(right_table),
            "right_table_name": right_table,
            "right_field": right_field,
            "relation_type": "foreign_key",
            "description": description,
        }
        for left_table, left_field, right_table, right_field, description in RELATION_SPECS
    ]


def profile_field(connection: duckdb.DuckDBPyConnection, table_name: str, field_name: str, source_type: str) -> dict[str, Any]:
    table_sql = f'"{table_name}"'
    field_sql = f'"{field_name}"'
    total, non_null, distinct = connection.execute(
        f"SELECT COUNT(*), COUNT({field_sql}), COUNT(DISTINCT {field_sql}) FROM {table_sql}"
    ).fetchone()
    sample_values = [
        normalize(row[0])
        for row in connection.execute(
            f"SELECT DISTINCT {field_sql} FROM {table_sql} WHERE {field_sql} IS NOT NULL LIMIT 5"
        ).fetchall()
    ]
    profile: dict[str, Any] = {
        "sample_values": sample_values,
        "row_count": total,
        "null_ratio": round((total - non_null) / total, 6) if total else 0,
        "distinct_count": distinct,
        "duplicate_ratio": round((non_null - distinct) / non_null, 6) if non_null else 0,
        "unit": unit_for(field_name),
    }
    field_type = schema_type(source_type)
    if field_type in {"整数", "数值"}:
        minimum, maximum, average = connection.execute(
            f"SELECT MIN({field_sql}), MAX({field_sql}), AVG({field_sql}) FROM {table_sql}"
        ).fetchone()
        profile.update({"min": normalize(minimum), "max": normalize(maximum), "avg": round(float(average), 4) if average is not None else None})
    elif field_type == "日期":
        minimum, maximum = connection.execute(
            f"SELECT MIN({field_sql}), MAX({field_sql}) FROM {table_sql}"
        ).fetchone()
        profile.update({"min": normalize(minimum), "max": normalize(maximum), "format": "YYYY-MM-DD HH:mm:ss" if "TIME" in source_type.upper() else "YYYY-MM-DD"})
    if distinct <= 20:
        profile["top_values"] = [
            {"value": normalize(value), "count": count}
            for value, count in connection.execute(
                f"SELECT {field_sql}, COUNT(*) AS count FROM {table_sql} GROUP BY {field_sql} ORDER BY count DESC LIMIT 10"
            ).fetchall()
        ]
    return profile


def profile_summary(profile: dict[str, Any]) -> str:
    parts = [
        f"样例值：{'、'.join(str(value) for value in profile['sample_values']) or '无'}",
        f"空值率：{profile['null_ratio']:.2%}",
        f"不同值数量：{profile['distinct_count']}",
    ]
    if "min" in profile:
        parts.append(f"范围：{profile['min']} 至 {profile['max']}")
    if profile.get("avg") is not None:
        parts.append(f"均值：{profile['avg']}")
    if profile.get("unit"):
        parts.append(f"单位：{profile['unit']}")
    return "；".join(parts)


def generate() -> dict[str, Any]:
    manifest = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    relations = relation_payload()
    foreign_fields = {
        (relation["right_table_name"], relation["right_field"])
        for relation in relations
    }
    relation_lookup: dict[tuple[str, str], list[dict[str, str]]] = {}
    for relation in relations:
        relation_lookup.setdefault((relation["left_table_name"], relation["left_field"]), []).append(relation)
        relation_lookup.setdefault((relation["right_table_name"], relation["right_field"]), []).append(relation)

    tables = []
    connection = duckdb.connect(":memory:")
    try:
        for table_name, manifest_table in manifest["tables"].items():
            csv_path = (DATABASE_DIR / f"{table_name}.csv").resolve().as_posix().replace("'", "''")
            connection.execute(
                f'CREATE OR REPLACE VIEW "{table_name}" AS SELECT * FROM read_csv_auto(\'{csv_path}\', header=true, sample_size=-1)'
            )
            source_types = {
                row[0]: row[1]
                for row in connection.execute(f'DESCRIBE SELECT * FROM "{table_name}"').fetchall()
            }
            table_label = manifest_table["description"]
            domain = domain_for(table_name)
            business_terms = list(dict.fromkeys([table_label, domain, table_name.replace("_", " ")]))
            fields = []
            for field_name in manifest_table["columns"]:
                label = label_for(field_name)
                field_type = schema_type(source_types[field_name])
                role, aggregation = role_for(table_name, field_name, field_type, foreign_fields)
                profile = profile_field(connection, table_name, field_name, source_types[field_name])
                related = relation_lookup.get((table_name, field_name), [])
                related_text = "、".join(relation["description"] for relation in related)
                description = f"{table_label}中的{label}"
                if related_text:
                    description += f"；{related_text}"
                aliases = list(dict.fromkeys([label, *FIELD_ALIASES.get(field_name, [])]))
                summary = profile_summary(profile)
                keyword_text = " ".join([
                    DATABASE, table_name, table_label, domain, *business_terms,
                    field_name, label, description, *aliases,
                    *(str(value) for value in profile["sample_values"]),
                ])
                vector_text = (
                    f"字段名：{field_name}；字段语义：{description}；所属表：{table_name}（{table_label}）；"
                    f"业务域：{domain}；数据类型：{field_type}；业务表达：{'、'.join(aliases)}；{summary}。"
                )
                rerank_text = (
                    f"数据库：{DATABASE}；表：{table_name}（{table_label}）；表用途：{table_label}相关业务查询与分析；"
                    f"字段：{field_name}（{label}）；字段含义：{description}；原始类型：{source_types[field_name]}；"
                    f"字段角色：{role}；默认聚合：{aggregation}；同义词：{'、'.join(aliases)}；{summary}；"
                    f"关联信息：{related_text or '无'}。"
                )
                fields.append({
                    "name": field_name,
                    "label": label,
                    "type": field_type,
                    "source_type": source_types[field_name],
                    "description": description,
                    "aliases": aliases,
                    "role": role,
                    "aggregation": aggregation,
                    "data_profile": profile,
                    "index_content": {
                        "keyword_text": keyword_text,
                        "vector_text": vector_text,
                        "rerank_text": rerank_text,
                    },
                })
            tables.append({
                "id": table_id(table_name),
                "name": table_name,
                "label": table_label,
                "database": DATABASE,
                "domain": domain,
                "description": f"{table_label}，用于{domain}相关的查询、统计和关联分析。",
                "business_terms": business_terms,
                "primary_key": TABLE_PRIMARY_KEYS[table_name],
                "row_count": manifest_table["row_count"],
                "fields": fields,
            })
    finally:
        connection.close()

    payload = {
        "schema_version": "1.0.0",
        "database": DATABASE,
        "schema_layers": {
            "table_level": "表名、业务含义、用途、所属数据库和主键",
            "column_level": "字段名、数据类型、字段语义、别名、角色和聚合方式",
            "data_level": "样例值、数据范围、均值、枚举分布、空值率和重复率",
            "integrated_field_schema": "字段级Schema融合表级和数据级上下文，并生成三级索引文本",
        },
        "tables": tables,
        "relations": relations,
    }
    SCHEMA_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    schema = generate()
    field_count = sum(len(table["fields"]) for table in schema["tables"])
    print(json.dumps({"tables": len(schema["tables"]), "fields": field_count, "relations": len(schema["relations"]), "path": str(SCHEMA_PATH)}, ensure_ascii=False))
