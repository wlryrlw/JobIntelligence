from __future__ import annotations

import csv
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Sequence


SEED = 20260823
DATA_START = datetime(2025, 1, 1)
DATA_END = datetime(2026, 8, 22, 23, 59, 59)
DATABASE_DIR = Path(__file__).resolve().parents[1] / "data" / "databases" / "ecommerce_ops"

REGION_CITIES = {
    "华东": [("上海", "上海"), ("浙江", "杭州"), ("江苏", "南京"), ("山东", "青岛")],
    "华南": [("广东", "广州"), ("广东", "深圳"), ("福建", "厦门"), ("广西", "南宁")],
    "华北": [("北京", "北京"), ("天津", "天津"), ("河北", "石家庄"), ("山西", "太原")],
    "西南": [("四川", "成都"), ("重庆", "重庆"), ("云南", "昆明"), ("贵州", "贵阳")],
    "华中": [("湖北", "武汉"), ("湖南", "长沙"), ("河南", "郑州"), ("江西", "南昌")],
}

CATEGORY_TREE = {
    "数码家电": ["手机通讯", "电脑办公", "家用电器", "智能设备"],
    "服饰鞋包": ["男装", "女装", "运动鞋服", "箱包配饰"],
    "食品生鲜": ["休闲食品", "饮料冲调", "粮油调味", "生鲜果蔬"],
    "家居生活": ["家纺", "厨具", "清洁用品", "家具"],
    "美妆个护": ["护肤", "彩妆", "洗护", "个人护理"],
    "母婴玩具": ["婴童用品", "奶粉辅食", "玩具", "童装童鞋"],
    "运动户外": ["健身训练", "户外装备", "骑行", "体育用品"],
    "图书文娱": ["图书", "乐器", "文创", "宠物生活"],
}

TABLE_DESCRIPTIONS = {
    "users": "商城注册用户主表",
    "user_profiles": "用户画像和人口属性",
    "user_addresses": "用户收货地址",
    "user_memberships": "会员等级和有效期",
    "member_point_transactions": "会员积分变动流水",
    "user_tags": "用户标签定义",
    "user_tag_relations": "用户和标签的关联",
    "acquisition_channels": "用户和流量来源渠道",
    "user_devices": "用户常用设备",
    "user_sessions": "访问会话",
    "page_views": "页面浏览明细",
    "search_events": "站内搜索行为",
    "stores": "平台店铺",
    "store_staff": "店铺运营人员",
    "brands": "商品品牌",
    "product_categories": "商品类目层级",
    "products": "SPU商品主表",
    "product_skus": "SKU销售单元",
    "product_prices": "SKU价格变更历史",
    "warehouses": "履约仓库",
    "sku_inventory": "SKU当前库存",
    "inventory_movements": "库存出入库流水",
    "suppliers": "商品供应商",
    "purchase_orders": "采购单",
    "purchase_order_items": "采购单商品明细",
    "marketing_campaigns": "平台营销活动",
    "campaign_products": "营销活动商品",
    "coupons": "优惠券定义",
    "coupon_receipts": "用户领券记录",
    "coupon_usages": "优惠券核销记录",
    "ad_campaigns": "广告投放计划",
    "ad_daily_stats": "广告计划每日效果",
    "recommendation_exposures": "推荐位曝光和点击明细",
    "shopping_carts": "用户购物车",
    "cart_items": "购物车商品明细",
    "favorite_products": "用户收藏商品",
    "orders": "交易订单主表",
    "order_items": "订单商品明细",
    "payments": "订单支付流水",
    "shipments": "订单发货单",
    "shipment_tracks": "物流轨迹",
    "refunds": "退款申请",
    "refund_items": "退款商品明细",
    "product_reviews": "商品评价",
    "review_replies": "店铺评价回复",
    "service_tickets": "客服工单",
    "ticket_messages": "客服会话消息",
    "daily_product_metrics": "商品日运营指标",
    "daily_store_metrics": "店铺日经营指标",
    "sales_targets": "店铺月度经营目标",
}


