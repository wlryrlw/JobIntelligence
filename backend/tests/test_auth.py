import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.security import AuthService


class AuthServiceTest(unittest.TestCase):
    def test_product_metadata_and_accounts_use_jobintel_domain(self) -> None:
        self.assertEqual(app.title, "JobIntel API")
        auth = AuthService()
        admin = auth.login("admin", "admin123")
        candidate = auth.login("candidate", "candidate123")

        self.assertEqual(admin[1].user_id, "demo_admin")
        self.assertEqual(candidate[1].user_id, "demo_candidate")
        self.assertEqual(candidate[1].role, "candidate")
        self.assertNotEqual(admin[0], candidate[0])
        self.assertIsNone(auth.login("candidate", "wrong"))

    def test_api_exposes_only_jobs_market_schema_and_database_tool(self) -> None:
        with TestClient(app) as client:
            local_schema = client.get("/api/schema")
            self.assertEqual(local_schema.status_code, 200)
            self.assertEqual({table["database"] for table in local_schema.json()}, {"jobs_market"})
            self.assertEqual(
                client.get("/api/schema", headers={"Authorization": "Bearer invalid"}).status_code,
                401,
            )
            admin_token = client.post(
                "/api/auth/login",
                json={"username": "admin", "password": "admin123"},
            ).json()["access_token"]
            candidate_token = client.post(
                "/api/auth/login",
                json={"username": "candidate", "password": "candidate123"},
            ).json()["access_token"]

            schema = client.get(
                "/api/schema",
                headers={"Authorization": f"Bearer {candidate_token}"},
            ).json()
            self.assertTrue(schema)
            self.assertTrue(all(item["id"].startswith("jobs_market.") for item in schema))
            self.assertEqual({item["database"] for item in schema}, {"jobs_market"})

            tools = client.get(
                "/api/mcp/tools",
                headers={"Authorization": f"Bearer {admin_token}"},
            ).json()
            tool_names = {item["name"] for item in tools}
            self.assertEqual(
                {name for name in tool_names if name.startswith("query_")},
                {"query_jobs_market"},
            )
            self.assertNotIn("current_datetime", tool_names)
            self.assertNotIn("resolve_date_range", tool_names)
            self.assertNotIn("build_bar_chart", tool_names)


if __name__ == "__main__":
    unittest.main()
