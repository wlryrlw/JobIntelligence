from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..config import Settings, settings
from ..contracts import QueryPlan, UserRequest
from ..career import (
    CareerAgent,
    CareerAgentError,
    JobProfileBuilder,
    JobProfileError,
    ResumeNotFound,
    ResumeStore,
    ResumeStoreError,
)
from ..coordinator import Coordinator
from ..errors import PipelineStageError
from ..mcp_runtime import LocalMcpClient, create_local_mcp_server
from ..model_client import ModelClient
from ..models import QueryResult
from ..preprocessing import RequestPreprocessor
from ..query_runtime import QueryTimeoutError, emit_progress
from ..querying.duckdb_engine import DuckDbEngine
from ..querying.data_qa_agent import DataQaAgent
from ..querying.models import SqlExecution
from ..querying.response_generator import ResponseGenerator
from ..querying.query_agent import QueryAgent
from ..querying.schema_catalog import SchemaCatalogBuilder
from ..rag import JdRagService, RagError
from ..security import AccessScope
from ..skills import SkillRegistry
from .result_builder import ResultBuilder
from .state import QueryState


class QueryWorkflow:
    """基于 LangGraph 的问答和数据库查询工作流。"""

    def __init__(
        self,
        model_client: ModelClient,
        config: Settings | None = None,
    ) -> None:
        self.model_client = model_client
        self.config = config or settings
        self.database_engine = DuckDbEngine()
        self.coordinator = Coordinator(dataset_root=self.database_engine.database_root)
        self.preprocessor = RequestPreprocessor(model_client)
        self.skills = SkillRegistry()
        self.query_agent = QueryAgent(
            model_client,
            self.database_engine,
            SchemaCatalogBuilder(self.database_engine.database_root),
            max_attempts=3,
        )
        self.response_generator = ResponseGenerator(
            model_client,
            self.config,
        )
        self.resume_store = ResumeStore()
        self.career_agent = CareerAgent(
            model_client,
            profile_builder=JobProfileBuilder(
                self.database_engine.database_root,
                model_client=model_client,
                use_llm=self.config.career_llm_enabled,
            ),
        )
        self.jd_rag = JdRagService(
            self.config,
            database_root=self.database_engine.database_root,
        )
        self.data_qa_agent = DataQaAgent(
            model_client,
            self.skills.get("data_qa"),
        )
        self.checkpointer = InMemorySaver()
        self.graph = self._compile()

    def database_mcp_client(self, access_scope: dict[str, Any]) -> LocalMcpClient:
        scope = AccessScope.from_dict(access_scope)
        return LocalMcpClient(create_local_mcp_server(self.database_engine, scope))

    def _compile(self):
        builder = StateGraph(QueryState)
        builder.add_node("preprocess", self._preprocess)
        builder.add_node("respond_directly", self._respond_directly)
        builder.add_node("answer_qa", self._answer_qa)
        builder.add_node("coordinate_career", self._coordinate_career)
        builder.add_node("query_agent", self._query_agent)
        builder.add_node("human_clarification", self._human_clarification)
        builder.add_edge(START, "preprocess")
        builder.add_conditional_edges(
            "preprocess",
            self._route_after_preprocess,
            {
                "respond_directly": "respond_directly",
                "answer_qa": "answer_qa",
                "coordinate_career": "coordinate_career",
                "query_agent": "query_agent",
            },
        )
        builder.add_edge("respond_directly", END)
        builder.add_edge("answer_qa", END)
        builder.add_conditional_edges(
            "coordinate_career",
            self._route_after_coordinate,
            {
                "human_clarification": "human_clarification",
                "end": END,
            },
        )
        builder.add_conditional_edges(
            "human_clarification",
            self._route_after_clarification,
            {
                "coordinate_career": "coordinate_career",
                "query_agent": "query_agent",
            },
        )
        builder.add_conditional_edges(
            "query_agent",
            lambda state: "human_clarification" if state.get("clarification") else "end",
            {"human_clarification": "human_clarification", "end": END},
        )
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def run_config(task_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": task_id}}

    def invoke(self, payload: QueryState | Command, task_id: str) -> QueryResult:
        emit_progress("workflow", "正在协调岗位问答流程")
        state = self.graph.invoke(payload, config=self.run_config(task_id))
        return self._state_result(state, task_id)

    def _state_result(self, state: dict[str, Any], task_id: str) -> QueryResult:
        if state.get("result"):
            return QueryResult.model_validate(state["result"])
        state = {**state, "task_id": state.get("task_id") or task_id}
        return ResultBuilder.waiting(state)

    @staticmethod
    def _route_after_preprocess(state: QueryState) -> str:
        action = str(state["intent"]["action"])
        if action == "career_advice":
            return "coordinate_career"
        if action == "database_query":
            return "query_agent"
        return {"direct_response": "respond_directly", "data_qa": "answer_qa"}[action]

    @staticmethod
    def _route_after_coordinate(state: QueryState) -> str:
        return "human_clarification" if state.get("clarification") else "end"

    @staticmethod
    def _route_after_clarification(state: QueryState) -> str:
        action = str((state.get("intent") or {}).get("action"))
        return "coordinate_career" if action == "career_advice" else "query_agent"

    def _preprocess(self, state: QueryState) -> dict[str, Any]:
        """生成路由、独立查询和 SQL 生成所需的查询口径。"""
        decision = self.preprocessor.prepare(
            state["query"],
            state.get("route_context", ""),
            has_data_context=bool(
                state.get("recent_result_context") or state.get("analysis_context")
            ),
            use_jd_rag=bool(state.get("use_jd_rag")),
        )
        emit_progress(
            "route_selected",
            f"已选择处理路径：{decision.action}",
            metadata={"action": decision.action},
        )
        execution_log = list(state.get("execution_log") or [])
        if decision.source == "model_unavailable_fallback":
            execution_log.append({
                "stage": "route_fallback",
                "success": True,
                "source": decision.source,
                "action": decision.action,
                "reason": decision.reason,
            })
        explicit_target = state.get("target")
        parsed_target = decision.target.model_dump(mode="json") if decision.target else None
        explicit_resume_id = state.get("resume_id")
        return {
            "intent": {
                "action": decision.action,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "response_type": decision.response_type,
                "source": decision.source,
                "career_mode": decision.career_mode,
            },
            "direct_response": decision.response,
            "standalone_query": decision.standalone_query,
            "rewritten": decision.rewritten,
            "target": explicit_target or parsed_target,
            "resume_id": explicit_resume_id or decision.resume_id,
            "career_mode": decision.career_mode or state.get("career_mode"),
            "extraction": decision.extraction.public(),
            "execution_log": execution_log,
        }

    def _respond_directly(self, state: QueryState) -> dict[str, Any]:
        """返回预处理模型生成的普通回答或自然语言澄清。"""
        emit_progress("direct_response", "正在整理 OfferAgent 回复")
        result = ResultBuilder.direct_response(
            state["task_id"],
            state.get("direct_response", ""),
            state["intent"],
            list(state.get("execution_log") or []),
        )
        return {
            "workflow_mode": result.workflow_mode,
            "result": result.model_dump(mode="json"),
        }

    def _answer_qa(self, state: QueryState) -> dict[str, Any]:
        emit_progress("data_qa", "正在分析已有岗位结果")
        qa_result = self.data_qa_agent.run(
            state["query"],
            {
                "short_term": state.get("short_term_context", ""),
                "recent_result": state.get("recent_result_context", ""),
                "selected_tables": state.get("analysis_context", ""),
            },
        )
        result = ResultBuilder.qa(
            state["task_id"], qa_result, state["intent"], state.get("analysis_sources") or []
        )
        return {
            "workflow_mode": "qa_report" if qa_result.report else "qa",
            "result": result.model_dump(mode="json"),
        }

    def _query_agent(self, state: QueryState) -> dict[str, Any]:
        database = self._database_for_query(state)
        execution_log: list[dict[str, Any]] = []

        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            execution_log.append({"stage": event_type, "success": event_type != "query.failed", **payload})
            messages = {
                "query.schema_loaded": "已读取岗位数据结构",
                "query.sql_generated": "已生成只读 SQL",
                "query.sql_validated": "已完成 SQL 安全校验",
                "query.sql_executed": "已完成岗位数据查询",
                "query.sql_repair": "正在修正 SQL 并准备有限重试",
                "query.completed": "数据库查询完成",
                "query.failed": "数据库查询失败",
                "query.clarification": "查询需要补充信息",
            }
            stages = {
                "query.schema_loaded": "schema_catalog",
                "query.sql_generated": "sql_generation",
                "query.sql_validated": "sql_validation",
                "query.sql_executed": "sql_execution",
                "query.sql_repair": "sql_generation",
                "query.completed": "completed",
                "query.failed": "query_failed",
                "query.clarification": "clarification",
            }
            emit_progress(
                stages.get(event_type, event_type.removeprefix("query.")),
                messages.get(event_type, "正在执行岗位数据查询"),
                status="failed" if event_type == "query.failed" else "running",
                metadata={
                    key: value
                    for key, value in payload.items()
                    if key in {"attempt", "row_count", "attempts", "table_count", "field_count"}
                },
            )

        extraction = state.get("extraction") or {}
        workspace = state.get("workspace") or {}
        plan = QueryPlan(
            query=state.get("standalone_query") or state["query"],
            database=database,
            filters=[str(item) for item in extraction.get("filters") or []],
            metrics=[str(item) for item in extraction.get("metrics") or []],
            dimensions=[str(item) for item in extraction.get("dimensions") or []],
            time_expressions=[
                str(item) for item in extraction.get("time_expressions") or []
            ],
            confirmed_parameters={
                str(key): str(value)
                for key, value in (workspace.get("confirmed_parameters") or {}).items()
            },
        )
        result = self.query_agent.run(
            plan,
            access_scope=state.get("access_scope") or {},
            event_sink=on_event,
        )

        if result.status == "waiting_clarification":
            clarification = result.clarification
            if clarification is None:
                raise PipelineStageError(
                    "query_clarification",
                    "QueryAgent返回等待状态但缺少澄清内容",
                )
            return {
                "workflow_mode": "query_agent_clarification",
                "execution_log": execution_log,
                "clarification": clarification.model_dump(mode="json"),
                "result": {},
            }

        schema_context = result.schema.render() if result.schema else state.get("schema_context", "")
        sql = result.sql or (result.attempts[-1].sql if result.attempts else "")
        execution = SqlExecution(
            sql=sql,
            success=result.status == "completed",
            columns=result.columns,
            rows=result.rows,
            error=result.error,
            error_kind=result.error_kind.value if result.error_kind else None,
        )
        query_state = {
            **state,
            "workflow_mode": "query_agent",
            "schema_context": schema_context,
            "schema_tables": [
                {"name": table.name, "label": table.label}
                for table in result.schema.tables
            ] if result.schema else [],
            "dataset_version": result.dataset_version,
            "prompt_token_count": result.schema.prompt_token_count if result.schema else None,
        }
        call = {
            "call_index": 1,
            "database": database,
            "arguments": {
                "mode": "query_agent",
                "transport": "direct",
                "schema_catalog_version": result.dataset_version,
                "sql_source": "model",
            },
            "sql": execution.sql,
            "success": execution.success,
            "row_count": len(execution.rows),
            "error": execution.error,
        }
        if not execution.success:
            final_result = ResultBuilder.failed(query_state, execution, execution_log)
            return {
                "workflow_mode": "query_agent",
                "execution_log": execution_log,
                "tool_calls": [call],
                "result": final_result.model_dump(mode="json"),
            }

        try:
            final = self.response_generator.finalize(
                plan.query,
                execution,
                schema_context,
                state.get("analysis_context", ""),
            )
        except QueryTimeoutError as exc:
            execution_log.append({
                "stage": exc.stage,
                "success": False,
                "error_code": "timeout",
                "elapsed_ms": exc.elapsed_ms,
            })
            final = {
                "valid": True,
                "reason": "结果说明阶段超时",
                "title": "查询结果（说明生成超时）",
                "analysis": (
                    f"SQL已成功执行，但结果说明在“{exc.stage}”阶段超时；"
                    f"已用时 {exc.elapsed_ms / 1000:.1f} 秒，下面展示原始查询结果。"
                ),
            }
        except PipelineStageError as exc:
            execution_log.append({"stage": exc.stage, "success": False, "error": exc.message})
            final = {
                "valid": True,
                "reason": f"{exc.stage}失败",
                "title": "查询结果（文字说明生成失败）",
                "analysis": f"SQL已成功执行，但{exc.stage}失败：{exc.message}",
            }

        final_result = ResultBuilder.completed(
            query_state,
            [execution],
            execution,
            final,
            [call],
            execution_log,
        )
        return {
            "workflow_mode": "query_agent",
            "execution_log": execution_log,
            "tool_calls": [call],
            "result": final_result.model_dump(mode="json"),
        }

    def _coordinate_career(self, state: QueryState) -> dict[str, Any]:
        emit_progress("career_resource_gate", "正在检查简历、目标岗位和岗位数据资源")
        request = UserRequest(
            task_id=state["task_id"],
            session_id=state.get("session_id") or "default-session",
            text=state["query"],
            resume_id=state.get("resume_id"),
            target=state.get("target"),
        )
        career_mode = str(state.get("career_mode") or "resume_gap")
        decision = self.coordinator.decide(
            request,
            action="career_advice",
            career_mode=career_mode,
        )
        if decision.action == "clarify":
            clarification = decision.clarification.model_dump(mode="json") if decision.clarification else {
                "parameter": "target",
                "question": decision.reason,
                "reason": decision.reason,
                "options": [],
            }
            waiting_state = {
                **state,
                "intent": {**(state.get("intent") or {}), "action": "career_advice"},
                "clarification": clarification,
                "workflow_mode": "career_clarification",
            }
            return {
                "workflow_mode": "career_clarification",
                "clarification": clarification,
                "result": ResultBuilder.waiting(waiting_state).model_dump(mode="json"),
            }
        if decision.action == "request_crawl":
            availability = decision.availability.model_dump(mode="json") if decision.availability else {}
            result = QueryResult(
                task_id=state["task_id"],
                status="failed",
                route="career_advice",
                message="当前没有可用的已发布岗位数据。",
                analysis="当前岗位库中没有可直接用于简历与岗位分析的已发布数据，无法生成岗位画像。",
                route_reason=decision.reason,
                steps=["意图路由：career_advice", "检查当前已发布岗位库", "没有可用岗位数据，停止分析"],
                workflow_mode="career_agent_failed",
                execution_log=[{"stage": "resource_gate", "success": False, "availability": availability}],
            )
            return {"workflow_mode": "career_agent_failed", "result": result.model_dump(mode="json")}
        availability = decision.availability.model_dump(mode="json") if decision.availability else {}
        resource_log = {"stage": "resource_gate", "success": True, "availability": availability}
        target = decision.target
        if target is None:
            raise CareerAgentError("Coordinator 未返回有效目标岗位")
        if career_mode == "job_requirement":
            return self._coordinate_job_requirement(state, decision, resource_log, target)
        user_id = str((state.get("access_scope") or {}).get("user_id") or "anonymous")
        resume_id = str(state.get("resume_id") or "")
        rag_documents = []
        try:
            emit_progress("career_resume", "正在读取本地结构化简历证据")
            resume = self.resume_store.load(resume_id, user_id=user_id)
            emit_progress("career_profile", "正在构建目标岗位画像")
            profile = self.career_agent.build_profile(target)
            if state.get("use_jd_rag"):
                emit_progress("career_rag", "正在检索岗位 JD 证据")
                rag_documents = self.jd_rag.retrieve(
                    state["query"], target, dataset_version=profile.dataset_version
                )
                if not rag_documents:
                    raise RagError("岗位 JD RAG 没有返回与目标岗位匹配的证据")
            emit_progress("career_analysis", "正在生成证据约束的求职分析")
            report = self.career_agent.analyze(resume, profile, rag_documents=rag_documents)
        except ResumeNotFound:
            clarification = {
                "parameter": "resume_id",
                "question": "简历不存在或不属于当前用户，请重新上传简历。",
                "reason": "为避免泄露简历归属，系统不会区分不存在和无权访问。",
                "options": [],
            }
            waiting_state = {
                **state,
                "intent": {**(state.get("intent") or {}), "action": "career_advice"},
                "clarification": clarification,
                "workflow_mode": "career_clarification",
            }
            return {
                "workflow_mode": "career_clarification",
                "clarification": clarification,
                "result": ResultBuilder.waiting(waiting_state).model_dump(mode="json"),
            }
        except QueryTimeoutError:
            raise
        except (ResumeStoreError, CareerAgentError, JobProfileError, RagError, ValueError) as exc:
            execution_log = [
                resource_log,
                {
                    "stage": "career.failed",
                    "success": False,
                    "error_type": type(exc).__name__,
                },
            ]
            result = QueryResult(
                task_id=state["task_id"],
                status="failed",
                route="career_advice",
                message="求职分析失败。",
                analysis=f"简历证据、岗位画像或 JD RAG 不可用：{exc}",
                route_reason=decision.reason,
                steps=["意图路由：career_advice", "检查简历与岗位参数", "CareerAgent 执行失败并停止"],
                workflow_mode="career_agent_failed",
                execution_log=execution_log,
            )
            return {"workflow_mode": "career_agent_failed", "result": result.model_dump(mode="json")}

        execution_log = [
            resource_log,
            {
                "stage": "career.resume_loaded",
                "success": True,
                "resume_id": resume.resume_id,
                "skill_count": len(resume.skills),
                "project_count": len(resume.projects),
            },
            {
                "stage": "career.profile_built",
                "success": True,
                "dataset_version": profile.dataset_version,
                "sample_size": profile.sample_size,
                "valid_jd_count": profile.valid_jd_count,
            },
            {
                "stage": "career.completed",
                "success": True,
                "report_id": report.report_id,
            },
        ]
        result = QueryResult(
            task_id=state["task_id"],
            status="completed",
            route="career_advice",
            message="求职分析完成。",
            analysis=report.summary,
            career_report=report,
            route_reason=decision.reason,
            steps=[
                "意图路由：career_advice",
                "检查简历与岗位参数",
                "加载用户隔离的结构化简历证据",
                "构建带样本口径和证据的岗位画像",
                "完成技能覆盖分析",
            ],
            workflow_mode="career_agent",
            execution_log=execution_log,
            dataset_version=profile.dataset_version,
            rag_used=bool(rag_documents),
            rag_index_version=profile.dataset_version if rag_documents else None,
            rag_sources=[item.public_source() for item in rag_documents],
        )
        return {"workflow_mode": "career_agent", "result": result.model_dump(mode="json")}

    def _coordinate_job_requirement(
        self,
        state: QueryState,
        decision: Any,
        resource_log: dict[str, Any],
        target: Any,
    ) -> dict[str, Any]:
        """无简历的岗位要求问题：必须用 JD RAG，不生成简历匹配结论。"""

        rag_documents = []
        try:
            emit_progress("career_profile", "正在构建目标岗位画像")
            profile = self.career_agent.build_profile(target)
            emit_progress("career_rag", "正在检索岗位 JD 证据")
            rag_documents = self.jd_rag.retrieve(
                state["query"], target, dataset_version=profile.dataset_version
            )
            emit_progress("career_analysis", "正在整理岗位要求分析")
            answer = self.career_agent.answer_job_requirement(
                state["query"], profile, rag_documents, use_llm=True
            )
        except (CareerAgentError, JobProfileError, RagError, ValueError) as exc:
            result = QueryResult(
                task_id=state["task_id"],
                status="failed",
                route="career_advice",
                message="岗位要求分析失败。",
                analysis=f"岗位画像或 JD RAG 不可用：{exc}",
                route_reason=decision.reason,
                steps=[
                    "意图路由：career_advice",
                    "构建目标岗位画像",
                    "检索岗位 JD 证据并停止",
                ],
                workflow_mode="career_agent_failed",
                execution_log=[
                    resource_log,
                    {"stage": "career.job_requirement_failed", "success": False, "error_type": type(exc).__name__},
                ],
            )
            return {"workflow_mode": "career_agent_failed", "result": result.model_dump(mode="json")}

        result = QueryResult(
            task_id=state["task_id"],
            status="completed",
            route="career_advice",
            message="岗位要求分析完成。",
            analysis=answer,
            route_reason=decision.reason,
            steps=[
                "意图路由：career_advice",
                "构建目标岗位画像",
                "Milvus BM25 + Embedding 双路召回",
                "RRF 融合并进行候选重排",
                "LLM 基于岗位 JD 证据归纳要求并给出综合解读",
            ],
            workflow_mode="career_agent_job_requirement",
            execution_log=[
                resource_log,
                {
                    "stage": "career.rag_retrieval",
                    "success": True,
                    "dataset_version": profile.dataset_version,
                    "hit_count": len(rag_documents),
                    "rerank_enabled": self.config.rag_rerank_enabled,
                },
            ],
            dataset_version=profile.dataset_version,
            rag_used=True,
            rag_index_version=profile.dataset_version,
            rag_sources=[item.public_source() for item in rag_documents],
        )
        return {"workflow_mode": "career_agent_job_requirement", "result": result.model_dump(mode="json")}

    @staticmethod
    def _database_for_query(state: QueryState) -> str:
        confirmed = str(
            (state.get("workspace") or {}).get("confirmed_parameters", {}).get("database")
            or ""
        ).strip()
        if confirmed:
            return confirmed
        allowed = sorted(
            str(item)
            for item in (state.get("access_scope") or {}).get("allowed_databases") or []
        )
        return "jobs_market" if "jobs_market" in allowed else allowed[0] if allowed else "jobs_market"

    def _human_clarification(self, state: QueryState) -> dict[str, Any]:
        # LangGraph 将 Command(resume=...) 的值作为 interrupt 返回值。
        response = interrupt(state.get("clarification") or {})
        option_id = str(response.get("option_id") if isinstance(response, dict) else response)
        workspace = dict(state.get("workspace") or {})
        payload = state.get("clarification") or {}
        parameter = str(payload.get("parameter") or "other")
        workspace["confirmed_parameters"] = {
            **dict(workspace.get("confirmed_parameters") or {}),
            parameter: option_id,
        }
        target = state.get("target")
        if target is not None and parameter in {
            "keyword",
            "city",
            "company",
            "experience",
            "degree",
        }:
            target = {**target, parameter: option_id}
        return {
            "workspace": workspace,
            "target": target,
            "clarification": None,
            "result": {},
        }
