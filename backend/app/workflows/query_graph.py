from __future__ import annotations

from typing import Any

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt

from ..config import Settings, settings
from ..contracts import UserRequest
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
from ..database import DATABASE_LABELS, SCHEMA
from ..errors import PipelineStageError
from ..mcp_runtime import LocalMcpClient, create_local_mcp_server
from ..model_client import ModelClient
from ..models import QueryResult
from ..preprocessing import RequestPreprocessor
from ..querying.duckdb_engine import DuckDbEngine
from ..querying.data_qa_agent import DataQaAgent
from ..querying.models import SqlExecution
from ..querying.response_generator import ResponseGenerator
from ..querying.query_agent import QueryAgent
from ..querying.schema_catalog import SchemaCatalogBuilder
from ..querying.single_database_agent import SingleDatabaseAgent
from ..retrieval import SchemaGraphBuilder, SchemaIndex
from ..security import AccessScope
from ..skills import SkillRegistry
from .result_builder import ResultBuilder
from .state import QueryState


class QueryWorkflow:
    """基于 LangGraph 的问答和数据库查询工作流。"""

    def __init__(
        self,
        model_client: ModelClient,
        schema_index: SchemaIndex | None = None,
        config: Settings | None = None,
    ) -> None:
        self.model_client = model_client
        self.config = config or settings
        self.database_engine = DuckDbEngine()
        self.coordinator = Coordinator(dataset_root=self.database_engine.database_root)
        self.schema_index = schema_index or SchemaIndex(model_client, self.config)
        self.preprocessor = RequestPreprocessor(model_client)
        self.skills = SkillRegistry()
        self.graph_builder = SchemaGraphBuilder()
        self.query_agent = QueryAgent(
            model_client,
            self.database_engine,
            SchemaCatalogBuilder(self.database_engine.database_root),
            max_attempts=3,
        )
        self.single_database_agent = SingleDatabaseAgent(
            model_client,
            self.mcp_client,
            self.skills.get("database_query"),
            self.config.mcp_max_tool_calls,
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
        self.data_qa_agent = DataQaAgent(
            model_client,
            self.mcp_client,
            self.skills.get("data_qa"),
        )
        self.checkpointer = InMemorySaver()
        self.graph = self._compile()

    def mcp_client(self, access_scope: dict[str, Any]) -> LocalMcpClient:
        scope = AccessScope.from_dict(access_scope)
        return LocalMcpClient(create_local_mcp_server(self.database_engine, scope))

    def _compile(self):
        builder = StateGraph(QueryState)
        builder.add_node("preprocess", self._preprocess)
        builder.add_node("respond_directly", self._respond_directly)
        builder.add_node("answer_qa", self._answer_qa)
        builder.add_node("coordinate_career", self._coordinate_career)
        builder.add_node("query_agent", self._query_agent)
        builder.add_node("retrieve_schema", self._retrieve_schema)
        builder.add_node("human_clarification", self._human_clarification)
        builder.add_node("prepare_single_database", self._prepare_single_database)
        builder.add_node("execute_single_database", self._execute_single_database)
        builder.add_node("run_multi_database", self._run_multi_database)
        builder.add_edge(START, "preprocess")
        builder.add_conditional_edges(
            "preprocess",
            self._route_after_preprocess,
            {
                "respond_directly": "respond_directly",
                "answer_qa": "answer_qa",
                "coordinate_career": "coordinate_career",
                "query_agent": "query_agent",
                "retrieve_schema": "retrieve_schema",
            },
        )
        builder.add_edge("respond_directly", END)
        builder.add_edge("answer_qa", END)
        builder.add_edge("coordinate_career", END)
        builder.add_edge("query_agent", END)
        builder.add_conditional_edges(
            "retrieve_schema",
            self._after_retrieval,
            {
                "human_clarification": "human_clarification",
                "prepare_single_database": "prepare_single_database",
                "run_multi_database": "run_multi_database",
            },
        )
        builder.add_edge("human_clarification", "retrieve_schema")
        builder.add_conditional_edges(
            "prepare_single_database",
            lambda state: "human_clarification" if state.get("clarification") else "execute_single_database",
            {
                "human_clarification": "human_clarification",
                "execute_single_database": "execute_single_database",
            },
        )
        builder.add_edge("execute_single_database", END)
        builder.add_conditional_edges(
            "run_multi_database",
            lambda state: "human_clarification" if state.get("clarification") else "end",
            {"human_clarification": "human_clarification", "end": END},
        )
        return builder.compile(checkpointer=self.checkpointer)

    @staticmethod
    def run_config(task_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": task_id}}

    def invoke(self, payload: QueryState | Command, task_id: str) -> QueryResult:
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
            allowed = {
                str(item)
                for item in (state.get("access_scope") or {}).get("allowed_databases") or []
            }
            if allowed == {"jobs_market"}:
                return "query_agent"
            return "retrieve_schema"
        return {"direct_response": "respond_directly", "data_qa": "answer_qa"}[action]

    def _preprocess(self, state: QueryState) -> dict[str, Any]:
        """生成路由、独立查询和 Schema 检索参数。"""
        decision = self.preprocessor.prepare(state["query"], state.get("route_context", ""))
        execution_log = list(state.get("execution_log") or [])
        if decision.source == "model_unavailable_fallback":
            execution_log.append({
                "stage": "route_fallback",
                "success": True,
                "source": decision.source,
                "action": decision.action,
                "reason": decision.reason,
            })
        return {
            "intent": {
                "action": decision.action,
                "confidence": decision.confidence,
                "reason": decision.reason,
                "response_type": decision.response_type,
                "source": decision.source,
            },
            "direct_response": decision.response,
            "standalone_query": decision.standalone_query,
            "rewritten": decision.rewritten,
            "target": decision.target.model_dump(mode="json") if decision.target else None,
            "resume_id": decision.resume_id,
            "extraction": decision.retrieval.public(),
            "execution_log": execution_log,
        }

    def _respond_directly(self, state: QueryState) -> dict[str, Any]:
        """返回预处理模型生成的普通回答或自然语言澄清。"""
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
        qa_result = self.data_qa_agent.run(
            state["query"],
            {
                "short_term": state.get("short_term_context", ""),
                "recent_result": state.get("recent_result_context", ""),
                "selected_tables": state.get("analysis_context", ""),
            },
            state.get("access_scope") or {},
        )
        result = ResultBuilder.qa(
            state["task_id"], qa_result, state["intent"], state.get("analysis_sources") or []
        )
        return {
            "workflow_mode": "qa_report" if qa_result.report else "qa",
            "result": result.model_dump(mode="json"),
        }

    def _query_agent(self, state: QueryState) -> dict[str, Any]:
        allowed = [
            str(item)
            for item in (state.get("access_scope") or {}).get("allowed_databases") or []
        ]
        database = "jobs_market" if "jobs_market" in allowed else (allowed[0] if allowed else "jobs_market")
        execution_log: list[dict[str, Any]] = []

        def on_event(event_type: str, payload: dict[str, Any]) -> None:
            execution_log.append({"stage": event_type, "success": event_type != "query.failed", **payload})

        result = self.query_agent.run(
            state["standalone_query"] or state["query"],
            database,
            access_scope=state.get("access_scope") or {},
            event_sink=on_event,
        )
        attempts = [item.model_dump(mode="json") for item in result.attempts]
        if result.status == "completed":
            return {
                "workflow_mode": "full_schema_query_agent",
                "execution_log": execution_log,
                "result": QueryResult(
                    task_id=state["task_id"],
                    status="completed",
                    route="database_query",
                    message="查询完成",
                    sql=result.sql,
                    columns=result.columns,
                    rows=result.rows,
                    steps=[
                        "意图路由：database_query",
                        f"全量 Schema 注入（{result.schema.prompt_token_count if result.schema else 0} token 估算）",
                        f"SQL 尝试 {len(attempts)} 次（含可修复错误回喂）",
                        "只读校验并执行 DuckDB",
                    ],
                    analysis="查询结果已按数据集版本返回。",
                    route_reason=str(state.get("intent", {}).get("reason") or "问数"),
                    execution_log=execution_log,
                    sql_attempts=attempts,
                    dataset_version=result.dataset_version,
                    prompt_token_count=result.schema.prompt_token_count if result.schema else None,
                    workflow_mode="full_schema_query_agent",
                ).model_dump(mode="json"),
            }
        return {
            "workflow_mode": "full_schema_query_agent",
            "execution_log": execution_log,
            "result": QueryResult(
                task_id=state["task_id"],
                status="failed",
                route="database_query",
                message="查询执行失败",
                analysis=result.error or "SQL 执行失败",
                route_reason=str(state.get("intent", {}).get("reason") or "问数"),
                steps=["意图路由：database_query", "全量 Schema 注入", f"SQL 尝试 {len(attempts)} 次", "执行失败并停止"],
                execution_log=execution_log,
                sql_attempts=attempts,
                dataset_version=result.dataset_version,
                prompt_token_count=result.schema.prompt_token_count if result.schema else None,
                workflow_mode="full_schema_query_agent",
            ).model_dump(mode="json"),
        }

    def _coordinate_career(self, state: QueryState) -> dict[str, Any]:
        request = UserRequest(
            task_id=state["task_id"],
            session_id=state.get("session_id") or "default-session",
            text=state["query"],
            resume_id=state.get("resume_id"),
            target=state.get("target"),
        )
        decision = self.coordinator.decide(request, action="career_advice")
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
                status="waiting_clarification",
                route="career_advice",
                message="岗位数据不可用，需要先创建采集任务。",
                analysis=f"{decision.reason}。请先创建独立采集任务，再继续求职分析。",
                route_reason=decision.reason,
                steps=["意图路由：career_advice", "检查岗位关键词、城市和数据集新鲜度", "等待独立采集任务完成"],
                workflow_mode="crawl_required",
                execution_log=[{"stage": "resource_gate", "success": False, "availability": availability}],
            )
            return {"workflow_mode": "crawl_required", "result": result.model_dump(mode="json")}
        availability = decision.availability.model_dump(mode="json") if decision.availability else {}
        resource_log = {"stage": "resource_gate", "success": True, "availability": availability}
        user_id = str((state.get("access_scope") or {}).get("user_id") or "anonymous")
        resume_id = str(state.get("resume_id") or "")
        try:
            resume = self.resume_store.load(resume_id, user_id=user_id)
            target = decision.target
            if target is None:
                raise CareerAgentError("Coordinator 未返回有效目标岗位")
            profile = self.career_agent.build_profile(target)
            report = self.career_agent.analyze(resume, profile)
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
        except (ResumeStoreError, CareerAgentError, JobProfileError, ValueError) as exc:
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
                analysis="简历证据、岗位画像或结构化分析不可用，请修正输入或数据集后重试。",
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
                "完成技能覆盖、改写方向和面试清单分析",
            ],
            workflow_mode="career_agent",
            execution_log=execution_log,
            dataset_version=profile.dataset_version,
        )
        return {"workflow_mode": "career_agent", "result": result.model_dump(mode="json")}

    @staticmethod
    def _narrowed_scope(state: QueryState) -> AccessScope | dict[str, Any] | None:
        """用户已选定目标数据库时，将访问范围收窄到该库。"""
        scope = state.get("access_scope")
        confirmed_database = str(
            (state.get("workspace") or {}).get("confirmed_parameters", {}).get("database")
            or ""
        )
        if not confirmed_database:
            return scope
        database_tables = frozenset(
            table["id"]
            for table in SCHEMA
            if str(table.get("database") or "askdata_mock") == confirmed_database
        )
        if not database_tables:
            return scope
        base = scope if isinstance(scope, AccessScope) else AccessScope.from_dict(scope)
        return AccessScope(
            user_id=base.user_id,
            roles=base.roles,
            allowed_databases=frozenset({confirmed_database}),
            allowed_tables=database_tables,
        )

    def _retrieve_schema(self, state: QueryState) -> dict[str, Any]:
        standalone_query = state["standalone_query"]
        extraction = state.get("extraction") or {}
        access_scope = self._narrowed_scope(state)
        retrieval = self.schema_index.retrieve(
            standalone_query,
            retrieval_terms=list(extraction.get("retrieval_terms") or []),
            access_scope=access_scope,
        )
        workspace = state.get("workspace") or {}
        query_workspace = {
            "schema_fields": list(workspace.get("schema_fields") or []),
            "confirmed_schema_tables": list(workspace.get("confirmed_schema_tables") or []),
            "confirmed_parameters": dict(workspace.get("confirmed_parameters") or {}),
        }
        retrieval = self.schema_index.include_workspace(
            retrieval,
            query_workspace,
            access_scope,
        )
        retrieval["extraction"] = extraction
        schema_graph = self.graph_builder.build(
            retrieval["hits"],
            access_scope,
        )
        retrieval["schema_graph"] = schema_graph
        databases = sorted({
            str(table.get("database") or schema_graph.get("database") or "askdata_mock")
            for table in schema_graph.get("tables", [])
        })
        return {
            "standalone_query": standalone_query,
            "extraction": extraction,
            "retrieval": retrieval,
            "schema_graph": schema_graph,
            "schema_context": self.graph_builder.context_text(schema_graph),
            "database_names": databases,
            "clarification": None,
            "direct_sql": "",
        }

    @staticmethod
    def _after_retrieval(state: QueryState) -> str:
        if state.get("clarification"):
            return "human_clarification"
        return "prepare_single_database" if len(state.get("database_names") or []) <= 1 else "run_multi_database"

    def _human_clarification(self, state: QueryState) -> dict[str, Any]:
        # LangGraph 将 Command(resume=...) 的值作为 interrupt 返回值。
        response = interrupt(state.get("clarification") or {})
        option_id = str(response.get("option_id") if isinstance(response, dict) else response)
        workspace = dict(state.get("workspace") or {})
        payload = state.get("clarification") or {}
        parameter = str(payload.get("parameter") or "other")
        tables = list(workspace.get("confirmed_schema_tables") or [])
        if any(table["id"] == option_id for table in SCHEMA):
            tables = list(dict.fromkeys([*tables, option_id]))
        if parameter == "database" and option_id:
            database_tables = {
                table["id"]
                for table in SCHEMA
                if str(table.get("database") or "askdata_mock") == option_id
            }
            tables = [table for table in tables if table in database_tables]
        workspace["confirmed_schema_tables"] = tables
        workspace["confirmed_parameters"] = {
            **dict(workspace.get("confirmed_parameters") or {}),
            parameter: option_id,
        }
        return {"workspace": workspace, "clarification": None, "direct_sql": "", "result": {}}

    def _prepare_single_database(self, state: QueryState) -> dict[str, Any]:
        workspace = dict(state.get("workspace") or {})
        database = (state.get("database_names") or ["askdata_mock"])[0]
        decision = self.single_database_agent.prepare(
            state["standalone_query"],
            database,
            state["schema_graph"],
            state["schema_context"],
            state["retrieval"],
            workspace,
            state.get("access_scope") or {},
        )
        if decision["action"] == "clarify":
            return {
                "workflow_mode": "single_database_agent",
                "clarification": decision["clarification"],
                "mcp_tool_trace": decision.get("tool_trace", []),
                "direct_sql": "",
            }
        execution = decision["execution"]
        return {
            "workflow_mode": "single_database_agent",
            "clarification": None,
            "mcp_execution": execution,
            "mcp_tool_trace": decision.get("tool_trace", []),
            "direct_sql": str(execution.get("sql") or ""),
            "sql_source": decision.get("source", "model"),
        }

    def _execute_single_database(self, state: QueryState) -> dict[str, Any]:
        database = (state.get("database_names") or ["askdata_mock"])[0]
        raw_execution = state.get("mcp_execution") or {}
        execution = SqlExecution(
            sql=str(raw_execution.get("sql") or state.get("direct_sql") or ""),
            success=bool(raw_execution.get("success")),
            columns=list(raw_execution.get("columns") or []),
            rows=list(raw_execution.get("rows") or []),
            error=raw_execution.get("error"),
        )
        trace = list(state.get("mcp_tool_trace") or [])
        log = [
            {
                "stage": "mcp_tool_call",
                "success": not bool(item.get("result", {}).get("error")),
                **item,
            }
            for item in trace
        ]
        log.append({
            "stage": "execute_duckdb",
            "success": execution.success,
            "error": execution.error,
            "via": "mcp",
        })
        database_call = next(
            (item for item in reversed(trace) if item.get("tool") == f"query_{database}"),
            {},
        )
        call = {
            "call_index": int(database_call.get("call_index") or 1),
            "database": database,
            "arguments": {
                "mode": "single_database_agent",
                "transport": "mcp_in_process",
                "tool_name": database_call.get("tool"),
                "schema_graph_version": state.get("schema_graph", {}).get("graph_version"),
                "sql_source": state.get("sql_source", "model"),
            },
            "sql": execution.sql,
            "success": execution.success,
            "row_count": len(execution.rows),
            "error": execution.error,
        }
        if not execution.success:
            result = ResultBuilder.failed(state, execution, log)
            return {"execution_log": log, "tool_calls": [call], "result": result.model_dump(mode="json")}
        try:
            final = self.response_generator.finalize(
                state["standalone_query"], execution, state["schema_context"],
                state.get("analysis_context", ""),
            )
        except PipelineStageError as exc:
            # 结果说明失败时仍保留已成功执行的查询结果。
            log.append({"stage": exc.stage, "success": False, "error": exc.message})
            final = {
                "valid": True,
                "reason": f"{exc.stage}失败",
                "title": "查询结果（文字说明生成失败）",
                "analysis": f"SQL已成功执行，但{exc.stage}失败：{exc.message}",
            }
        result = ResultBuilder.completed(state, [execution], execution, final, [call], log)
        return {"execution_log": log, "tool_calls": [call], "result": result.model_dump(mode="json")}

    def _run_multi_database(self, state: QueryState) -> dict[str, Any]:
        """多库命中时请求用户选择目标库，选定后回流检索按单库执行。"""
        options = []
        for database in sorted(state.get("database_names") or []):
            table_count = sum(
                1
                for table in SCHEMA
                if str(table.get("database") or "askdata_mock") == database
            )
            options.append({
                "id": database,
                "label": DATABASE_LABELS.get(database, database),
                "description": f"共 {table_count} 张表",
            })
        clarification = {
            "parameter": "database",
            "question": "本次查询的字段涉及多个数据库，请选择目标数据库",
            "reason": "当前仅支持单库查询，选择后将在该库内完成检索与执行",
            "options": options,
        }
        return {
            "workflow_mode": "multi_database_clarification",
            "clarification": clarification,
        }
