import unittest
from unittest.mock import patch

from app.config import Settings
from app.model_client import ModelClient
from app.prompt_context import PRODUCT_CONTEXT, with_product_context


class PromptContextTest(unittest.TestCase):
    def test_product_context_identifies_jobintel(self) -> None:
        prompt = with_product_context("你负责结构化输出。")

        self.assertIn("JobIntel", prompt)
        self.assertIn("当前职责", prompt)
        self.assertEqual(prompt.count("JobIntel"), 3)

    def test_model_client_injects_product_context_at_chat_boundary(self) -> None:
        client = ModelClient(Settings(api_key="test-key"))
        response = {"choices": [{"message": {"content": "ok"}}]}

        with patch.object(client, "_post", return_value=response) as post:
            self.assertEqual(client.chat("你负责回答用户问题。", "你是谁"), "ok")

        payload = post.call_args.args[1]
        system = payload["messages"][0]["content"]
        self.assertIn(PRODUCT_CONTEXT, system)
        self.assertIn("JobIntel", system)
        self.assertIn("你负责回答用户问题", system)


if __name__ == "__main__":
    unittest.main()
