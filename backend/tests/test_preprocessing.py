import unittest

from app.preprocessing import RequestPreprocessor


class CountingModel:
    def __init__(self, action: str) -> None:
        self.action = action
        self.calls = 0

    def chat_json(self, system: str, user: str) -> dict:
        self.calls += 1
        if self.action == "data_qa":
            return {
                "action": "data_qa", "confidence": 0.9, "reason": "分析已有结果",
                # 验证问答路由会清空 Schema 检索参数。
                "standalone_query": "不应保留",
                "retrieval": {"retrieval_terms": ["岗位", "城市"]},
            }
        if self.action == "direct_response":
            if "你是什么大模型" in user:
                return {
                    "action": "direct_response",
                    "confidence": 0.95,
                    "reason": "产品身份问题由正常直接回复路由处理",
                    "response": (
                        "我是 OfferAgent，一个面向求职场景的 AI 求职决策系统。"
                        "底层大模型由当前部署配置决定。"
                    ),
                    "response_type": "answer",
                }
            return {
                "action": "direct_response",
                "confidence": 0.72,
                "reason": "无法确定用户希望查新数据还是分析已有结果",
                "response": "你希望查询新的岗位数据，还是分析已有的岗位结果？",
                "response_type": "clarification",
                "standalone_query": "不应保留",
                "retrieval": {"retrieval_terms": ["岗位", "城市"]},
            }
        return {
            "action": "database_query", "confidence": 0.9, "reason": "需要新数据",
            "standalone_query": "查询上海 Python 岗位", "rewritten": True,
            "retrieval": {
                "retrieval_terms": ["城市", "Python", "岗位"],
                "metrics": ["岗位数量"], "dimensions": ["城市"],
                "filters": [], "time_expressions": ["本月"], "operations": ["汇总"],
            },
        }


class FailingModel:
    def chat_json(self, system: str, user: str) -> dict:
        raise RuntimeError("测试模型不可用")


class RequestPreprocessorTest(unittest.TestCase):
    def test_database_request_finishes_three_tasks_in_one_model_call(self) -> None:
        model = CountingModel("database_query")
        result = RequestPreprocessor(model).prepare("北京呢", "上一轮查询上海 Python 岗位")
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.action, "database_query")
        self.assertEqual(result.standalone_query, "查询上海 Python 岗位")
        self.assertEqual(result.retrieval.retrieval_terms, ["城市", "Python", "岗位"])

    def test_qa_clears_query_and_retrieval_output(self) -> None:
        model = CountingModel("data_qa")
        result = RequestPreprocessor(model).prepare("解释刚才的结果", "有上一轮结果")
        self.assertEqual(model.calls, 1)
        self.assertEqual(result.action, "data_qa")
        self.assertEqual(result.standalone_query, "")
        self.assertEqual(result.retrieval.retrieval_terms, [])

    def test_model_failure_uses_explicit_conservative_fallback(self) -> None:
        result = RequestPreprocessor(FailingModel()).prepare("查询上海 Python 岗位", "")

        self.assertEqual(result.action, "direct_response")
        self.assertEqual(result.confidence, 0.0)
        self.assertEqual(result.source, "model_unavailable_fallback")
        self.assertIn("暂时不可用", result.response)
        self.assertIn("测试模型不可用", result.reason)

    def test_product_identity_is_answered_without_exposing_internal_module(self) -> None:
        model = CountingModel("direct_response")

        result = RequestPreprocessor(model).prepare("你是什么大模型", "")

        self.assertEqual(model.calls, 1)
        self.assertEqual(result.action, "direct_response")
        self.assertEqual(result.source, "model")
        self.assertIn("OfferAgent", result.response)
        self.assertIn("底层大模型", result.response)
        self.assertNotIn("预处理器", result.response)

    def test_gray_zone_is_clarified_by_preprocessor_without_retrieval(self) -> None:
        model = CountingModel("direct_response")

        result = RequestPreprocessor(model).prepare("看看岗位情况", "有一张岗位结果表")

        self.assertEqual(model.calls, 1)
        self.assertEqual(result.action, "direct_response")
        self.assertEqual(result.response_type, "clarification")
        self.assertIn("查询新的岗位数据", result.response)
        self.assertEqual(result.standalone_query, "")
        self.assertEqual(result.retrieval.retrieval_terms, [])

    def test_market_question_without_result_context_cannot_stay_in_data_qa(self) -> None:
        model = CountingModel("data_qa")

        result = RequestPreprocessor(model).prepare(
            "上海 AI Agent 岗位最常见的技能有哪些？",
            "",
            has_data_context=False,
        )

        self.assertEqual(result.action, "database_query")
        self.assertEqual(result.standalone_query, "上海 AI Agent 岗位最常见的技能有哪些？")


if __name__ == "__main__":
    unittest.main()
