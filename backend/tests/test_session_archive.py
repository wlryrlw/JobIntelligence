from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.config import Settings
from app.models import QueryResult
from app.services.session_archive import SessionArchive
from app.services.session_context import SessionContext


class NoopModel:
    def chat(self, system: str, user: str) -> str:
        return "摘要"


class SessionArchiveTest(unittest.TestCase):
    def test_complete_turn_and_summary_survive_reload(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sessions.db"
            archive = SessionArchive(path)
            result = {
                "task_id": "task-1",
                "status": "completed",
                "route": "database_query",
                "message": "查询完成",
                "sql": "SELECT 1",
                "columns": ["value"],
                "rows": [{"value": 1}],
                "analysis": "结果为1",
            }

            archive.save_turn("task-1", "s1", "查询数据", {"schema_fields": []}, result)
            archive.save_message("s1", "user", "查询数据", task_id="task-1")
            archive.save_message(
                "s1",
                "assistant",
                "结果为1",
                task_id="task-1",
                metadata={"status": "completed"},
            )
            archive.save_summary("s1", "历史摘要", {"task-1"})

            restored = SessionArchive(path)
            turn = restored.load_turns()[0]
            summary = restored.load_summaries()[0]
            messages = restored.load_messages("s1")

            self.assertEqual(turn["result"]["rows"], [{"value": 1}])
            self.assertEqual(turn["result"]["sql"], "SELECT 1")
            self.assertEqual(summary["summary"], "历史摘要")
            self.assertEqual(summary["summarized_ids"], {"task-1"})
            self.assertEqual([item["role"] for item in messages], ["user", "assistant"])
            self.assertEqual(messages[1]["metadata"]["status"], "completed")

    def test_session_context_restores_complete_turns(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sessions.db"
            config = Settings(
                api_key="",
                session_archive_enabled=True,
                session_archive_path=str(path),
            )
            result = QueryResult(
                task_id="task-1",
                status="completed",
                route="data_qa",
                message="回答完成",
                analysis="这是完整回答",
            )

            first = SessionContext(NoopModel(), config)  # type: ignore[arg-type]
            first.remember("s1", result, "你好", {})
            restored = SessionContext(NoopModel(), config)  # type: ignore[arg-type]

            self.assertEqual(restored.tasks["task-1"]["query"], "你好")
            self.assertEqual(
                restored.tasks["task-1"]["result"].analysis,
                "这是完整回答",
            )
            self.assertEqual(restored.session_tasks["s1"], ["task-1"])

    def test_route_context_uses_only_six_recent_turns_without_summary(self) -> None:
        config = Settings(
            api_key="",
            session_archive_enabled=False,
            route_context_turns=6,
            short_term_summary_enabled=False,
        )
        context = SessionContext(NoopModel(), config)  # type: ignore[arg-type]
        for index in range(8):
            result = QueryResult(
                task_id=f"task-{index}",
                status="completed",
                route="data_qa",
                message="回答完成",
                analysis=f"回答-{index}",
            )
            context.remember("s1", result, f"问题-{index}", {})
        context.short_term_memory.restore("s1", "更早的历史摘要", {"task-0"})

        route_context = json.loads(context.route_context("s1"))
        memory_context = json.loads(context.short_term_context("s1"))

        self.assertEqual(len(route_context["recent_turns"]), 6)
        self.assertEqual(route_context["recent_turns"][0]["turn_id"], "task-2")
        self.assertNotIn("history_summary", route_context)
        self.assertEqual(memory_context["history_summary"], "更早的历史摘要")


if __name__ == "__main__":
    unittest.main()
