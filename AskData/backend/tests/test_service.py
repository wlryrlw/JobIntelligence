import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.models import QueryRequest
from app.retrieval import SchemaIndex
from app.service import AskDataService


class FakeModelClient:
    enabled = True

    def __init__(self) -> None:
        self.chat_calls = 0
        self.embed_calls: list[list[str]] = []
        self.rerank_queries: list[str] = []

    def chat_json(self, system: str, user: str) -> dict:
        if "问数系统的请求预处理器" in system:
            query = user.split("当前问题：", 1)[-1].split("\n近期轻量上下文：", 1)[0]
            if "你好" in query:
                return {
                    "action": "direct_response", "confidence": 0.99,
                    "reason": "普通问候无需调用下游模型",
                    "response": "你好，有什么想了解的数据或问题吗？",
                    "response_type": "answer",
                }
            if "看看销售情况" in query:
                return {
                    "action": "direct_response", "confidence": 0.72,
                    "reason": "无法确定是查询新数据还是分析已有结果",
                    "response": "你希望查询新的销售数据，还是分析已有的销售结果？",
                    "response_type": "clarification",
                }
            is_qa = any(term in query for term in ("分析刚才", "基于这个表格", "分析报告"))
            if is_qa:
                return {
                    "action": "data_qa", "confidence": 0.95, "reason": "测试问答路由",
                    "standalone_query": "", "rewritten": False,
                    "retrieval": {
                        "retrieval_terms": [], "metrics": [],
                        "dimensions": [], "filters": [], "time_expressions": [], "operations": [],
                    },
                }
            terms = [term for term in ("客户等级", "销售额", "销售目标", "地区") if term in query]
            return {
                "action": "database_query", "confidence": 0.95, "reason": "测试问数路由",
                "standalone_query": query, "rewritten": False,
                "retrieval": {
                    "retrieval_terms": [*terms, *[term for term in ("今天", "本月", "历史") if term in query]],
                    "metrics": [term for term in terms if term in {"销售额", "销售目标"}],
                    "dimensions": [term for term in terms if term in {"客户等级", "地区"}],
                    "filters": [],
                    "time_expressions": [term for term in ("今天", "本月", "历史") if term in query],
                    "operations": ["对比"] if "对比" in query else [],
                },
            }
        if system.startswith("你是数据问答智能体"):
            state = json.loads(user)
            query = state["query"]
            sources = state["available_data_sources"]
            if "分析报告" in query:
                source = sources[0]
                return {
                    "action": "report",
                    "answer": "已生成销售数据分析报告。",
                    "title": "地区销售数据分析报告",
                    "markdown": (
                        "# 地区销售数据分析报告\n\n"
                        "## 关键发现\n\n"
                        "- 华东地区销售额在当前结果中最高。\n\n"
                        "## 建议\n\n"
                        "继续跟踪各地区销售变化。"
                    ),
                    "tool_calls": [{
                        "name": "build_bar_chart",
                        "arguments": {
                            "title": "各地区销售额对比",
                            "source_task_id": source["task_id"],
                            "category_field": "销售地区",
                            "value_field": "销售额",
                            "max_items": 8,
                        },
                    }],
                }
            return {
                "action": "answer",
                "answer": "华东销售额最高，回答复用了最近一次查询结果。",
                "tool_calls": [],
            }
        if system.startswith("你是单数据库问数智能体") and "上一次DuckDB" not in system:
            state = json.loads(user)
            query = state["query"]
            graph_text = json.dumps(state["schema_graph"], ensure_ascii=False)
            confirmed_tables = set(state.get("confirmed_parameters", {}).values())
            tool_results = state.get("tool_results", [])

            if "今天" in query and not any(
                item.get("tool") == "current_datetime" for item in tool_results
            ):
                return {
                    "action": "call_tool",
                    "tool_name": "current_datetime",
                    "arguments": {"timezone_name": "Asia/Shanghai"},
                    "reason": "先获取今天的明确日期",
                }
            if "歧义查询" in query and not confirmed_tables:
                return {
                    "action": "clarify",
                    "reason": "模型判断时间范围会改变结果",
                    "clarification": {
                        "parameter": "time_range",
                        "question": "查询当前订单还是历史订单？",
                        "reason": "两个范围会产生不同结果",
                        "options": [
                            {"id": "orders_current", "label": "本月当前订单", "description": "当前CSV数据", "recommended": True},
                            {"id": "orders_history", "label": "历史订单", "description": "历史CSV数据", "recommended": False},
                        ],
                    },
                }
            if "sales_targets" in graph_text and "销售目标" in query:
                sql = "WITH actual AS (SELECT region, SUM(paid_amount) AS sales FROM orders_current WHERE status = '已支付' GROUP BY region), target AS (SELECT region, target_amount FROM sales_targets WHERE target_month = '2026-08') SELECT a.region AS '销售地区', ROUND(a.sales, 2) AS '销售额', ROUND(t.target_amount, 2) AS '销售目标', ROUND(a.sales / NULLIF(t.target_amount, 0), 4) AS '目标完成率' FROM actual a JOIN target t ON a.region = t.region ORDER BY 4 DESC"
            elif "客户等级" in query:
                sql = "SELECT c.customer_level AS '客户等级', ROUND(SUM(o.paid_amount), 2) AS '销售额' FROM orders_current o JOIN customers c ON o.customer_id = c.customer_id WHERE o.status = '已支付' GROUP BY c.customer_level ORDER BY 2 DESC"
            else:
                table = "orders_history" if "orders_history" in graph_text and "orders_current" not in graph_text else "orders_current"
                sql = f"SELECT region AS '销售地区', ROUND(SUM(paid_amount), 2) AS '销售额' FROM {table} WHERE status = '已支付' GROUP BY region ORDER BY 2 DESC"
            return {
                "action": "call_tool",
                "tool_name": "query_askdata_mock",
                "arguments": {"sql": sql},
                "reason": "通过MCP数据库工具执行查询",
            }
        if "查询结果整理器" in system:
            return {"valid": True, "reason": "结果符合问题", "title": "销售查询结果", "analysis": "查询结果已经按要求汇总。"}
        if "一级意图路由器" in system:
            # 模拟表格追问的路由结果。
            action = "data_qa" if any(
                term in user for term in ("分析刚才", "基于这个表格", "你好")
            ) else "database_query"
            return {
                "action": action,
                "confidence": 0.95,
                "reason": "测试一级路由",
                "keywords": ["客户等级", "销售额"] if "客户等级" in user else ["销售地区", "销售额", "销售目标"],
            }
        if "独立查询" in system and "改写" in system:
            query = user.split("当前问题：", 1)[-1]
            return {"standalone_query": query, "rewritten": False}
        if "Schema检索意图提取器" in system:
            query = user.split("独立查询：", 1)[-1]
            terms = [term for term in ("客户等级", "销售额", "销售目标", "地区") if term in query]
            return {
                "retrieval_terms": [*terms, *[term for term in ("今天", "本月", "历史") if term in query]],
                "metrics": [term for term in terms if term in {"销售额", "销售目标"}],
                "dimensions": [term for term in terms if term in {"客户等级", "地区"}],
                "filters": [],
                "time_expressions": [term for term in ("今天", "本月", "历史") if term in query],
                "operations": ["对比"] if "对比" in query else [],
            }
        if system.startswith("你是被问数Agent调用的 SQL Coder 工具"):
            tool_line = user.splitlines()[1]
            if "sales_targets" in tool_line and "orders_current" in tool_line:
                return {
                    "sql": "WITH actual AS (SELECT region, SUM(paid_amount) AS sales FROM orders_current WHERE status = '已支付' GROUP BY region), target AS (SELECT region, target_amount FROM sales_targets WHERE target_month = '2026-08') SELECT a.region AS '销售地区', ROUND(a.sales, 2) AS '销售额', ROUND(t.target_amount, 2) AS '销售目标', ROUND(a.sales / NULLIF(t.target_amount, 0), 4) AS '目标完成率' FROM actual a JOIN target t ON a.region = t.region ORDER BY 4 DESC"
                }
            if "sales_targets" in tool_line:
                return {"sql": "SELECT region AS '销售地区', target_amount AS '销售目标' FROM sales_targets WHERE target_month = '2026-08' ORDER BY 2 DESC"}
            if "客户等级" in user.splitlines()[0]:
                return {
                    "sql": "SELECT c.customer_level AS '客户等级', ROUND(SUM(o.paid_amount), 2) AS '销售额' FROM orders_current o JOIN customers c ON o.customer_id = c.customer_id WHERE o.status = '已支付' GROUP BY c.customer_level ORDER BY 2 DESC"
                }
            table = "orders_history" if "orders_history" in tool_line else "orders_current"
            return {
                "sql": f"SELECT region AS '销售地区', ROUND(SUM(paid_amount), 2) AS '销售额' FROM {table} WHERE status = '已支付' GROUP BY region ORDER BY 2 DESC"
            }
        if system.startswith("你是 Text-to-SQL 查询控制器"):
            state = json.loads(user)
            query = state["query"]
            executions = state["executions"]
            confirmed = state["user_confirmed"].get("confirmed_schema_tables", [])
            confirmed_parameters = state["user_confirmed"].get("confirmed_parameters", {})
            if "执行后澄清" in query and executions and "metric" not in confirmed_parameters:
                return {
                    "action": "clarify",
                    "reason": "需要确认金额口径",
                    "tool_args": None,
                    "clarification": {
                        "parameter": "metric",
                        "question": "销售额按哪个口径计算？",
                        "reason": "支付金额和订单原价会产生不同结果",
                        "options": [
                            {"id": "paid_amount", "label": "实付金额", "description": "按用户实际支付金额", "recommended": True},
                            {"id": "order_amount", "label": "订单原价", "description": "按优惠前订单金额", "recommended": False},
                        ],
                    },
                }
            if "歧义查询" in query and not confirmed:
                return {
                    "action": "clarify",
                    "reason": "当前与历史订单范围不明确",
                    "tool_args": None,
                    "clarification": {
                        "question": "查询当前还是历史订单？",
                        "reason": "时间范围会改变结果",
                        "options": [
                            {"id": "orders_current", "label": "当前订单", "description": "2026年8月", "recommended": True},
                            {"id": "orders_history", "label": "历史订单", "description": "2026年6月至7月", "recommended": False},
                        ],
                    },
                }
            if not state["schema_hits"]:
                return {
                    "action": "clarify",
                    "reason": "没有高置信度 Schema",
                    "tool_args": None,
                    "clarification": {
                        "question": "请选择数据范围",
                        "reason": "字段未达到阈值",
                        "options": [
                            {"id": "orders_current", "label": "当前订单", "description": "订单数据", "recommended": True},
                            {"id": "customers", "label": "客户信息", "description": "客户数据", "recommended": False},
                        ],
                    },
                }
            if executions:
                used_targets = any("sales_targets" in item.get("objects", []) for item in executions)
                if "销售目标" not in query or used_targets:
                    return {"action": "finish", "reason": "结果足够", "tool_args": None, "clarification": None}
                return {
                    "action": "call_coder",
                    "reason": "补充目标数据",
                    "tool_args": {
                        "database": "askdata_mock",
                        "processing_objects": ["sales_targets"],
                        "operation_instruction": "查询本月各地区销售目标",
                        "output_target": ["销售地区", "销售目标"],
                    },
                    "clarification": None,
                }
            objects = ["orders_current", "customers"] if "客户等级" in query else [confirmed[0] if confirmed else "orders_current"]
            return {
                "action": "call_coder",
                "reason": "执行一次查询",
                "tool_args": {
                    "database": "askdata_mock",
                    "processing_objects": objects,
                    "operation_instruction": query,
                    "output_target": ["查询结果"],
                },
                "clarification": None,
            }
        if "最终结果整理与校验器" in system:
            return {"valid": True, "reason": "结果符合问题", "title": "销售查询结果", "analysis": "查询结果已经按要求汇总。"}
        return {"sql": "SELECT 1 AS value"}

    def chat(self, system: str, user: str, **_: object) -> str:
        self.chat_calls += 1
        return "华东销售额最高，回答复用了最近一次查询结果。"

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        vectors = []
        for text in texts:
            vector = [0.0] * 32
            digest = hashlib.sha256(text.encode("utf-8")).digest()
            vector[int.from_bytes(digest[:2], "big") % 32] = 1.0
            vectors.append(vector)
        return vectors

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        self.rerank_queries.append(query)
        terms = [term for term in ("销售额", "销售目标", "客户等级", "地区", "产品名称") if term in query]
        return [
            (index, 0.96 if any(term in document for term in terms) else max(0.05, 0.5 - index * 0.01))
            for index, document in enumerate(documents[:top_n])
        ]


