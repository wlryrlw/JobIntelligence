"""手动运行：使用当前 .env 完成一次端到端查询。"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import BASE_DIR
from app.demo_data import seed_demo_data
from app.service import AskDataService


if __name__ == "__main__":
    seed_demo_data(database_dir=BASE_DIR / "data" / "databases" / "askdata_mock")
    service = AskDataService()
    result = service.submit("查询本月各地区销售额", "smoke-session")
    print(
        {
            "status": result.status,
            "route": result.route,
            "coder_calls": len(result.tool_calls),
            "sql_executed": bool(result.sql),
            "row_count": len(result.rows),
            "schema_threshold": (result.retrieval or {}).get("threshold"),
            "embedding_source": (result.retrieval or {}).get("embedding_source"),
            "rerank_source": (result.retrieval or {}).get("rerank_source"),
        }
    )
