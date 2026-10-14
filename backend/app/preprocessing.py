from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from .errors import PipelineStageError
from .model_client import ModelClient
from .contracts import TargetJobRequest
from .coordinator import Coordinator
from .query_runtime import QueryTimeoutError, emit_progress


RouteName = Literal["data_qa", "database_query", "career_advice", "direct_response"]
CareerMode = Literal["job_requirement", "resume_gap", "resume_rewrite", "interview_preparation"]
ResponseType = Literal["answer", "clarification"]
logger = logging.getLogger(__name__)


@dataclass
class QueryExtraction:
    """供 QueryAgent 生成 SQL 的结构化查询口径。"""

    metrics: list[str] = field(default_factory=list)
    dimensions: list[str] = field(default_factory=list)
    filters: list[str] = field(default_factory=list)
    time_expressions: list[str] = field(default_factory=list)
    operations: list[str] = field(default_factory=list)

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PreparedRequest:
    """请求预处理结果。"""

    action: RouteName
    confidence: float
    reason: str
    standalone_query: str = ""
    rewritten: bool = False
    extraction: QueryExtraction = field(default_factory=QueryExtraction)
    response: str = ""
    response_type: ResponseType = "answer"
    target: TargetJobRequest | None = None
    resume_id: str | None = None
    career_mode: CareerMode | None = None
    source: str = "model"


