from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.config import settings
from app.model_client import ModelClient
from app.querying.data_qa_agent import DataQaAgent
from app.skills import SkillRegistry


def main() -> None:
    if not settings.api_key:
        raise RuntimeError("LLM_API_KEY尚未配置")

    model = ModelClient(settings)
    agent = DataQaAgent(
        model,
        SkillRegistry().get("data_qa"),
    )
    source = {
        "task_id": "report-check-source",
        "title": "各地区销售额",
        "query": "查询本月各地区销售额",
        "columns": ["销售地区", "销售额"],
        "rows": [
            {"销售地区": "华东", "销售额": 3513112.52},
            {"销售地区": "华南", "销售额": 3379043.36},
            {"销售地区": "西南", "销售额": 4474287.06},
            {"销售地区": "华北", "销售额": 4292183.75},
        ],
    }
    result = agent.run(
        "请基于这张表生成一份详细的数据分析报告，并使用合适的图表展示地区差异",
        {
            "short_term": "",
            "recent_result": "",
            "selected_tables": json.dumps([source], ensure_ascii=False),
        },
    )
    if not result.report or not result.report.visualizations:
        raise RuntimeError("模型没有按报告需求选择展示工具")
    print(json.dumps({
        "action": result.action,
        "title": result.report.title,
        "tools": [call["tool"] for call in result.tool_calls],
        "visualizations": [item.model_dump() for item in result.report.visualizations],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
