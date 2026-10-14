import unittest

from app.config import Settings
from app.service import JobIntelService


class ModelStub:
    enabled = False

    def chat_json(self, _system: str, _user: str) -> dict[str, object]:
        raise AssertionError("该测试不应调用模型")


class JobIntelServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        model = ModelStub()
        self.service = JobIntelService(model, config=Settings())

    def test_service_and_visible_schema_are_jobintel_job_domain(self) -> None:
        schema = self.service.visible_schema("demo_candidate")

        self.assertTrue(schema)
        self.assertEqual({table["database"] for table in schema}, {"jobs_market"})
        self.assertTrue(all(table["id"].startswith("jobs_market.") for table in schema))

    def test_mcp_boundary_exposes_only_database_query_tool(self) -> None:
        tools = self.service.list_mcp_tools("demo_candidate")
        names = {item["name"] for item in tools}

        self.assertEqual(names, {"query_jobs_market"})
        self.assertNotIn("current_datetime", names)
        self.assertNotIn("resolve_date_range", names)
        self.assertNotIn("build_bar_chart", names)


if __name__ == "__main__":
    unittest.main()