class RequestPreprocessor:
    """一次调用完成上下文聚合、意图判断、查询口径提取或直接回复。"""

    def __init__(self, model_client: ModelClient) -> None:
        self.model_client = model_client

    def prepare(
        self,
        query: str,
        recent_context: str,
        *,
        has_data_context: bool | None = None,
        use_jd_rag: bool = False,
    ) -> PreparedRequest:
        emit_progress("request_preprocessing", "正在理解问题并选择处理路径")
        if has_data_context is None:
            has_data_context = bool(recent_context.strip())
        # 这是用户显式勾选 RAG 后的确定性路由保护，避免“上海 3-5 年 Agent
        # 岗位通常如何要求 RAG？”被模型误判为普通 database_query 或 direct_response。
        if use_jd_rag and self.looks_like_job_requirement_query(query):
            return PreparedRequest(
                action="career_advice",
                confidence=1.0,
                reason="用户显式选择岗位 JD 检索，问题属于岗位要求分析",
                target=Coordinator().parse_target(query),
                career_mode="job_requirement",
                source="user_jd_rag_toggle",
            )
        system = """你负责 OfferAgent 的请求理解与回复路由。根据用户问题选择正确的下游能力，
但不能把内部模块当成产品身份向用户介绍。

意图边界：
1. database_query：用户明确要求查询新的数据库数据，例如获取指标、明细、排名、统计或对比。
2. data_qa：用户明确要求解释或分析上下文中已经存在的查询结果、表格或数据，不需要查询新数据。
3. career_advice：用户要求基于目标岗位和简历做技能缺口、简历或面试建议，或在用户显式选择 JD RAG 后询问岗位通常如何要求。
4. direct_response：除以上三类之外的请求，包括闲聊、一般交流，以及无法确定是
   查询新数据还是分析已有结果的灰色地带。此类请求由你直接回答；如果缺少的信息会影响
   意图判断，则直接提出一个简短的自然语言问题。不要生成选项，不要假装已经查询数据。

上下文聚合：只在当前问题依赖上文时理解和补全语义，不要拼接无关历史。
仅当action=database_query时，将问题改写为可独立理解的standalone_query，并提取生成SQL所需的
指标、维度、过滤条件、时间表达式和操作。data_qa和direct_response不提取查询口径。

只返回JSON：
{
  "action":"database_query|data_qa|career_advice|direct_response",
  "confidence":0.0,
  "reason":"...",
  "standalone_query":"问数时填写，其他情况为空字符串",
  "rewritten":false,
  "response":"仅direct_response填写自然语言回答或澄清问题",
  "response_type":"answer|clarification",
  "career_mode":"job_requirement|resume_gap|resume_rewrite|interview_preparation",
  "query_extraction":{
    "metrics":[], "dimensions":[], "filters":[],
    "time_expressions":[], "operations":[]
  }
}
database_query必须填写standalone_query和query_extraction，response为空。
career_advice可以提供target（keyword/city/company/experience/degree）、resume_id和career_mode；没有简历但询问岗位普遍要求时使用job_requirement。
data_qa必须清空standalone_query、response和query_extraction。
direct_response必须填写response，并清空standalone_query和query_extraction。不要猜表名。"""
        user = f"当前问题：{query}\n近期轻量上下文：{recent_context or '无'}"
        try:
            payload = self.model_client.chat_json(system, user)
        except QueryTimeoutError:
            raise
        except RuntimeError as exc:
            # 预处理失败时终止下游查询，避免错误路由访问数据库。
            logger.warning(
                "route_fallback_used stage=request_preprocessing action=direct_response error=%s",
                exc,
            )
            return PreparedRequest(
                action="direct_response",
                confidence=0.0,
                reason=f"预处理模型不可用，已启用保守兜底：{exc}",
                response="大模型服务暂时不可用，请稍后重试。",
                source="model_unavailable_fallback",
            )
        try:
            decision = self._parse(payload, query)
            if (
                not has_data_context
                and decision.action in {"data_qa", "direct_response"}
                and self.looks_like_market_data_query(query)
            ):
                return PreparedRequest(
                    action="database_query",
                    confidence=decision.confidence,
                    reason="问题包含岗位市场数据诉求，但当前没有可供解读的历史结果，已转为查询最新岗位数据",
                    standalone_query=query.strip()[:800],
                    extraction=QueryExtraction(),
                )
            return decision
        except (ValueError, KeyError, TypeError) as exc:
            raise PipelineStageError("request_preprocessing", str(exc)) from exc

    def _parse(self, payload: dict[str, Any], original_query: str) -> PreparedRequest:
        action = str(payload["action"])
        if action not in {"data_qa", "database_query", "career_advice", "direct_response"}:
            raise ValueError(f"不支持的路由结果：{action}")

        confidence = max(0.0, min(1.0, float(payload.get("confidence", 0.8))))
        reason = str(payload.get("reason") or "模型完成请求预处理")

        if action == "data_qa":
            return PreparedRequest(action="data_qa", confidence=confidence, reason=reason)

        if action == "career_advice":
            raw_target = payload.get("target")
            target = TargetJobRequest.model_validate(raw_target) if isinstance(raw_target, dict) else None
            return PreparedRequest(
                action="career_advice",
                confidence=confidence,
                reason=reason,
                target=target,
                resume_id=str(payload.get("resume_id") or "").strip() or None,
                career_mode=self._career_mode(payload.get("career_mode")),
            )

        if action == "direct_response":
            response = str(payload.get("response") or "").strip()
            if not response:
                raise ValueError("direct_response缺少response")
            response_type = str(payload.get("response_type") or "answer")
            if response_type not in {"answer", "clarification"}:
                raise ValueError(f"不支持的直接回复类型：{response_type}")
            return PreparedRequest(
                action="direct_response",
                confidence=confidence,
                reason=reason,
                response=response,
                response_type=response_type,
            )

        standalone = str(payload.get("standalone_query") or original_query).strip()[:800]
        raw = payload.get("query_extraction")
        if not isinstance(raw, dict):
            raw = payload.get("extraction") if isinstance(payload.get("extraction"), dict) else {}
        extraction = QueryExtraction(
            metrics=self._strings(raw.get("metrics")),
            dimensions=self._strings(raw.get("dimensions")),
            filters=self._strings(raw.get("filters")),
            time_expressions=self._strings(raw.get("time_expressions")),
            operations=self._strings(raw.get("operations")),
        )
        return PreparedRequest(
            action="database_query",
            confidence=confidence,
            reason=reason,
            standalone_query=standalone,
            rewritten=bool(payload.get("rewritten", standalone != original_query.strip())),
            extraction=extraction,
        )

    @staticmethod
    def looks_like_market_data_query(query: str) -> bool:
        """识别明显的岗位市场问数请求，避免无上下文时误进 data_qa。"""

        hints = (
            "岗位", "职位", "招聘", "薪资", "工资", "技能", "技术栈", "公司",
            "城市", "学历", "经验", "数量", "多少", "统计", "分布", "排名",
            "占比", "平均", "最高", "最低", "最常见", "趋势",
        )
        return any(hint in query for hint in hints)

    @staticmethod
    def looks_like_job_requirement_query(query: str) -> bool:
        query_lower = query.casefold()
        has_job_context = any(item in query_lower for item in ("岗位", "职位", "jd", "招聘", "agent"))
        requirement_hints = (
            "通常如何", "一般如何", "岗位要求", "任职要求", "技能要求", "技术栈",
            "需要哪些", "有什么要求", "有哪些要求", "如何要求", "看重什么",
        )
        return has_job_context and any(item in query_lower for item in requirement_hints)

    @staticmethod
    def _career_mode(value: Any) -> CareerMode:
        normalized = str(value or "resume_gap")
        if normalized not in {"job_requirement", "resume_gap", "resume_rewrite", "interview_preparation"}:
            raise ValueError(f"不支持的 career_mode：{normalized}")
        return normalized  # type: ignore[return-value]

    @staticmethod
    def _strings(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))
