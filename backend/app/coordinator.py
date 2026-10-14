
from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from .contracts import TargetJobRequest, UserRequest


class DatasetAvailability(BaseModel):
    available: bool
    reason: str
    dataset_version: str | None = None
    run_id: str | None = None
    age_days: float | None = Field(default=None, ge=0)
    keyword: str | None = None
    city: str | None = None


class CoordinatorClarification(BaseModel):
    parameter: Literal["keyword", "city", "resume_id", "target"] | str
    question: str
    reason: str
    options: list[dict[str, str]] = Field(default_factory=list)


class RouteDecision(BaseModel):
    action: Literal[
        "direct_response",
        "data_qa",
        "database_query",
        "career_advice",
        "request_crawl",
        "clarify",
    ]
    target: TargetJobRequest | None = None
    clarification: CoordinatorClarification | None = None
    availability: DatasetAvailability | None = None
    career_mode: str | None = None
    reason: str = Field(min_length=1, max_length=1000)


class Coordinator:
    """只做路由、资源/新鲜度检查和澄清，不执行 scraper 或 SQL。"""

    CITY_NAMES = (
        "北京", "上海", "广州", "深圳", "杭州", "南京", "苏州", "成都",
        "武汉", "西安", "重庆", "天津", "厦门", "长沙", "合肥", "宁波",
    )
    DEFAULT_CITY_OPTIONS = [{"id": city, "label": city, "description": f"查询{city}岗位市场"} for city in CITY_NAMES]

    def __init__(
        self,
        dataset_root: Path | None = None,
        *,
        freshness_days: int = 7,
    ) -> None:
        if freshness_days < 0:
            raise ValueError("freshness_days 不能小于 0")
        self.dataset_root = dataset_root
        self.freshness_days = freshness_days

    def parse_target(
        self,
        text: str,
        target: TargetJobRequest | None = None,
    ) -> TargetJobRequest | None:
        if target:
            return target
        query = text.strip()
        if not query:
            return None
        city = next((city for city in self.CITY_NAMES if city in query), None)
        keyword = query
        for item in self.CITY_NAMES:
            keyword = keyword.replace(item, " ")
        keyword = re.sub(
            r"(?:请帮我|帮我|我想|请|查询|统计|分析|找|招聘|岗位|职位|市场|薪资|工资|情况|看看|关于)",
            " ",
            keyword,
        )
        keyword = re.split(
            r"(?:通常如何|一般如何|如何|怎么|有哪些|有什么|岗位要求|任职要求|技能要求|技术栈|需要哪些)",
            keyword,
            maxsplit=1,
        )[0]
        keyword = re.sub(r"(?:应届|经验不限|1\s*[-~至]\s*3\s*年|3\s*[-~至]\s*5\s*年|5\s*[-~至]\s*10\s*年)", " ", keyword)
        keyword = re.split(r"[，,。！？?;；]", keyword, maxsplit=1)[0]
        keyword = re.sub(r"\s+", " ", keyword).strip(" ：:、")
        keyword = re.sub(r"^(?:的|在|于)\s*", "", keyword)
        if not keyword:
            return None
        experience_patterns = (
            ("应届", r"应届"),
            ("经验不限", r"经验\s*不限"),
            ("1-3年", r"1\s*[-~至]\s*3\s*年"),
            ("3-5年", r"3\s*[-~至]\s*5\s*年"),
            ("5-10年", r"5\s*[-~至]\s*10\s*年"),
        )
        experience = next(
            (label for label, pattern in experience_patterns if re.search(pattern, query)),
            None,
        )
        degree = next(
            (item for item in ("大专", "本科", "硕士", "博士", "学历不限")
             if item in query),
            None,
        )
        return TargetJobRequest(
            keyword=keyword[:200],
            city=city,
            experience=experience,
            degree=degree,
        )

    def inspect_published_dataset(
        self,
        target: TargetJobRequest,
        *,
        now: datetime | None = None,
    ) -> DatasetAvailability:
        """检查当前发布快照；简历分析不要求采集批次元数据与目标完全一致。"""

        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        folder = self._market_folder()
        path = folder / "crawl_runs.csv"
        if not path.exists():
            return DatasetAvailability(
                available=False,
                reason="当前没有可用的已发布岗位数据",
                keyword=target.keyword,
                city=target.city,
            )

        candidates: list[dict[str, str]] = []
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row.get("status") == "succeeded" and row.get("dataset_version"):
                    candidates.append(row)
        if not candidates:
            return DatasetAvailability(
                available=False,
                reason="当前没有可用的已发布岗位数据",
                keyword=target.keyword,
                city=target.city,
            )

        latest = max(candidates, key=lambda row: self._parse_time(row.get("finished_at") or row.get("started_at")))
        finished = self._parse_time(latest.get("finished_at") or latest.get("started_at"))
        age = max(0.0, (now - finished).total_seconds() / 86400)
        return DatasetAvailability(
            available=True,
            reason="将在当前已发布岗位库的全部已有数据中匹配目标岗位",
            dataset_version=latest["dataset_version"],
            run_id=latest.get("run_id"),
            age_days=age,
            keyword=target.keyword,
            city=target.city,
        )

    def inspect_dataset(
        self,
        target: TargetJobRequest,
        *,
        now: datetime | None = None,
    ) -> DatasetAvailability:
        now = now or datetime.now(timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        folder = self._market_folder()
        path = folder / "crawl_runs.csv"
        if not path.exists():
            return DatasetAvailability(
                available=False, reason="岗位数据集不存在", keyword=target.keyword, city=target.city
            )
        candidates: list[dict[str, str]] = []
        with path.open(newline="", encoding="utf-8") as stream:
            for row in csv.DictReader(stream):
                if row.get("status") != "succeeded":
                    continue
                if row.get("keyword", "").strip().casefold() != target.keyword.casefold():
                    continue
                if target.city and row.get("city", "").strip() != target.city:
                    continue
                if not row.get("dataset_version"):
                    continue
                candidates.append(row)
        if not candidates:
            return DatasetAvailability(
                available=False,
                reason="没有匹配岗位关键词和城市的已发布采集批次",
                keyword=target.keyword,
                city=target.city,
            )
        latest = max(candidates, key=lambda row: self._parse_time(row.get("finished_at") or row.get("started_at")))
        finished = self._parse_time(latest.get("finished_at") or latest.get("started_at"))
        age = max(0.0, (now - finished).total_seconds() / 86400)
        if age > self.freshness_days:
            return DatasetAvailability(
                available=False,
                reason=f"数据集已超过 {self.freshness_days} 天",
                dataset_version=latest["dataset_version"],
                run_id=latest.get("run_id"),
                age_days=age,
                keyword=target.keyword,
                city=target.city,
            )
        return DatasetAvailability(
            available=True,
            reason="存在未过期的已发布数据集",
            dataset_version=latest["dataset_version"],
            run_id=latest.get("run_id"),
            age_days=age,
            keyword=target.keyword,
            city=target.city,
        )

    def decide(
        self,
        request: UserRequest,
        *,
        action: str,
        career_mode: str | None = None,
        now: datetime | None = None,
    ) -> RouteDecision:
        if action in {"direct_response", "data_qa"}:
            return RouteDecision(action=action, reason="无需检查岗位数据资源")
        target = self.parse_target(request.text, request.target)
        if not target:
            return RouteDecision(
                action="clarify",
                clarification=CoordinatorClarification(
                    parameter="target",
                    question="请补充目标岗位关键词，例如“上海 Python 工程师”。",
                    reason="岗位关键词会决定数据集和查询口径。",
                ),
                reason="缺少目标岗位关键词",
            )
        if not target.city:
            return RouteDecision(
                action="clarify",
                target=target,
                clarification=CoordinatorClarification(
                    parameter="city",
                    question="请选择目标城市。",
                    reason="城市会改变岗位样本和薪资统计结果。",
                    options=self.DEFAULT_CITY_OPTIONS,
                ),
                reason="缺少城市筛选条件",
            )
        if action == "career_advice" and career_mode != "job_requirement" and not request.resume_id:
            return RouteDecision(
                action="clarify",
                target=target,
                clarification=CoordinatorClarification(
                    parameter="resume_id",
                    question="请先上传简历，再进行技能覆盖分析。",
                    reason="求职建议需要简历证据，不能凭空生成经历。",
                ),
                reason="缺少简历",
            )
        availability = (
            self.inspect_published_dataset(target, now=now)
            if action == "career_advice"
            else self.inspect_dataset(target, now=now)
        )
        if not availability.available:
            return RouteDecision(
                action="request_crawl",
                target=target,
                availability=availability,
                reason=availability.reason,
            )
        return RouteDecision(
            action=action if action in {"database_query", "career_advice"} else "database_query",
            target=target,
            availability=availability,
            career_mode=career_mode,
            reason="岗位数据资源可用",
        )

    def _market_folder(self) -> Path:
        if self.dataset_root:
            root = self.dataset_root.resolve()
            return root if root.name == "jobs_market" else root / "jobs_market"
        return Path(__file__).resolve().parents[2] / "data" / "databases" / "jobs_market"

    @staticmethod
    def _parse_time(raw: str | None) -> datetime:
        if not raw:
            return datetime.min.replace(tzinfo=timezone.utc)
        parsed = datetime.fromisoformat(raw)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)


__all__ = [
    "Coordinator",
    "CoordinatorClarification",
    "DatasetAvailability",
    "RouteDecision",
]