class EcommerceDataGenerator:
    def __init__(self, output_dir: Path = DATABASE_DIR, seed: int = SEED) -> None:
        self.output_dir = output_dir
        self.random = random.Random(seed)
        self.seed = seed
        self.counts: dict[str, int] = {}
        self.schemas: dict[str, list[str]] = {}
        self.channels: list[dict] = []
        self.users: list[dict] = []
        self.devices: list[dict] = []
        self.sessions: list[dict] = []
        self.stores: list[dict] = []
        self.brands: list[dict] = []
        self.categories: list[dict] = []
        self.suppliers: list[dict] = []
        self.products: list[dict] = []
        self.skus: list[dict] = []
        self.warehouses: list[dict] = []
        self.campaigns: list[dict] = []
        self.coupons: list[dict] = []
        self.coupon_receipts: list[dict] = []
        self.orders: list[dict] = []
        self.order_items: list[dict] = []

    def generate(self) -> dict[str, int]:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.generate_users()
        self.generate_catalog()
        self.generate_behavior()
        self.generate_supply_chain()
        self.generate_marketing()
        self.generate_commerce()
        self.generate_after_sales()
        self.generate_metrics()
        self.write_manifest()
        return self.counts

    def write_csv(self, table: str, columns: Sequence[str], rows: Iterable[Sequence[object]]) -> int:
        path = self.output_dir / f"{table}.csv"
        count = 0
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            for row in rows:
                writer.writerow(row)
                count += 1
        self.counts[table] = count
        self.schemas[table] = list(columns)
        return count

    def random_datetime(self, start: datetime = DATA_START, end: datetime = DATA_END) -> datetime:
        seconds = int((end - start).total_seconds())
        return start + timedelta(seconds=self.random.randint(0, max(seconds, 0)))

    def choice(self, values: Sequence, weights: Sequence[int] | None = None):
        return self.random.choices(values, weights=weights, k=1)[0]

    def location(self, region: str | None = None) -> tuple[str, str, str]:
        selected_region = region or self.choice(list(REGION_CITIES), [30, 24, 18, 14, 14])
        province, city = self.random.choice(REGION_CITIES[selected_region])
        return selected_region, province, city

    def generate_users(self) -> None:
        channel_data = [
            ("CH01", "自然访问", "organic", "站内"),
            ("CH02", "搜索广告", "paid_search", "广告"),
            ("CH03", "信息流广告", "paid_social", "广告"),
            ("CH04", "短视频达人", "influencer", "内容"),
            ("CH05", "直播间", "live", "内容"),
            ("CH06", "短信召回", "sms", "私域"),
            ("CH07", "公众号", "wechat", "私域"),
            ("CH08", "应用商店", "app_store", "自然"),
            ("CH09", "好友邀请", "referral", "裂变"),
            ("CH10", "联盟推广", "affiliate", "广告"),
            ("CH11", "线下扫码", "offline", "线下"),
            ("CH12", "其他", "other", "其他"),
        ]
        self.channels = [
            {"channel_id": row[0], "channel_name": row[1], "channel_type": row[2]}
            for row in channel_data
        ]
        self.write_csv(
            "acquisition_channels",
            ["channel_id", "channel_name", "channel_type", "channel_group", "enabled"],
            [(*row, 1) for row in channel_data],
        )

        surnames = "赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦许何吕施张孔曹严华金魏陶姜谢邹苏潘葛范彭郎鲁韦马苗方俞任袁柳鲍史唐费廉岑薛雷贺倪汤滕殷罗毕郝安常乐于时傅皮卞齐康伍余元顾孟黄穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田樊胡凌霍虞万支柯管卢莫房裘缪解应宗丁宣邓郁单杭洪包左石崔吉龚程邢裴陆荣翁荀羊甄曲封芮羿储靳汲邴糜松井富乌焦巴弓牧隗山谷车侯宓蓬全班仰秋仲伊宫宁仇栾暴甘厉戎祖武符刘景詹束龙叶司黎薄白蒲赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡姬申扶堵冉宰郦雍郤璩桑桂濮牛寿通边扈燕冀郏浦尚农温别庄晏柴瞿阎充慕连茹习宦艾鱼容向古易慎戈廖庾终步都耿满弘匡国文寇广禄阙东欧沃利蔚越夔隆师巩厍聂晁勾敖融冷訾辛阚那简饶空曾毋沙养鞠须丰巢关蒯相查后荆红游竺权逯盖益桓公"
        given = "子涵宇轩梓萱欣怡浩然雨桐思远若曦一诺嘉怡明轩诗涵俊杰雅琪晨曦可欣博文静怡天佑佳宁昊然依诺语桐沐辰书瑶皓轩梦琪文博安然星宇清妍景行知夏云舒向阳泽宇思齐锦程心悦嘉言亦辰念初"
        users_rows = []
        profiles_rows = []
        for index in range(1, 5001):
            user_id = f"U{index:06d}"
            region, province, city = self.location()
            registered_at = self.random_datetime()
            channel = self.choice(self.channels, [18, 13, 12, 9, 7, 5, 7, 8, 6, 4, 2, 1])
            status = self.choice(["active", "inactive", "blocked"], [92, 7, 1])
            name = self.random.choice(surnames) + self.random.choice(given) + self.random.choice(given)
            user = {
                "user_id": user_id,
                "region": region,
                "province": province,
                "city": city,
                "registered_at": registered_at,
                "channel_id": channel["channel_id"],
                "status": status,
            }
            self.users.append(user)
            users_rows.append((
                user_id,
                f"星友{index:05d}",
                f"mock_{index:05d}@example.invalid",
                f"1{30 + index % 60:02d}****{index % 10000:04d}",
                channel["channel_id"],
                registered_at.strftime("%Y-%m-%d %H:%M:%S"),
                status,
                self.choice(["app", "web", "mini_program"], [62, 20, 18]),
            ))
            birthday = date(1965 + self.random.randrange(40), 1, 1) + timedelta(days=self.random.randrange(365))
            profiles_rows.append((
                user_id, name, self.choice(["男", "女", "未知"], [47, 49, 4]), birthday.isoformat(),
                region, province, city,
                self.choice(["企业职员", "自由职业", "学生", "个体经营", "专业人士", "其他"], [38, 14, 10, 12, 16, 10]),
                self.choice(["0-5k", "5k-10k", "10k-20k", "20k-40k", "40k以上", "未知"], [12, 25, 31, 19, 5, 8]),
                (registered_at + timedelta(days=self.random.randint(0, 400))).strftime("%Y-%m-%d %H:%M:%S"),
            ))
        self.write_csv(
            "users",
            ["user_id", "nickname", "email", "masked_mobile", "register_channel_id", "registered_at", "account_status", "register_platform"],
            users_rows,
        )
        self.write_csv(
            "user_profiles",
            ["user_id", "mock_name", "gender", "birthday", "region", "province", "city", "occupation", "income_band", "updated_at"],
            profiles_rows,
        )

        address_rows = []
        for index in range(1, 7501):
            user = self.random.choice(self.users)
            region, province, city = self.location(user["region"] if self.random.random() < 0.85 else None)
            address_rows.append((
                f"ADDR{index:07d}", user["user_id"], province, city, f"{city}城区", f"模拟路{index % 300 + 1}号",
                f"收货人{index % 1000:04d}", f"1{50 + index % 40:02d}****{index % 10000:04d}",
                1 if index <= 5000 else 0, region,
            ))
        self.write_csv(
            "user_addresses",
            ["address_id", "user_id", "province", "city", "district", "address_detail", "receiver_name", "masked_mobile", "is_default", "region"],
            address_rows,
        )

        member_users = self.random.sample(self.users, 4000)
        membership_rows = []
        for user in member_users:
            joined = user["registered_at"] + timedelta(days=self.random.randint(0, 120))
            level = self.choice(["普通会员", "银卡会员", "金卡会员", "黑金会员"], [50, 28, 17, 5])
            membership_rows.append((
                f"MB{len(membership_rows) + 1:06d}", user["user_id"], level,
                joined.date().isoformat(), (joined.date() + timedelta(days=365)).isoformat(),
                self.random.randint(0, 12000), self.random.randint(0, 15), "active",
            ))
        self.write_csv(
            "user_memberships",
            ["membership_id", "user_id", "member_level", "joined_date", "expire_date", "available_points", "growth_level", "status"],
            membership_rows,
        )

        point_rows = []
        for index in range(1, 12001):
            user = self.random.choice(member_users)
            change_type = self.choice(["order_reward", "campaign_reward", "refund_deduct", "redeem", "expire"], [48, 18, 10, 16, 8])
            sign = -1 if change_type in {"refund_deduct", "redeem", "expire"} else 1
            point_rows.append((
                f"PT{index:08d}", user["user_id"], change_type, sign * self.random.randint(5, 800),
                self.random_datetime().strftime("%Y-%m-%d %H:%M:%S"), f"MOCK{self.random.randint(1, 12000):07d}",
            ))
        self.write_csv(
            "member_point_transactions",
            ["point_transaction_id", "user_id", "change_type", "point_delta", "occurred_at", "reference_no"],
            point_rows,
        )

        tags = [
            ("TAG01", "高价值用户", "value"), ("TAG02", "价格敏感", "preference"),
            ("TAG03", "新品偏好", "preference"), ("TAG04", "数码爱好者", "category"),
            ("TAG05", "美妆人群", "category"), ("TAG06", "母婴家庭", "category"),
            ("TAG07", "运动达人", "category"), ("TAG08", "沉睡用户", "lifecycle"),
            ("TAG09", "新注册", "lifecycle"), ("TAG10", "高退款风险", "risk"),
            ("TAG11", "直播活跃", "behavior"), ("TAG12", "优惠券偏好", "behavior"),
            ("TAG13", "复购用户", "lifecycle"), ("TAG14", "大促活跃", "behavior"),
            ("TAG15", "企业采购", "identity"), ("TAG16", "内容种草", "behavior"),
        ]
        self.write_csv("user_tags", ["tag_id", "tag_name", "tag_type", "enabled"], [(*tag, 1) for tag in tags])
        self.write_csv(
            "user_tag_relations",
            ["relation_id", "user_id", "tag_id", "score", "assigned_at", "source"],
            [
                (f"UTR{index:08d}", self.random.choice(self.users)["user_id"], self.random.choice(tags)[0],
                 round(self.random.uniform(0.55, 1), 4), self.random_datetime().strftime("%Y-%m-%d %H:%M:%S"),
                 self.choice(["rule", "model", "manual"], [58, 38, 4]))
                for index in range(1, 8001)
            ],
        )

        device_rows = []
        for index in range(1, 7001):
            user = self.random.choice(self.users)
            device_type = self.choice(["Android", "iOS", "Windows", "macOS"], [48, 35, 13, 4])
            device = {"device_id": f"D{index:07d}", "user_id": user["user_id"], "device_type": device_type}
            self.devices.append(device)
            device_rows.append((
                device["device_id"], user["user_id"], device_type,
                self.choice(["星购App", "微信小程序", "网页"], [64, 20, 16]),
                self.choice(["5.8.0", "5.9.1", "6.0.0", "web"], [25, 34, 31, 10]),
                self.random_datetime().strftime("%Y-%m-%d %H:%M:%S"), 1,
            ))
        self.write_csv(
            "user_devices",
            ["device_id", "user_id", "device_type", "platform", "app_version", "last_active_at", "trusted"],
            device_rows,
        )

    def generate_catalog(self) -> None:
        store_types = ["自营", "品牌旗舰店", "专营店"]
        store_rows = []
        for index in range(1, 81):
            region, province, city = self.location()
            store = {
                "store_id": f"S{index:04d}",
                "store_name": f"{self.choice(['星选', '优品', '悦享', '臻品', '乐购', '新尚'])}{index:03d}店",
                "region": region,
                "opened_at": self.random_datetime(DATA_START - timedelta(days=800), DATA_START + timedelta(days=300)),
            }
            self.stores.append(store)
            store_rows.append((
                store["store_id"], store["store_name"], self.choice(store_types, [18, 52, 30]), region,
                province, city, store["opened_at"].date().isoformat(),
                self.choice(["active", "paused", "closed"], [94, 4, 2]), round(self.random.uniform(0.03, 0.16), 4),
            ))
        self.write_csv(
            "stores",
            ["store_id", "store_name", "store_type", "region", "province", "city", "opened_date", "status", "commission_rate"],
            store_rows,
        )

        staff_rows = []
        roles = ["店长", "商品运营", "活动运营", "客服", "仓配协调"]
        for index in range(1, 401):
            store = self.stores[(index - 1) % len(self.stores)]
            staff_rows.append((
                f"STF{index:05d}", store["store_id"], f"员工{index:04d}", self.choice(roles, [10, 24, 18, 36, 12]),
                f"staff{index:04d}@example.invalid", self.random_datetime(DATA_START - timedelta(days=500), DATA_END).date().isoformat(),
                self.choice(["active", "left"], [94, 6]),
            ))
        self.write_csv(
            "store_staff",
            ["staff_id", "store_id", "staff_name", "role_name", "email", "joined_date", "employment_status"],
            staff_rows,
        )

        brand_rows = []
        for index in range(1, 151):
            brand = {"brand_id": f"B{index:04d}", "brand_name": f"{self.choice(['星辰', '极光', '青禾', '云谷', '元气', '本色', '智造', '轻享', '悦己', '新境'])}{index:03d}"}
            self.brands.append(brand)
            brand_rows.append((brand["brand_id"], brand["brand_name"], self.choice(["国内", "进口"], [82, 18]), 1))
        self.write_csv("brands", ["brand_id", "brand_name", "origin_type", "enabled"], brand_rows)

        category_rows = []
        category_index = 1
        for parent_name, children in CATEGORY_TREE.items():
            parent_id = f"C{category_index:03d}"
            parent = {"category_id": parent_id, "category_name": parent_name, "parent_id": "", "level": 1}
            self.categories.append(parent)
            category_rows.append((parent_id, parent_name, "", 1, category_index, 1))
            category_index += 1
            for child in children:
                category_id = f"C{category_index:03d}"
                item = {"category_id": category_id, "category_name": child, "parent_id": parent_id, "level": 2}
                self.categories.append(item)
                category_rows.append((category_id, child, parent_id, 2, category_index, 1))
                category_index += 1
        self.write_csv(
            "product_categories",
            ["category_id", "category_name", "parent_category_id", "category_level", "sort_order", "enabled"],
            category_rows,
        )

        supplier_rows = []
        for index in range(1, 151):
            region, province, city = self.location()
            supplier = {"supplier_id": f"SUP{index:04d}", "region": region}
            self.suppliers.append(supplier)
            supplier_rows.append((
                supplier["supplier_id"], f"{city}模拟供应链{index:03d}号", region, province, city,
                self.choice(["A", "B", "C"], [28, 54, 18]), self.random.randint(5, 28),
                round(self.random.uniform(0.82, 0.99), 4), self.choice(["active", "suspended"], [97, 3]),
            ))
        self.write_csv(
            "suppliers",
            ["supplier_id", "supplier_name", "region", "province", "city", "supplier_grade", "lead_time_days", "on_time_rate", "status"],
            supplier_rows,
        )

        child_categories = [item for item in self.categories if item["level"] == 2]
        product_rows = []
        for index in range(1, 1501):
            category = self.random.choice(child_categories)
            store = self.stores[(index - 1) % len(self.stores)]
            brand = self.random.choice(self.brands)
            supplier = self.random.choice(self.suppliers)
            base_price = round(self.random.uniform(19, 3500), 2)
            product = {
                "product_id": f"P{index:06d}", "store_id": store["store_id"],
                "category_id": category["category_id"], "brand_id": brand["brand_id"],
                "supplier_id": supplier["supplier_id"], "base_price": base_price,
            }
            self.products.append(product)
            product_rows.append((
                product["product_id"], f"{brand['brand_name']}{category['category_name']}商品{index:04d}", store["store_id"],
                brand["brand_id"], category["category_id"], supplier["supplier_id"],
                self.choice(["active", "inactive", "draft"], [91, 6, 3]),
                self.random_datetime(DATA_START - timedelta(days=300), DATA_END).strftime("%Y-%m-%d %H:%M:%S"),
                round(self.random.uniform(0.2, 12), 2), self.random.randint(0, 24),
            ))
        self.write_csv(
            "products",
            ["product_id", "product_name", "store_id", "brand_id", "category_id", "supplier_id", "product_status", "created_at", "weight_kg", "warranty_months"],
            product_rows,
        )

        sku_rows = []
        sku_index = 1
        colors = ["标准款", "曜石黑", "云朵白", "星空蓝", "暖杏色", "活力橙"]
        specs = ["基础版", "升级版", "旗舰版", "小号", "中号", "大号"]
        for product in self.products:
            sku_count = 2 if sku_index <= 3000 else 1
            for variant in range(sku_count):
                sku_id = f"SKU{sku_index:07d}"
                market_price = round(product["base_price"] * self.random.uniform(1.05, 1.35), 2)
                sale_price = round(product["base_price"] * self.random.uniform(0.82, 1.05), 2)
                cost_price = round(sale_price * self.random.uniform(0.48, 0.78), 2)
                sku = {
                    "sku_id": sku_id, "product_id": product["product_id"], "store_id": product["store_id"],
                    "sale_price": sale_price, "cost_price": cost_price,
                }
                self.skus.append(sku)
                sku_rows.append((
                    sku_id, product["product_id"], f"{self.random.choice(colors)}-{self.random.choice(specs)}",
                    self.random.choice(colors), self.random.choice(specs), market_price, sale_price, cost_price,
                    f"69{sku_index:011d}", self.choice(["active", "inactive"], [95, 5]),
                ))
                sku_index += 1
        self.write_csv(
            "product_skus",
            ["sku_id", "product_id", "sku_name", "color", "specification", "market_price", "sale_price", "cost_price", "barcode", "sku_status"],
            sku_rows,
        )

        price_rows = []
        for sku in self.skus:
            first_date = self.random_datetime(DATA_START, DATA_END - timedelta(days=90))
            second_date = first_date + timedelta(days=self.random.randint(30, 180))
            price_rows.append((f"PR{len(price_rows) + 1:08d}", sku["sku_id"], round(sku["sale_price"] * self.random.uniform(0.92, 1.12), 2), first_date.date().isoformat(), min(second_date, DATA_END).date().isoformat(), "regular"))
            price_rows.append((f"PR{len(price_rows) + 1:08d}", sku["sku_id"], sku["sale_price"], min(second_date, DATA_END).date().isoformat(), "", self.choice(["regular", "campaign"], [72, 28])))
        self.write_csv(
            "product_prices",
            ["price_id", "sku_id", "price", "effective_from", "effective_to", "price_type"],
            price_rows,
        )

        warehouse_rows = []
        for index in range(1, 17):
            region = list(REGION_CITIES)[(index - 1) % len(REGION_CITIES)]
            _, province, city = self.location(region)
            warehouse = {"warehouse_id": f"W{index:03d}", "region": region}
            self.warehouses.append(warehouse)
            warehouse_rows.append((warehouse["warehouse_id"], f"{city}{self.choice(['中心仓', '前置仓', '协同仓'], [55, 30, 15])}{index:02d}", region, province, city, self.random.randint(5000, 50000), "active"))
        self.write_csv(
            "warehouses",
            ["warehouse_id", "warehouse_name", "region", "province", "city", "capacity_units", "status"],
            warehouse_rows,
        )

    def generate_behavior(self) -> None:
        session_rows = []
        for index in range(1, 20001):
            user = self.random.choice(self.users) if self.random.random() < 0.9 else None
            device = self.random.choice(self.devices)
            channel = self.choice(self.channels, [18, 13, 12, 9, 7, 5, 7, 8, 6, 4, 2, 1])
            started_at = self.random_datetime()
            duration = max(8, int(self.random.lognormvariate(5.0, 0.8)))
            session = {
                "session_id": f"SES{index:08d}", "user_id": user["user_id"] if user else "",
                "device_id": device["device_id"], "started_at": started_at, "duration": duration,
            }
            self.sessions.append(session)
            session_rows.append((
                session["session_id"], session["user_id"], device["device_id"], channel["channel_id"],
                started_at.strftime("%Y-%m-%d %H:%M:%S"), (started_at + timedelta(seconds=duration)).strftime("%Y-%m-%d %H:%M:%S"),
                duration, self.choice(["home", "search", "campaign", "product", "live"], [38, 18, 14, 24, 6]),
                1 if self.random.random() < 0.18 else 0,
            ))
        self.write_csv(
            "user_sessions",
            ["session_id", "user_id", "device_id", "channel_id", "started_at", "ended_at", "duration_seconds", "landing_page", "converted"],
            session_rows,
        )

        page_types = ["home", "search", "category", "product", "cart", "campaign", "checkout"]
        def page_rows():
            for index in range(1, 50001):
                session = self.random.choice(self.sessions)
                page_type = self.choice(page_types, [16, 15, 14, 38, 6, 8, 3])
                product_id = self.random.choice(self.products)["product_id"] if page_type == "product" else ""
                offset = self.random.randint(0, session["duration"])
                yield (
                    f"PV{index:09d}", session["session_id"], session["user_id"], page_type, product_id,
                    self.choice(["banner", "search", "recommendation", "category", "direct"], [12, 22, 34, 20, 12]),
                    (session["started_at"] + timedelta(seconds=offset)).strftime("%Y-%m-%d %H:%M:%S"),
                    self.random.randint(3, 240), round(self.random.uniform(0, 1), 4),
                )
        self.write_csv(
            "page_views",
            ["view_id", "session_id", "user_id", "page_type", "product_id", "source_position", "viewed_at", "stay_seconds", "scroll_depth"],
            page_rows(),
        )

        keywords = [category["category_name"] for category in self.categories] + ["开学季", "送礼", "新品", "性价比", "国货", "智能", "轻便", "家用", "夏季", "折扣"]
        def search_rows():
            for index in range(1, 12001):
                session = self.random.choice(self.sessions)
                keyword = self.random.choice(keywords)
                result_count = self.random.randint(0, 800)
                yield (
                    f"SE{index:08d}", session["session_id"], session["user_id"], keyword,
                    self.random.choice([item["category_id"] for item in self.categories if item["level"] == 2]),
                    result_count, 1 if result_count and self.random.random() < 0.57 else 0,
                    1 if result_count and self.random.random() < 0.08 else 0,
                    (session["started_at"] + timedelta(seconds=self.random.randint(0, session["duration"]))).strftime("%Y-%m-%d %H:%M:%S"),
                )
        self.write_csv(
            "search_events",
            ["search_id", "session_id", "user_id", "keyword", "category_id", "result_count", "clicked", "converted", "searched_at"],
            search_rows(),
        )

    def generate_supply_chain(self) -> None:
        inventory_rows = []
        sku_warehouse: dict[str, str] = {}
        for sku in self.skus:
            warehouse = self.random.choice(self.warehouses)
            sku_warehouse[sku["sku_id"]] = warehouse["warehouse_id"]
            available = self.random.randint(0, 800)
            reserved = self.random.randint(0, min(available, 80))
            inventory_rows.append((sku["sku_id"], warehouse["warehouse_id"], available, reserved, self.random.randint(20, 150), self.random.randint(200, 1200), DATA_END.strftime("%Y-%m-%d %H:%M:%S")))
        self.write_csv(
            "sku_inventory",
            ["sku_id", "warehouse_id", "available_qty", "reserved_qty", "safety_stock", "max_stock", "updated_at"],
            inventory_rows,
        )

        movement_types = ["purchase_in", "sale_out", "return_in", "adjustment", "transfer_in", "transfer_out"]
        def movement_rows():
            for index in range(1, 10001):
                sku = self.random.choice(self.skus)
                movement_type = self.choice(movement_types, [25, 50, 7, 5, 7, 6])
                sign = -1 if movement_type in {"sale_out", "transfer_out"} else 1
                yield (
                    f"IM{index:08d}", sku["sku_id"], sku_warehouse[sku["sku_id"]], movement_type,
                    sign * self.random.randint(1, 80), self.random_datetime().strftime("%Y-%m-%d %H:%M:%S"),
                    f"REF{self.random.randint(1, 15000):08d}", self.choice(["system", "purchase", "order", "warehouse"], [12, 22, 48, 18]),
                )
        self.write_csv(
            "inventory_movements",
            ["movement_id", "sku_id", "warehouse_id", "movement_type", "quantity_delta", "occurred_at", "reference_no", "source_system"],
            movement_rows(),
        )

        purchase_orders = []
        purchase_rows = []
        for index in range(1, 801):
            supplier = self.random.choice(self.suppliers)
            warehouse = self.random.choice(self.warehouses)
            created = self.random_datetime()
            status = self.choice(["created", "approved", "shipped", "received", "cancelled"], [4, 8, 12, 72, 4])
            po = {"purchase_order_id": f"PO{index:06d}", "supplier_id": supplier["supplier_id"], "warehouse_id": warehouse["warehouse_id"], "created_at": created, "status": status}
            purchase_orders.append(po)
            purchase_rows.append((po["purchase_order_id"], supplier["supplier_id"], warehouse["warehouse_id"], created.strftime("%Y-%m-%d %H:%M:%S"), (created + timedelta(days=self.random.randint(5, 24))).date().isoformat(), status, 0, "CNY"))

        po_item_rows = []
        totals = {po["purchase_order_id"]: 0.0 for po in purchase_orders}
        for index in range(1, 3001):
            po = purchase_orders[(index - 1) % len(purchase_orders)]
            sku = self.random.choice(self.skus)
            quantity = self.random.randint(20, 500)
            unit_cost = round(sku["cost_price"] * self.random.uniform(0.9, 1.03), 2)
            received = quantity if po["status"] == "received" else self.random.randint(0, quantity) if po["status"] == "shipped" else 0
            totals[po["purchase_order_id"]] += quantity * unit_cost
            po_item_rows.append((f"POI{index:07d}", po["purchase_order_id"], sku["sku_id"], quantity, received, unit_cost, round(quantity * unit_cost, 2)))
        purchase_rows = [(*row[:6], round(totals[row[0]], 2), row[7]) for row in purchase_rows]
        self.write_csv(
            "purchase_orders",
            ["purchase_order_id", "supplier_id", "warehouse_id", "created_at", "expected_date", "status", "purchase_amount", "currency"],
            purchase_rows,
        )
        self.write_csv(
            "purchase_order_items",
            ["purchase_item_id", "purchase_order_id", "sku_id", "ordered_qty", "received_qty", "unit_cost", "line_amount"],
            po_item_rows,
        )

    def generate_marketing(self) -> None:
        campaign_types = ["平台满减", "品类日", "品牌日", "会员专享", "新品首发", "限时秒杀"]
        campaign_rows = []
        for index in range(1, 101):
            starts_at = self.random_datetime()
            ends_at = min(starts_at + timedelta(days=self.random.randint(1, 18)), DATA_END)
            campaign = {"campaign_id": f"MC{index:04d}", "starts_at": starts_at, "ends_at": ends_at}
            self.campaigns.append(campaign)
            campaign_rows.append((
                campaign["campaign_id"], f"{starts_at:%Y%m}{self.random.choice(campaign_types)}{index:03d}",
                self.random.choice(campaign_types), starts_at.strftime("%Y-%m-%d %H:%M:%S"), ends_at.strftime("%Y-%m-%d %H:%M:%S"),
                round(self.random.uniform(10000, 800000), 2), self.choice(["platform", "store", "joint"], [48, 32, 20]),
                "finished" if ends_at < DATA_END else "active",
            ))
        self.write_csv(
            "marketing_campaigns",
            ["campaign_id", "campaign_name", "campaign_type", "starts_at", "ends_at", "budget", "sponsor_type", "status"],
            campaign_rows,
        )

        self.write_csv(
            "campaign_products",
            ["campaign_product_id", "campaign_id", "product_id", "campaign_price", "stock_limit", "per_user_limit"],
            [
                (f"MCP{index:07d}", self.random.choice(self.campaigns)["campaign_id"], product["product_id"],
                 round(product["base_price"] * self.random.uniform(0.62, 0.92), 2), self.random.randint(30, 1200), self.random.randint(1, 5))
                for index, product in enumerate((self.random.choice(self.products) for _ in range(1500)), 1)
            ],
        )

        coupon_rows = []
        for index in range(1, 181):
            coupon_type = self.choice(["满减券", "折扣券", "无门槛券", "运费券"], [48, 22, 18, 12])
            threshold = self.choice([0, 99, 199, 399, 699], [12, 21, 29, 24, 14])
            discount = round(self.random.uniform(5, min(150, max(10, threshold * 0.3))), 2)
            starts_at = self.random_datetime()
            ends_at = min(starts_at + timedelta(days=self.random.randint(7, 45)), DATA_END)
            coupon = {"coupon_id": f"CP{index:05d}", "starts_at": starts_at, "ends_at": ends_at, "threshold": threshold, "discount": discount}
            self.coupons.append(coupon)
            coupon_rows.append((
                coupon["coupon_id"], f"{coupon_type}{index:03d}", coupon_type, threshold, discount,
                starts_at.date().isoformat(), ends_at.date().isoformat(), self.random.randint(500, 20000),
                self.choice(["all", "category", "store"], [56, 27, 17]), "finished" if ends_at < DATA_END else "active",
            ))
        self.write_csv(
            "coupons",
            ["coupon_id", "coupon_name", "coupon_type", "threshold_amount", "discount_amount", "valid_from", "valid_to", "issue_limit", "scope_type", "status"],
            coupon_rows,
        )

        receipt_rows = []
        for index in range(1, 8001):
            user = self.random.choice(self.users)
            coupon = self.random.choice(self.coupons)
            received_at = self.random_datetime(coupon["starts_at"], coupon["ends_at"])
            status = self.choice(["unused", "used", "expired"], [24, 46, 30])
            receipt = {"receipt_id": f"CR{index:08d}", "coupon_id": coupon["coupon_id"], "user_id": user["user_id"], "status": status, "received_at": received_at}
            self.coupon_receipts.append(receipt)
            receipt_rows.append((receipt["receipt_id"], coupon["coupon_id"], user["user_id"], received_at.strftime("%Y-%m-%d %H:%M:%S"), status, self.choice(["campaign", "member_center", "new_user", "push"], [42, 24, 16, 18])))
        self.write_csv(
            "coupon_receipts",
            ["receipt_id", "coupon_id", "user_id", "received_at", "receipt_status", "source"],
            receipt_rows,
        )

        ad_campaign_rows = []
        ads = []
        for index in range(1, 101):
            store = self.random.choice(self.stores)
            channel = self.choice(self.channels[1:10])
            start_date = self.random_datetime().date()
            end_date = min(start_date + timedelta(days=self.random.randint(10, 90)), DATA_END.date())
            ad = {"ad_campaign_id": f"AD{index:05d}", "store_id": store["store_id"], "start_date": start_date, "end_date": end_date}
            ads.append(ad)
            ad_campaign_rows.append((ad["ad_campaign_id"], f"{store['store_name']}投放{index:03d}", store["store_id"], channel["channel_id"], self.choice(["cpc", "cpm", "ocpc"], [42, 28, 30]), round(self.random.uniform(5000, 180000), 2), start_date.isoformat(), end_date.isoformat(), "finished" if end_date < DATA_END.date() else "active"))
        self.write_csv(
            "ad_campaigns",
            ["ad_campaign_id", "ad_campaign_name", "store_id", "channel_id", "billing_mode", "budget", "start_date", "end_date", "status"],
            ad_campaign_rows,
        )

        def ad_stat_rows():
            for index in range(1, 5001):
                ad = ads[(index - 1) % len(ads)]
                span = max((ad["end_date"] - ad["start_date"]).days, 0)
                stat_date = ad["start_date"] + timedelta(days=self.random.randint(0, span))
                impressions = self.random.randint(1000, 180000)
                clicks = int(impressions * self.random.uniform(0.008, 0.08))
                conversions = int(clicks * self.random.uniform(0.015, 0.15))
                cost = round(clicks * self.random.uniform(0.3, 4.8), 2)
                revenue = round(conversions * self.random.uniform(60, 680), 2)
                yield (f"ADS{index:08d}", ad["ad_campaign_id"], stat_date.isoformat(), impressions, clicks, conversions, cost, revenue, round(revenue / cost, 4) if cost else 0)
        self.write_csv(
            "ad_daily_stats",
            ["ad_stat_id", "ad_campaign_id", "stat_date", "impressions", "clicks", "conversions", "ad_cost", "attributed_revenue", "roi"],
            ad_stat_rows(),
        )

        def exposure_rows():
            positions = ["首页猜你喜欢", "商品详情相似推荐", "购物车凑单", "搜索联想", "会员频道"]
            for index in range(1, 25001):
                user = self.random.choice(self.users)
                product = self.random.choice(self.products)
                exposed_at = self.random_datetime()
                clicked = self.random.random() < 0.12
                yield (
                    f"RE{index:09d}", user["user_id"], product["product_id"], self.random.choice(positions),
                    self.choice(["recommend_v3", "recommend_v4", "popular", "rule_based"], [38, 35, 12, 15]),
                    exposed_at.strftime("%Y-%m-%d %H:%M:%S"), int(clicked), int(clicked and self.random.random() < 0.09),
                )
        self.write_csv(
            "recommendation_exposures",
            ["exposure_id", "user_id", "product_id", "position_name", "strategy_name", "exposed_at", "clicked", "converted"],
            exposure_rows(),
        )

    def generate_commerce(self) -> None:
        cart_rows = []
        carts = []
        for index in range(1, 6001):
            user = self.random.choice(self.users)
            updated_at = self.random_datetime()
            cart = {"cart_id": f"CART{index:07d}", "user_id": user["user_id"], "updated_at": updated_at}
            carts.append(cart)
            cart_rows.append((cart["cart_id"], user["user_id"], updated_at.strftime("%Y-%m-%d %H:%M:%S"), self.choice(["active", "converted", "abandoned"], [36, 41, 23]), self.choice(["app", "web", "mini_program"], [65, 17, 18])))
        self.write_csv("shopping_carts", ["cart_id", "user_id", "updated_at", "cart_status", "platform"], cart_rows)

        self.write_csv(
            "cart_items",
            ["cart_item_id", "cart_id", "sku_id", "quantity", "unit_price", "added_at", "selected"],
            [
                (f"CI{index:08d}", cart["cart_id"], sku["sku_id"], self.random.randint(1, 4), sku["sale_price"],
                 (cart["updated_at"] - timedelta(days=self.random.randint(0, 30))).strftime("%Y-%m-%d %H:%M:%S"),
                 1 if self.random.random() < 0.72 else 0)
                for index, (cart, sku) in enumerate(((self.random.choice(carts), self.random.choice(self.skus)) for _ in range(12000)), 1)
            ],
        )

        self.write_csv(
            "favorite_products",
            ["favorite_id", "user_id", "product_id", "favorited_at", "source_page"],
            [
                (f"FAV{index:08d}", self.random.choice(self.users)["user_id"], self.random.choice(self.products)["product_id"],
                 self.random_datetime().strftime("%Y-%m-%d %H:%M:%S"), self.choice(["product", "search", "recommendation", "campaign"], [58, 16, 18, 8]))
                for index in range(1, 8001)
            ],
        )

        skus_by_store: dict[str, list[dict]] = {}
        for sku in self.skus:
            skus_by_store.setdefault(sku["store_id"], []).append(sku)
        order_statuses = ["pending_payment", "paid", "shipped", "completed", "cancelled", "refunded"]
        for index in range(1, 12001):
            user = self.random.choice(self.users)
            store = self.random.choice(self.stores)
            ordered_at = self.random_datetime()
            order_age = (DATA_END - ordered_at).days
            if order_age < 2:
                status_weights = [12, 48, 28, 2, 10, 0]
            elif order_age < 7:
                status_weights = [2, 14, 35, 42, 5, 2]
            else:
                status_weights = [1, 5, 13, 68, 7, 6]
            status = self.choice(order_statuses, status_weights)
            order = {
                "order_id": f"O{index:08d}", "user_id": user["user_id"], "store_id": store["store_id"],
                "channel_id": self.choice(self.channels, [18, 13, 12, 9, 7, 5, 7, 8, 6, 4, 2, 1])["channel_id"],
                "ordered_at": ordered_at, "status": status, "region": user["region"], "province": user["province"], "city": user["city"],
                "item_amount": 0.0, "discount_amount": 0.0, "shipping_fee": 0.0, "payable_amount": 0.0, "paid_amount": 0.0,
            }
            self.orders.append(order)

        item_index = 1
        for order in self.orders:
            candidates = skus_by_store[order["store_id"]]
            for _ in range(self.choice([1, 2, 3, 4], [52, 30, 13, 5])):
                sku = self.random.choice(candidates)
                quantity = self.choice([1, 2, 3, 4], [76, 17, 5, 2])
                unit_price = round(sku["sale_price"] * self.random.uniform(0.94, 1.02), 2)
                discount = round(unit_price * quantity * self.random.uniform(0, 0.18), 2)
                paid = round(unit_price * quantity - discount, 2)
                item = {
                    "order_item_id": f"OI{item_index:09d}", "order_id": order["order_id"], "sku_id": sku["sku_id"],
                    "product_id": sku["product_id"], "quantity": quantity, "unit_price": unit_price,
                    "discount_amount": discount, "paid_amount": paid, "cost_amount": round(sku["cost_price"] * quantity, 2),
                }
                self.order_items.append(item)
                order["item_amount"] += unit_price * quantity
                order["discount_amount"] += discount
                item_index += 1
            order["shipping_fee"] = 0 if order["item_amount"] >= 99 else self.choice([6, 8, 10], [35, 50, 15])
            order["payable_amount"] = round(order["item_amount"] - order["discount_amount"] + order["shipping_fee"], 2)
            order["paid_amount"] = 0 if order["status"] in {"pending_payment", "cancelled"} else order["payable_amount"]

        self.write_csv(
            "orders",
            ["order_id", "user_id", "store_id", "channel_id", "ordered_at", "order_status", "item_amount", "discount_amount", "shipping_fee", "payable_amount", "paid_amount", "region", "province", "city"],
            [
                (order["order_id"], order["user_id"], order["store_id"], order["channel_id"], order["ordered_at"].strftime("%Y-%m-%d %H:%M:%S"), order["status"],
                 round(order["item_amount"], 2), round(order["discount_amount"], 2), order["shipping_fee"], order["payable_amount"], order["paid_amount"], order["region"], order["province"], order["city"])
                for order in self.orders
            ],
        )
        self.write_csv(
            "order_items",
            ["order_item_id", "order_id", "product_id", "sku_id", "quantity", "unit_price", "discount_amount", "paid_amount", "cost_amount"],
            [(item["order_item_id"], item["order_id"], item["product_id"], item["sku_id"], item["quantity"], item["unit_price"], item["discount_amount"], item["paid_amount"], item["cost_amount"]) for item in self.order_items],
        )

        paid_orders = [order for order in self.orders if order["paid_amount"] > 0]
        payment_rows = []
        payment_index = 1
        for order in paid_orders:
            paid_at = order["ordered_at"] + timedelta(minutes=self.random.randint(1, 90))
            payment_rows.append((f"PAY{payment_index:08d}", order["order_id"], order["user_id"], self.choice(["wechat", "alipay", "bank_card", "wallet"], [45, 37, 12, 6]), order["paid_amount"], "success", paid_at.strftime("%Y-%m-%d %H:%M:%S"), f"MOCKTX{payment_index:010d}"))
            payment_index += 1
            if self.random.random() < 0.08:
                payment_rows.append((f"PAY{payment_index:08d}", order["order_id"], order["user_id"], self.choice(["wechat", "alipay", "bank_card"], [45, 40, 15]), order["paid_amount"], "failed", (paid_at - timedelta(minutes=2)).strftime("%Y-%m-%d %H:%M:%S"), f"MOCKTX{payment_index:010d}"))
                payment_index += 1
        self.write_csv(
            "payments",
            ["payment_id", "order_id", "user_id", "payment_method", "payment_amount", "payment_status", "paid_at", "transaction_no"],
            payment_rows,
        )

        shippable_orders = [order for order in self.orders if order["status"] in {"shipped", "completed", "refunded"}]
        shipment_rows = []
        shipment_tracks = []
        carriers = ["顺丰速运", "京东物流", "中通快递", "圆通速递", "韵达快递"]
        for index, order in enumerate(shippable_orders, 1):
            shipped_at = order["ordered_at"] + timedelta(hours=self.random.randint(8, 72))
            delivered = order["status"] in {"completed", "refunded"}
            delivered_at = shipped_at + timedelta(days=self.random.randint(1, 6)) if delivered else None
            shipment_id = f"SHP{index:08d}"
            warehouse = self.random.choice(self.warehouses)
            carrier = self.random.choice(carriers)
            shipment_rows.append((shipment_id, order["order_id"], warehouse["warehouse_id"], carrier, f"MOCK{index:012d}", shipped_at.strftime("%Y-%m-%d %H:%M:%S"), delivered_at.strftime("%Y-%m-%d %H:%M:%S") if delivered_at else "", "delivered" if delivered else "in_transit", self.random.randint(1, 4)))
            events = [("已揽收", shipped_at), ("运输中", shipped_at + timedelta(hours=12))]
            if delivered_at:
                events.extend([("派送中", delivered_at - timedelta(hours=6)), ("已签收", delivered_at)])
            for status_text, event_at in events:
                shipment_tracks.append((f"TRK{len(shipment_tracks) + 1:09d}", shipment_id, status_text, order["city"], event_at.strftime("%Y-%m-%d %H:%M:%S")))
        self.write_csv(
            "shipments",
            ["shipment_id", "order_id", "warehouse_id", "carrier_name", "tracking_no", "shipped_at", "delivered_at", "shipment_status", "package_count"],
            shipment_rows,
        )
        self.write_csv(
            "shipment_tracks",
            ["track_id", "shipment_id", "track_status", "location", "occurred_at"],
            shipment_tracks,
        )

        orders_by_user: dict[str, list[dict]] = {}
        for order in paid_orders:
            orders_by_user.setdefault(order["user_id"], []).append(order)
        coupons_by_id = {coupon["coupon_id"]: coupon for coupon in self.coupons}
        used_receipts = [receipt for receipt in self.coupon_receipts if receipt["status"] == "used"]
        usage_rows = []
        for index, receipt in enumerate(used_receipts, 1):
            user_orders = orders_by_user.get(receipt["user_id"], [])
            order = self.random.choice(user_orders) if user_orders else self.random.choice(paid_orders)
            coupon = coupons_by_id[receipt["coupon_id"]]
            usage_rows.append((f"CU{index:08d}", receipt["receipt_id"], receipt["coupon_id"], order["order_id"], receipt["user_id"], min(coupon["discount"], order["paid_amount"]), (order["ordered_at"] + timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M:%S")))
        self.write_csv(
            "coupon_usages",
            ["usage_id", "receipt_id", "coupon_id", "order_id", "user_id", "discount_amount", "used_at"],
            usage_rows,
        )

    def generate_after_sales(self) -> None:
        orders_by_id = {order["order_id"]: order for order in self.orders}
        users_by_id = {user["user_id"]: user for user in self.users}
        refundable_orders = [order for order in self.orders if order["status"] in {"completed", "shipped", "refunded"}]
        selected_orders = self.random.sample(refundable_orders, min(1600, len(refundable_orders)))
        refund_rows = []
        refund_item_rows = []
        items_by_order: dict[str, list[dict]] = {}
        for item in self.order_items:
            items_by_order.setdefault(item["order_id"], []).append(item)
        for index, order in enumerate(selected_orders, 1):
            refund_id = f"RF{index:07d}"
            order_items = items_by_order[order["order_id"]]
            selected_items = self.random.sample(order_items, self.random.randint(1, min(2, len(order_items))))
            amount = round(sum(item["paid_amount"] for item in selected_items), 2)
            applied_at = order["ordered_at"] + timedelta(days=self.random.randint(1, 25))
            status = self.choice(["approved", "completed", "rejected", "processing"], [18, 67, 7, 8])
            refund_rows.append((refund_id, order["order_id"], order["user_id"], self.choice(["质量问题", "尺码不合适", "未按时到货", "拍错或多拍", "不喜欢", "商品破损"], [18, 15, 12, 19, 25, 11]), amount, status, applied_at.strftime("%Y-%m-%d %H:%M:%S"), (applied_at + timedelta(days=self.random.randint(1, 5))).strftime("%Y-%m-%d %H:%M:%S") if status in {"completed", "rejected"} else ""))
            for item in selected_items:
                refund_item_rows.append((f"RFI{len(refund_item_rows) + 1:08d}", refund_id, item["order_item_id"], item["sku_id"], item["quantity"], item["paid_amount"], self.choice(["return_and_refund", "refund_only"], [72, 28])))
        self.write_csv(
            "refunds",
            ["refund_id", "order_id", "user_id", "refund_reason", "refund_amount", "refund_status", "applied_at", "completed_at"],
            refund_rows,
        )
        self.write_csv(
            "refund_items",
            ["refund_item_id", "refund_id", "order_item_id", "sku_id", "refund_quantity", "refund_amount", "refund_type"],
            refund_item_rows,
        )

        completed_items = [item for item in self.order_items if orders_by_id[item["order_id"]]["status"] == "completed"]
        review_source = self.random.sample(completed_items, min(6000, len(completed_items)))
        review_rows = []
        reviews = []
        for index, item in enumerate(review_source, 1):
            order = orders_by_id[item["order_id"]]
            score = self.choice([1, 2, 3, 4, 5], [3, 4, 9, 25, 59])
            review = {"review_id": f"RV{index:08d}", "store_id": order["store_id"], "created_at": order["ordered_at"] + timedelta(days=self.random.randint(3, 30))}
            reviews.append(review)
            review_rows.append((review["review_id"], item["order_item_id"], order["order_id"], order["user_id"], item["product_id"], score, self.choice(["质量很好，符合预期", "物流很快，包装完整", "整体满意", "性价比不错", "体验一般", "与描述有差异"], [26, 18, 24, 18, 9, 5]), int(self.random.random() < 0.12), review["created_at"].strftime("%Y-%m-%d %H:%M:%S"), self.choice(["visible", "hidden"], [99, 1])))
        self.write_csv(
            "product_reviews",
            ["review_id", "order_item_id", "order_id", "user_id", "product_id", "rating", "review_content", "has_image", "created_at", "review_status"],
            review_rows,
        )

        replied_reviews = self.random.sample(reviews, min(1000, len(reviews)))
        self.write_csv(
            "review_replies",
            ["reply_id", "review_id", "store_id", "reply_content", "replied_at", "reply_type"],
            [
                (f"RVR{index:07d}", review["review_id"], review["store_id"], self.choice(["感谢支持，期待再次光临", "感谢反馈，我们会持续改进", "已联系您协助处理", "祝您生活愉快"]),
                 (review["created_at"] + timedelta(hours=self.random.randint(1, 72))).strftime("%Y-%m-%d %H:%M:%S"), self.choice(["manual", "template"], [64, 36]))
                for index, review in enumerate(replied_reviews, 1)
            ],
        )

        ticket_rows = []
        tickets = []
        for index in range(1, 2201):
            order = self.random.choice(self.orders) if self.random.random() < 0.78 else None
            user = users_by_id[order["user_id"]] if order else self.random.choice(self.users)
            created_at = (order["ordered_at"] + timedelta(hours=self.random.randint(1, 240))) if order else self.random_datetime()
            status = self.choice(["open", "processing", "resolved", "closed"], [6, 9, 38, 47])
            ticket = {"ticket_id": f"TK{index:07d}", "user_id": user["user_id"], "created_at": created_at, "status": status}
            tickets.append(ticket)
            ticket_rows.append((ticket["ticket_id"], user["user_id"], order["order_id"] if order else "", self.choice(["订单咨询", "物流问题", "退款售后", "商品咨询", "账户问题", "活动优惠"], [18, 24, 21, 15, 8, 14]), self.choice(["low", "normal", "high", "urgent"], [14, 63, 19, 4]), status, created_at.strftime("%Y-%m-%d %H:%M:%S"), (created_at + timedelta(hours=self.random.randint(1, 72))).strftime("%Y-%m-%d %H:%M:%S") if status in {"resolved", "closed"} else "", self.choice(["app", "web", "phone", "social"], [46, 18, 21, 15])))
        self.write_csv(
            "service_tickets",
            ["ticket_id", "user_id", "order_id", "issue_type", "priority", "ticket_status", "created_at", "resolved_at", "source_channel"],
            ticket_rows,
        )

        def message_rows():
            for index in range(1, 6001):
                ticket = tickets[(index - 1) % len(tickets)]
                sender = self.choice(["user", "agent", "bot"], [42, 44, 14])
                yield (
                    f"TM{index:08d}", ticket["ticket_id"], sender,
                    self.choice(["咨询当前处理进度", "已收到您的问题，正在核实", "请提供相关订单信息", "问题已处理，请确认", "申请退款或补发", "感谢您的耐心等待"]),
                    (ticket["created_at"] + timedelta(minutes=self.random.randint(0, 1440))).strftime("%Y-%m-%d %H:%M:%S"),
                    self.choice(["text", "image", "system"], [88, 7, 5]),
                )
        self.write_csv(
            "ticket_messages",
            ["message_id", "ticket_id", "sender_type", "message_content", "sent_at", "message_type"],
            message_rows(),
        )

    def generate_metrics(self) -> None:
        seen: set[tuple[str, date]] = set()
        product_metric_rows = []
        while len(product_metric_rows) < 25000:
            product = self.random.choice(self.products)
            stat_date = DATA_END.date() - timedelta(days=self.random.randint(0, 364))
            key = (product["product_id"], stat_date)
            if key in seen:
                continue
            seen.add(key)
            impressions = self.random.randint(100, 25000)
            views = int(impressions * self.random.uniform(0.03, 0.35))
            visitors = int(views * self.random.uniform(0.55, 0.9))
            buyers = int(visitors * self.random.uniform(0.005, 0.12))
            paid_orders = max(0, int(buyers * self.random.uniform(0.8, 1.2)))
            gmv = round(paid_orders * product["base_price"] * self.random.uniform(0.7, 1.4), 2)
            product_metric_rows.append((f"DPM{len(product_metric_rows) + 1:09d}", stat_date.isoformat(), product["product_id"], product["store_id"], impressions, views, visitors, buyers, paid_orders, gmv, self.random.randint(0, max(1, paid_orders // 8)), self.random.randint(0, max(1, views // 12))))
        self.write_csv(
            "daily_product_metrics",
            ["metric_id", "stat_date", "product_id", "store_id", "impressions", "page_views", "unique_visitors", "buyers", "paid_orders", "gmv", "refund_orders", "favorite_additions"],
            product_metric_rows,
        )

        store_metric_rows = []
        for day_offset in range(200):
            stat_date = DATA_END.date() - timedelta(days=day_offset)
            for store in self.stores:
                visitors = self.random.randint(80, 6000)
                buyers = int(visitors * self.random.uniform(0.008, 0.11))
                orders = max(0, int(buyers * self.random.uniform(0.85, 1.2)))
                gmv = round(orders * self.random.uniform(80, 620), 2)
                refunds = round(gmv * self.random.uniform(0.01, 0.12), 2)
                store_metric_rows.append((f"DSM{len(store_metric_rows) + 1:09d}", stat_date.isoformat(), store["store_id"], visitors, buyers, orders, gmv, refunds, round(gmv - refunds, 2), round(buyers / visitors, 5) if visitors else 0, round(self.random.uniform(3.9, 4.95), 2), round(self.random.uniform(0.82, 0.995), 4)))
        self.write_csv(
            "daily_store_metrics",
            ["metric_id", "stat_date", "store_id", "visitors", "buyers", "paid_orders", "gmv", "refund_amount", "net_revenue", "conversion_rate", "service_score", "fulfillment_rate"],
            store_metric_rows,
        )

        target_rows = []
        months = []
        cursor = date(2026, 1, 1)
        while cursor <= date(2026, 8, 1):
            months.append(cursor)
            cursor = date(cursor.year + (1 if cursor.month == 12 else 0), 1 if cursor.month == 12 else cursor.month + 1, 1)
        for month in months:
            for store in self.stores:
                gmv_target = round(self.random.uniform(180000, 1800000), 2)
                target_rows.append((f"TGT{len(target_rows) + 1:07d}", month.strftime("%Y-%m"), store["store_id"], gmv_target, round(gmv_target * self.random.uniform(0.78, 0.94), 2), self.random.randint(380, 4200), round(self.random.uniform(0.025, 0.09), 4), f"负责人{int(store['store_id'][1:]) % 30 + 1:02d}"))
        self.write_csv(
            "sales_targets",
            ["target_id", "target_month", "store_id", "gmv_target", "net_revenue_target", "order_target", "conversion_rate_target", "owner_name"],
            target_rows,
        )

    def write_manifest(self) -> None:
        payload = {
            "database": "ecommerce_ops",
            "scenario": "星购商城综合零售电商运营",
            "seed": self.seed,
            "data_range": {"start": DATA_START.date().isoformat(), "end": DATA_END.date().isoformat()},
            "table_count": len(self.counts),
            "total_rows": sum(self.counts.values()),
            "tables": {
                table: {
                    "description": TABLE_DESCRIPTIONS[table],
                    "row_count": self.counts[table],
                    "columns": self.schemas[table],
                }
                for table in sorted(self.counts)
            },
        }
        (self.output_dir / "_database_manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )


if __name__ == "__main__":
    generator = EcommerceDataGenerator()
    result = generator.generate()
    print(json.dumps({"database": str(generator.output_dir), "tables": len(result), "rows": sum(result.values())}, ensure_ascii=False))
