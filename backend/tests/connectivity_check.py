"""手动运行：验证三个模型接口，不输出 API Key。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.model_client import ModelClient


if __name__ == "__main__":
    client = ModelClient()
    status = {}
    checks = {
        "chat": lambda: bool(client.chat_json("只返回 JSON。", '返回 {"ok": true}').get("ok")),
        "embedding": lambda: len(client.embed(["销售额字段"])[0]),
        "rerank": lambda: len(client.rerank("销售额", ["实付金额字段", "客户名称字段"], 2)),
    }
    for name, check in checks.items():
        try:
            status[name] = {"ok": True, "result": check()}
        except RuntimeError as exc:
            message = str(exc)
            status[name] = {
                "ok": False,
                "error": "AccessDenied" if "AccessDenied" in message else message[:160],
            }
    print(status)