class SqlGenerationFailureClient(FakeModelClient):
    def chat_json(self, system: str, user: str) -> dict:
        if system.startswith("你是单数据库问数智能体"):
            raise RuntimeError("SQL生成模型测试失败")
        return super().chat_json(system, user)


class PreprocessingFailureClient(FakeModelClient):
    def chat_json(self, system: str, user: str) -> dict:
        if system.startswith("你是问数系统的请求预处理器"):
            raise RuntimeError("预处理模型连接超时")
        return super().chat_json(system, user)


class InvalidSqlClient(FakeModelClient):
    def chat_json(self, system: str, user: str) -> dict:
        if system.startswith("你是单数据库问数智能体"):
            return {
                "action": "call_tool",
                "tool_name": "query_askdata_mock",
                "arguments": {"sql": "SELECT missing_column FROM orders_current"},
                "reason": "测试错误SQL",
            }
        return super().chat_json(system, user)


class AskDataServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database_root = Path(self.temp_dir.name) / "databases"
        self.database_patch = patch(
            "app.querying.duckdb_engine.DATABASE_ROOT", self.database_root
        )
        self.database_patch.start()
        from app.demo_data import seed_demo_data

        seed_demo_data(database_dir=self.database_root / "askdata_mock")
        config = Settings(
            api_key="",
            embedding_dimensions=32,
            schema_recall_threshold=0.55,
            session_archive_enabled=False,
        )
        client = FakeModelClient()
        index = SchemaIndex(client, config, Path(self.temp_dir.name) / "schema_index.json")
        self.service = AskDataService(client, index)  # type: ignore[arg-type]
        self.client = client

    def tearDown(self) -> None:
        self.database_patch.stop()
        self.temp_dir.cleanup()

    def test_schema_ambiguity_requests_clarification_after_retrieval(self) -> None:
        result = self.service.submit("歧义查询各地区销售额", "s1")
        self.assertEqual(result.status, "waiting_clarification")
        self.assertTrue(result.retrieval["hits"])
        self.assertEqual(result.clarification.options[0].id, "orders_current")

    def test_clarification_resumes_same_task(self) -> None:
        waiting = self.service.submit("歧义查询各地区销售额", "s1")
        result = self.service.clarify(waiting.task_id, "orders_current")
        self.assertEqual(result.status, "completed")
        self.assertTrue(result.rows)
        self.assertIn("GROUP BY region", result.sql)

    def test_multi_database_hits_request_database_choice(self) -> None:
        waiting = self.service.submit("按客户等级统计本月销售额", "multi-db", None, "demo_admin")

        self.assertEqual(waiting.status, "waiting_clarification")
        self.assertEqual(waiting.clarification.parameter, "database")
        self.assertEqual(
            {option.id for option in waiting.clarification.options},
            {"askdata_mock", "ecommerce_ops"},
        )

    def test_database_clarification_resumes_in_chosen_database(self) -> None:
        waiting = self.service.submit("按客户等级统计本月销售额", "multi-db-2", None, "demo_admin")

        result = self.service.clarify(waiting.task_id, "askdata_mock", user_id="demo_admin")

        self.assertEqual(result.status, "completed")
        self.assertEqual(
            {table["database"] for table in (result.schema_graph or {}).get("tables", [])},
            {"askdata_mock"},
        )
        self.assertIn("JOIN customers", result.sql)
        self.assertTrue(result.rows)

    def test_natural_language_clarification_resumes_original_task(self) -> None:
        waiting = self.service.submit("歧义查询各地区销售额", "natural-session")

        result = self.service.submit("就用本月当前订单", "natural-session")

        self.assertEqual(waiting.status, "waiting_clarification")
        self.assertEqual(result.task_id, waiting.task_id)
        self.assertEqual(result.status, "completed")
        self.assertIn("orders_current", result.sql)

    def test_dragged_result_table_analysis_never_enters_schema_clarification(self) -> None:
        source = self.service.submit("查询本月各地区销售额", "table-qa")
        workspace = {"analysis_table_ids": [source.task_id]}

        result = self.service.submit("基于这个表格帮我分析一下", "table-qa", workspace)

        self.assertEqual(result.route, "data_qa")
        self.assertEqual(result.status, "completed")
        self.assertIsNone(result.clarification)
        self.assertEqual(result.analysis_sources[0]["task_id"], source.task_id)

    def test_confirmed_field_defaults_to_auto_without_user_aggregation(self) -> None:
        payload = QueryRequest.model_validate({
            "query": "查询本月销售额",
            "workspace": {"schema_fields": [{"name": "paid_amount", "tableId": "orders_current"}]},
        })

        self.assertEqual(payload.workspace.schema_fields[0].aggregation, "auto")

    def test_relative_date_calls_datetime_tool_before_sql(self) -> None:
        result = self.service.submit(
            "查询今天各地区销售额",
            "s1",
            {"confirmed_schema_tables": ["orders_current"]},
        )
        tool_result = next(item for item in result.execution_log if item["stage"] == "mcp_tool_call")
        self.assertEqual(tool_result["tool"], "current_datetime")
        self.assertIn("date", tool_result["result"])
        self.assertEqual(len(result.tool_calls), 1)

    def test_single_database_agent_executes_one_query(self) -> None:
        result = self.service.submit("查询本月各地区销售额", "s1")
        self.assertEqual(result.columns, ["销售地区", "销售额"])
        self.assertEqual(len(result.tool_calls), 1)
        self.assertTrue(result.rows)

    def test_join_query_is_still_one_database_call(self) -> None:
        result = self.service.submit(
            "按客户等级统计本月销售额",
            "s1",
            {
                "schema_fields": [
                    {"name": "customer_level", "aggregation": "group", "tableId": "customers"}
                ]
            },
        )
        self.assertEqual(len(result.tool_calls), 1)
        self.assertIn("JOIN customers", result.sql)

    def test_actual_vs_target_uses_single_database_agent(self) -> None:
        result = self.service.submit("对比本月各地区销售额和销售目标", "s1")
        self.assertEqual(len(result.tool_calls), 1)
        arguments = result.tool_calls[0]["arguments"]
        self.assertEqual(arguments["mode"], "single_database_agent")
        self.assertNotIn("instruction", arguments)
        self.assertEqual(result.workflow_mode, "single_database_agent")
        self.assertIn("销售目标", result.columns)
        self.assertIn("目标完成率", result.columns)
        self.assertTrue(result.rows)

    def test_schema_graph_does_not_expand_unrelated_table_for_single_hit(self) -> None:
        retrieval = self.service.search_schema("客户等级", threshold=0.9)
        graph = retrieval["schema_graph"]
        table_ids = {table["id"] for table in graph["tables"]}
        self.assertIn("customers", table_ids)
        self.assertNotIn("sales_targets", table_ids)

    def test_follow_up_uses_data_qa_without_new_sql(self) -> None:
        self.service.submit("查询本月各地区销售额", "s1")
        result = self.service.submit("分析刚才的结果", "s1")
        self.assertEqual(result.route, "data_qa")
        self.assertIsNone(result.sql)
        self.assertEqual(result.rows, [])
        self.assertIn("复用", result.analysis)

    def test_explicit_report_request_calls_chart_tool(self) -> None:
        source = self.service.submit("查询本月各地区销售额", "report-session")

        result = self.service.submit("基于刚才的数据生成详细分析报告", "report-session")

        self.assertEqual(result.route, "data_qa")
        self.assertEqual(result.workflow_mode, "qa_report")
        self.assertIsNotNone(result.report)
        self.assertIn("# 地区销售数据分析报告", result.report.markdown)
        self.assertEqual(result.report.visualizations[0].type, "bar")
        self.assertEqual(result.report.visualizations[0].source_task_id, source.task_id)
        self.assertEqual(result.report_tool_calls[0]["tool"], "build_bar_chart")
        self.assertIsNone(result.sql)

    def test_greeting_after_query_does_not_repeat_previous_table(self) -> None:
        self.service.submit("查询本月各地区销售额", "greeting-session")
        result = self.service.submit("你好", "greeting-session")
        self.assertEqual(result.route, "direct_response")
        self.assertEqual(result.rows, [])
        self.assertEqual(result.columns, [])
        self.assertIn("你好", result.analysis)
        self.assertEqual(self.client.chat_calls, 0)

    def test_gray_zone_uses_plain_language_clarification_without_interrupt(self) -> None:
        result = self.service.submit("看看销售情况", "gray-zone-session")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.route, "direct_response")
        self.assertEqual(result.workflow_mode, "direct_response")
        self.assertIsNone(result.clarification)
        self.assertIn("查询新的销售数据", result.analysis)
        self.assertIsNone(result.sql)
        self.assertEqual(self.client.chat_calls, 0)

    def test_dragged_field_is_forced_into_schema_graph(self) -> None:
        result = self.service.submit(
            "查询本月各地区销售额",
            "s1",
            {
                "schema_fields": [
                    {"name": "customer_level", "aggregation": "group", "tableId": "customers"}
                ]
            },
        )
        hit = next(item for item in result.retrieval["hits"] if item["doc_id"] == "customers.customer_level")
        self.assertEqual(hit["score"], 1.0)
        self.assertEqual(hit["source"], "user_confirmed")

    def test_selected_result_table_is_used_only_as_analysis_context(self) -> None:
        source = self.service.submit("查询本月各地区销售额", "s1")
        workspace = {"analysis_table_ids": [source.task_id]}

        result = self.service.submit("分析刚才的结果", "s1", workspace)

        self.assertEqual(result.route, "data_qa")
        self.assertEqual(result.analysis_sources[0]["task_id"], source.task_id)
        query_workspace = self.service._query_workspace(self.service._normalize_workspace(workspace))
        self.assertNotIn("analysis_table_ids", query_workspace)

    def test_frontend_supplied_history_table_survives_backend_task_loss(self) -> None:
        workspace = {
            "analysis_table_ids": ["old-task"],
            "analysis_tables": [
                {
                    "task_id": "old-task",
                    "title": "历史地区销售额",
                    "query": "查询历史销售额",
                    "columns": ["地区", "销售额"],
                    "rows": [{"地区": "华东", "销售额": 120000}],
                }
            ],
        }

        result = self.service.submit("分析刚才的结果", "fresh-session", workspace)

        self.assertEqual(result.route, "data_qa")
        self.assertEqual(result.analysis_sources[0]["title"], "历史地区销售额")

    def test_route_receives_selected_table_header_without_rows(self) -> None:
        workspace = {
            "analysis_table_ids": ["old-task"],
            "analysis_tables": [{
                "task_id": "old-task",
                "title": "历史地区销售额",
                "query": "查询历史销售额",
                "columns": ["地区", "销售额"],
                "rows": [{"地区": "华东", "销售额": 120000}],
            }],
        }

        payload = self.service._payload("task", "分析这个表格", "fresh-session", workspace)
        route_context = json.loads(payload["route_context"])
        header = route_context["selected_table_headers"][0]

        self.assertEqual(header["columns"], ["地区", "销售额"])
        self.assertEqual(header["row_count"], 1)
        self.assertNotIn("rows", header)

    def test_threshold_retrieval_returns_only_confident_fields(self) -> None:
        result = self.service.search_schema("按客户等级统计销售额", threshold=0.55)
        self.assertTrue(result["hits"])
        self.assertTrue(all(hit["score"] >= 0.55 for hit in result["hits"]))
        self.assertTrue(all("rerank_text" in hit for hit in result["hits"]))

    def test_embedding_uses_extracted_terms_while_rerank_uses_full_query(self) -> None:
        query = "请结合前面的讨论，帮我仔细查询本月各地区销售额并说明结果"

        self.service.search_schema(query, threshold=0.55)

        self.assertEqual(self.client.embed_calls[-1], ["销售额 地区 本月"])
        self.assertEqual(self.client.rerank_queries[-1], query)

    def test_unknown_schema_returns_no_hits(self) -> None:
        result = self.service.search_schema("库存周转天数", threshold=0.95)
        self.assertEqual(result["hits"], [])

    def test_schema_retrieval_excludes_unauthorized_table(self) -> None:
        result = self.service.search_schema(
            "查询历史订单销售额",
            threshold=0.0,
            user_id="demo_current_sales",
        )

        self.assertTrue(result["hits"])
        self.assertNotIn(
            "orders_history",
            {hit["table_id"] for hit in result["hits"]},
        )

    def test_query_task_cannot_be_read_by_another_user(self) -> None:
        result = self.service.submit(
            "查询本月各地区销售额",
            "private-session",
            user_id="demo_analyst",
        )

        with self.assertRaises(PermissionError):
            self.service.get_task(result.task_id, "demo_current_sales")

    def test_sql_model_failure_is_returned_without_template_fallback(self) -> None:
        client = SqlGenerationFailureClient()
        index = SchemaIndex(client, self.service.config, Path(self.temp_dir.name) / "failure_index.json")
        service = AskDataService(client, index)  # type: ignore[arg-type]

        result = service.submit("查询本月各地区销售额", "failure-session")

        self.assertEqual(result.status, "failed")
        self.assertEqual(result.execution_log[0]["stage"], "single_database_agent")
        self.assertIn("SQL生成模型测试失败", result.analysis)
        self.assertIsNone(result.sql)

    def test_preprocessing_failure_uses_logged_conservative_fallback(self) -> None:
        client = PreprocessingFailureClient()
        index = SchemaIndex(
            client,
            self.service.config,
            Path(self.temp_dir.name) / "route_fallback_index.json",
        )
        service = AskDataService(client, index)  # type: ignore[arg-type]

        result = service.submit("查询本月各地区销售额", "route-fallback-session")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.route, "direct_response")
        self.assertEqual(result.workflow_mode, "route_fallback")
        self.assertIsNone(result.sql)
        self.assertEqual(result.rows, [])
        self.assertEqual(result.execution_log[0]["stage"], "route_fallback")
        self.assertEqual(result.execution_log[0]["source"], "model_unavailable_fallback")
        self.assertIn("暂时不可用", result.analysis)

    def test_invalid_sql_stops_after_original_execution_error(self) -> None:
        client = InvalidSqlClient()
        index = SchemaIndex(client, self.service.config, Path(self.temp_dir.name) / "invalid_sql_index.json")
        service = AskDataService(client, index)  # type: ignore[arg-type]

        result = service.submit("查询本月各地区销售额", "invalid-sql-session")

        self.assertEqual(result.status, "failed")
        self.assertIn("missing_column", result.sql)
        stages = [item["stage"] for item in result.execution_log]
        self.assertEqual(stages[-1], "execute_duckdb")
        self.assertNotIn("repair_and_retry", stages)
        self.assertIn("missing_column", result.analysis)


if __name__ == "__main__":
    unittest.main()
