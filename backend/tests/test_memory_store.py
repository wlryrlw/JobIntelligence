import tempfile
import unittest
from pathlib import Path

from app.services.memory_store import MemoryStore


class MemoryStoreTest(unittest.TestCase):
    def test_result_memories_persist_with_column_labels(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            store = MemoryStore(path=path, limit=20)
            store.save_result(
                "task-1",
                "查询岗位薪资",
                "查询完成",
                "岗位薪资结果",
                ["city", "salary_raw"],
                [{"city": "上海", "salary_raw": "30-50K"}],
                column_labels={"city": "城市", "salary_raw": "薪资"},
            )

            reloaded = MemoryStore(path=path, limit=20).list()

            self.assertEqual(reloaded[0]["kind"], "result_table")
            self.assertEqual(reloaded[0]["column_labels"]["salary_raw"], "薪资")
            self.assertEqual(reloaded[0]["rows"][0]["salary_raw"], "30-50K")

    def test_result_memory_can_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            store = MemoryStore(path=path, limit=20)
            store.save_result("task-1", "查询", "完成", "结果", ["city"], [{"city": "上海"}])

            store.delete("result:task-1")

            self.assertEqual(store.list(), [])

    def test_result_memories_are_persistent_and_isolated_by_user(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "memories.json"
            store = MemoryStore(path=path, limit=20)
            store.save_result("task-a", "查询A", "完成", "结果A", ["city"], [{"city": "上海"}], "user-a")
            store.save_result("task-b", "查询B", "完成", "结果B", ["city"], [{"city": "杭州"}], "user-b")

            reloaded = MemoryStore(path=path, limit=20)

            self.assertEqual(len(reloaded.list("user-a")), 1)
            self.assertEqual(reloaded.list("user-a")[0]["task_id"], "task-a")
            self.assertEqual(reloaded.list("user-b")[0]["task_id"], "task-b")


if __name__ == "__main__":
    unittest.main()
