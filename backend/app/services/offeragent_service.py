from __future__ import annotations

import asyncio
import uuid
from typing import Any

from langgraph.types import Command

from ..config import Settings, settings
from ..crawling import CrawlRequest, CrawlRunManager, parse_crawl_intent
from ..database import SCHEMA
from ..contracts import TargetJobRequest
from ..errors import PipelineStageError
from ..model_client import ModelClient
from ..models import QueryResult
from ..models import Clarification, ClarificationOption
from ..querying import QueryAgent, SchemaCatalogBuilder
from ..query_runtime import (
    ProgressSink,
    QueryRunStore,
    QueryTimeoutError,
    current_query_runtime,
    query_runtime,
)
from ..querying.column_labels import column_labels
from ..security import AccessController
from ..workflows.query_graph import QueryWorkflow
from .memory_store import MemoryStore
from .session_context import SessionContext


class OfferAgentService:
    """查询服务入口，负责会话管理和工作流调用。"""

    def __init__(
        self,
        model_client: ModelClient | None = None,
        config: Settings | None = None,
        crawl_manager: CrawlRunManager | None = None,
    ) -> None:
        self.model_client = model_client or ModelClient(settings)
        self.config = config or getattr(self.model_client, "config", settings)
        self.access_controller = AccessController()
        self.context = SessionContext(self.model_client, self.config)
        self.memories = MemoryStore(limit=self.config.max_saved_memories)
        self.workflow = QueryWorkflow(self.model_client, self.config)
        self.schema_catalog = SchemaCatalogBuilder()
        self.query_agent = QueryAgent(self.model_client, catalog_builder=self.schema_catalog)
        self.crawl_manager = crawl_manager or CrawlRunManager()
        self.query_runs = QueryRunStore()
        self.tasks = self.context.tasks
        self.session_tasks = self.context.session_tasks

    async def submit_async(
        self,
        query: str,
        session_id: str,
        workspace: dict[str, Any] | None = None,
        user_id: str | None = None,
        *,
        resume_id: str | None = None,
        target: TargetJobRequest | None = None,
        use_jd_rag: bool = False,
        task_id: str | None = None,
    ) -> QueryResult:
        """异步 API 入口：采集副作用只创建任务，普通查询放到受控线程边界。"""

        normalized = query.strip()
        access_scope = self.access_controller.resolve(user_id)
        run_task_id = task_id or uuid.uuid4().hex[:12]
        self.query_runs.start(run_task_id, access_scope.user_id)
        result: QueryResult | None = None
        scoped_session_id = f"{access_scope.user_id}:{session_id}"
        try:
            pending = self.context.latest_pending(scoped_session_id)
            if pending and pending.get("user_id") == access_scope.user_id:
                option_id = self.context.match_clarification(normalized, pending["result"])
                if option_id in {"start_crawl", "cancel_crawl"}:
                    result = await self._resolve_crawl_confirmation(
                        pending,
                        option_id,
                        user_message=normalized,
                        user_id=access_scope.user_id,
                    )
                elif option_id:
                    # 兼容旧版客户端：即使把澄清文本作为普通查询提交，也要恢复原任务。
                    result = await asyncio.to_thread(
                        self.clarify,
                        pending["result"].task_id,
                        option_id,
                        normalized,
                        access_scope.user_id,
                    )
            if result is None and parse_crawl_intent(normalized):
                result = self._prepare_crawl_confirmation(
                    normalized,
                    session_id,
                    workspace,
                    access_scope.user_id,
                    task_id=run_task_id,
                )
            if result is None:
                sink: ProgressSink = lambda stage, message, status, metadata: self.query_runs.publish(
                    run_task_id, stage, message, status, metadata
                )
                with query_runtime(
                    run_task_id,
                    self.config.query_timeout_seconds,
                    progress_sink=sink,
                ):
                    result = await asyncio.wait_for(
                        asyncio.to_thread(
                            self.submit,
                            normalized,
                            session_id,
                            workspace,
                            access_scope.user_id,
                            resume_id=resume_id,
                            target=target,
                            use_jd_rag=use_jd_rag,
                            task_id=run_task_id,
                        ),
                        timeout=max(1.0, self.config.query_timeout_seconds + 2),
                    )
        except asyncio.TimeoutError:
            result = self._timeout_result(
                run_task_id,
                QueryTimeoutError(
                    "query_timeout",
                    "问答任务超过总时间预算",
                    elapsed_ms=int(self.config.query_timeout_seconds * 1000),
                    timeout_seconds=self.config.query_timeout_seconds,
                ),
            )
        finally:
            if result is not None:
                self.query_runs.finish(run_task_id, result)
        if result is None:
            raise RuntimeError("问答任务未返回结果")
        return result

    def _prepare_crawl_confirmation(
        self,
        query: str,
        session_id: str,
        workspace: dict[str, Any] | None,
        user_id: str,
        *,
        task_id: str | None = None,
    ) -> QueryResult:
        draft = parse_crawl_intent(query)
        if draft is None:
            raise ValueError("无法识别采集请求")
        task_id = task_id or uuid.uuid4().hex[:12]
        scoped_session_id = f"{user_id}:{session_id}"
        resolved_workspace = self.context.normalize_workspace(workspace)
        missing = []
        if not draft.keyword:
            missing.append("岗位关键词")
        if not draft.city:
            missing.append("城市")
        crawl_request: CrawlRequest | None = None
        if not missing:
            crawl_request = CrawlRequest(
                keyword=draft.keyword or "",
                city=draft.city or "",
                pages=draft.pages,
                detail=draft.detail,
                source="chat",
                confirmation=False,
            )
            resolved_workspace["crawl_request"] = crawl_request.model_dump(mode="json")
            summary = f"关键词“{crawl_request.keyword}”、城市“{crawl_request.city}”、{crawl_request.pages} 页、{'包含' if crawl_request.detail else '不含'} JD 详情。"
            question = f"我将访问 BOSS 专用 Chrome 采集 {summary}预计需要几分钟，是否开始？"
            reason = "采集会启动浏览器并访问外部站点，需要用户明确确认。"
            options = [
                ClarificationOption(id="start_crawl", label="开始采集", description="确认当前参数并创建异步采集任务", recommended=True),
                ClarificationOption(id="cancel_crawl", label="先不采集", description="不启动浏览器，返回普通对话", recommended=False),
            ]
        else:
            question = f"要创建采集任务，还需要补充：{'、'.join(missing)}。也可以直接打开右侧“更新岗位数据”面板。"
            reason = "关键词和城市是采集任务的必填参数。"
            options = [ClarificationOption(id="cancel_crawl", label="先不采集", description="不启动浏览器", recommended=False)]
        result = QueryResult(
            task_id=task_id,
            status="waiting_clarification",
            route="market_data_refresh",
            message="采集前需要确认",
            clarification=Clarification(parameter="crawl_confirmation", question=question, reason=reason, options=options),
            steps=["识别岗位数据采集意图", "校验关键词和城市", "等待外部访问确认"],
            analysis=question,
            route_reason="采集属于外部副作用，必须先经过用户确认",
            workflow_mode="crawl_confirmation",
        )
        self.context.archive.save_message(scoped_session_id, "user", query, task_id=task_id)
        self.context.remember(scoped_session_id, result, query, resolved_workspace, user_id)
        self.context.archive.save_message(scoped_session_id, "assistant", result.analysis or result.message, task_id=task_id, metadata={"route": result.route, "status": result.status})
        return result

    async def _resolve_crawl_confirmation(
        self,
        pending: dict[str, Any],
        option_id: str,
        *,
        user_message: str,
        user_id: str,
    ) -> QueryResult:
        current: QueryResult = pending["result"]
        task_id = current.task_id
        if option_id == "cancel_crawl":
            result = QueryResult(
                task_id=task_id,
                status="completed",
                route="direct_response",
                message="已取消采集",
                analysis="没有启动 Chrome，也没有访问 BOSS 直聘。",
                steps=["收到用户选择", "未创建采集任务"],
                workflow_mode="crawl_cancelled",
            )
        else:
            raw_request = pending.get("workspace", {}).get("crawl_request")
            if not raw_request:
                raise ValueError("采集确认缺少原始参数，请重新提交采集请求")
            request = CrawlRequest.model_validate(raw_request).model_copy(update={"confirmation": True, "source": "chat"})
            run = await self.crawl_manager.create(request, owner_id=user_id)
            result = QueryResult(
                task_id=task_id,
                status="completed",
                route="market_data_refresh",
                message="采集任务已创建",
                analysis=f"已创建采集任务 {run.run_id}。请在任务进度卡片或右侧面板查看 Chrome 检查、等待间隔、采集和 ETL 发布进度。",
                steps=["用户确认采集参数", "创建异步 crawl_run", "等待 Chrome/CDP 预检"],
                execution_log=[{"stage": "crawl_create", "success": True, "run_id": run.run_id}],
                workflow_mode="crawl_async",
                crawl_run_id=run.run_id,
            )
        self.context.archive.save_message(pending["session_id"], "user", user_message, task_id=task_id, metadata={"type": "clarification", "option_id": option_id})
        self.context.remember(pending["session_id"], result, pending["query"], pending["workspace"], user_id)
        self.context.archive.save_message(pending["session_id"], "assistant", result.analysis or result.message, task_id=task_id, metadata={"route": result.route, "status": result.status})
        return result

    def submit(
        self,
        query: str,
        session_id: str,
        workspace: dict[str, Any] | None = None,
        user_id: str | None = None,
        *,
        resume_id: str | None = None,
        target: TargetJobRequest | None = None,
        use_jd_rag: bool = False,
        task_id: str | None = None,
        event_sink: ProgressSink | None = None,
    ) -> QueryResult:
        task_id = task_id or uuid.uuid4().hex[:12]
        try:
            runtime = current_query_runtime()
            if runtime is None:
                with query_runtime(
                    task_id,
                    self.config.query_timeout_seconds,
                    progress_sink=event_sink,
                ):
                    return self._submit_inner(
                        query,
                        session_id,
                        workspace,
                        user_id,
                        resume_id=resume_id,
                        target=target,
                        use_jd_rag=use_jd_rag,
                        task_id=task_id,
                    )
            return self._submit_inner(
                query,
                session_id,
                workspace,
                user_id,
                resume_id=resume_id,
                target=target,
                use_jd_rag=use_jd_rag,
                task_id=task_id,
            )
        except QueryTimeoutError as exc:
            result = self._timeout_result(task_id, exc)
            access_scope = self.access_controller.resolve(user_id)
            scoped_session_id = f"{access_scope.user_id}:{session_id}"
            resolved_workspace = self.context.normalize_workspace(workspace)
            self.context.remember(
                scoped_session_id,
                result,
                query.strip(),
                resolved_workspace,
                access_scope.user_id,
            )
            self.context.archive.save_message(
                scoped_session_id,
                "assistant",
                result.analysis or result.message,
                task_id=task_id,
                metadata={"route": result.route, "status": result.status},
            )
            return result

    def _submit_inner(
        self,
        query: str,
        session_id: str,
        workspace: dict[str, Any] | None = None,
        user_id: str | None = None,
        *,
        resume_id: str | None = None,
        target: TargetJobRequest | None = None,
        use_jd_rag: bool = False,
        task_id: str,
    ) -> QueryResult:
        normalized = query.strip()
        access_scope = self.access_controller.resolve(user_id)
        scoped_session_id = f"{access_scope.user_id}:{session_id}"
        pending = self.context.latest_pending(scoped_session_id)
        if pending and pending.get("user_id") == access_scope.user_id:
            option_id = self.context.match_clarification(normalized, pending["result"])
            if option_id:
                return self.clarify(
                    pending["result"].task_id,
                    option_id,
                    user_message=normalized,
                    user_id=access_scope.user_id,
                )

        resolved_workspace = self.context.normalize_workspace(workspace)
        self.context.archive.save_message(
            scoped_session_id,
            "user",
            normalized,
            task_id=task_id,
        )
        try:
            result = self.workflow.invoke(
                self._payload(
                    task_id,
                    normalized,
                    scoped_session_id,
                    resolved_workspace,
                    access_scope.public(),
                    resume_id=resume_id,
                    target=target,
                    use_jd_rag=use_jd_rag,
                ),
                task_id,
            )
        except PipelineStageError as exc:
            runtime = current_query_runtime()
            result = QueryResult(
                task_id=task_id,
                status="failed",
                route="data_qa" if exc.stage == "qa_answer" else "database_query",
                message=f"处理停止：{exc.stage}失败",
                analysis=exc.message,
                route_reason="未使用本地语义兜底",
                steps=[f"{exc.stage}失败，流程已停止"],
                execution_log=[{"stage": exc.stage, "success": False, "error": exc.message}],
                workflow_mode="failed_explicitly",
                elapsed_ms=runtime.elapsed_ms if runtime else None,
                failure_stage=exc.stage,
            )
        self.context.remember(
            scoped_session_id,
            result,
            normalized,
            resolved_workspace,
            access_scope.user_id,
        )
        self.context.archive.save_message(
            scoped_session_id,
            "assistant",
            result.analysis or result.message,
            task_id=task_id,
            metadata={"route": result.route, "status": result.status},
        )
        return result

    def _timeout_result(self, task_id: str, error: QueryTimeoutError) -> QueryResult:
        stage_labels = {
            "request_preprocessing": "请求理解与路由",
            "schema_catalog": "岗位数据结构读取",
            "sql_generation": "只读 SQL 生成",
            "sql_validation": "SQL 安全校验",
            "sql_execution": "岗位数据查询执行",
            "data_qa": "已有结果分析",
            "result_analysis": "查询结果整理",
            "career_profile": "目标岗位画像",
            "career_analysis": "求职分析",
            "llm_call": "模型调用",
            "query_timeout": "问答总流程",
        }
        label = stage_labels.get(error.stage, error.stage)
        elapsed = error.elapsed_ms
        route = (
            "data_qa"
            if error.stage in {"data_qa", "qa_answer"}
            else "career_advice"
            if error.stage.startswith("career")
            else "database_query"
        )
        return QueryResult(
            task_id=task_id,
            status="failed",
            route=route,
            message="本次问答超时，已停止继续等待。",
            analysis=(
                f"已用时 {elapsed / 1000:.1f} 秒，在“{label}”阶段超时。"
                "请稍后重试；如果持续发生，请检查模型接口响应时间和配置。"
            ),
            steps=["开始问答", f"{label}超时", "停止后续调用"],
            execution_log=[{
                "stage": error.stage,
                "success": False,
                "error_code": "timeout",
                "elapsed_ms": elapsed,
                "timeout_seconds": error.timeout_seconds,
            }],
            workflow_mode="timeout",
            elapsed_ms=elapsed,
            error_code="timeout",
            failure_stage=error.stage,
        )

    def clarify(
        self,
        task_id: str,
        option_id: str,
        user_message: str | None = None,
        user_id: str | None = None,
    ) -> QueryResult:
        try:
            runtime = current_query_runtime()
            if runtime is None:
                with query_runtime(task_id, self.config.query_timeout_seconds):
                    return self._clarify_inner(task_id, option_id, user_message, user_id)
            return self._clarify_inner(task_id, option_id, user_message, user_id)
        except QueryTimeoutError as exc:
            result = self._timeout_result(task_id, exc)
            task = self.tasks.get(task_id)
            if task is not None:
                access_scope = self.access_controller.resolve(user_id)
                self.context.remember(
                    task["session_id"],
                    result,
                    task["query"],
                    task["workspace"],
                    access_scope.user_id,
                )
                self.context.archive.save_message(
                    task["session_id"],
                    "assistant",
                    result.analysis or result.message,
                    task_id=task_id,
                    metadata={"route": result.route, "status": result.status},
                )
            return result

    def _clarify_inner(
        self,
        task_id: str,
        option_id: str,
        user_message: str | None = None,
        user_id: str | None = None,
    ) -> QueryResult:
        task = self.tasks.get(task_id)
        if not task:
            raise KeyError(task_id)
        access_scope = self.access_controller.resolve(user_id)
        if task.get("user_id", AccessController.DEFAULT_USER) != access_scope.user_id:
            raise PermissionError("无权访问该查询任务")
        current: QueryResult = task["result"]
        if not current.clarification or option_id not in {item.id for item in current.clarification.options}:
            raise ValueError(option_id)
        option = next(item for item in current.clarification.options if item.id == option_id)
        self.context.archive.save_message(
            task["session_id"],
            "user",
            user_message or option.label,
            task_id=task_id,
            metadata={"type": "clarification", "option_id": option_id},
        )
        result = self.workflow.invoke(Command(resume={"option_id": option_id}), task_id)
        self.context.remember(
            task["session_id"],
            result,
            task["query"],
            task["workspace"],
            access_scope.user_id,
        )
        self.context.archive.save_message(
            task["session_id"],
            "assistant",
            result.analysis or result.message,
            task_id=task_id,
            metadata={"route": result.route, "status": result.status},
        )
        return result

    def get_task(self, task_id: str, user_id: str | None = None) -> QueryResult:
        if task_id not in self.tasks:
            raise KeyError(task_id)
        scope = self.access_controller.resolve(user_id)
        if self.tasks[task_id].get("user_id", AccessController.DEFAULT_USER) != scope.user_id:
            raise PermissionError("无权访问该查询任务")
        return self.tasks[task_id]["result"]

    def save_memory(self, task_id: str, user_id: str | None = None) -> None:
        task = self.tasks.get(task_id)
        if not task or task["result"].status != "completed":
            raise KeyError(task_id)
        scope = self.access_controller.resolve(user_id)
        if task.get("user_id", AccessController.DEFAULT_USER) != scope.user_id:
            raise PermissionError("无权保存该查询结果")
        result: QueryResult = task["result"]
        self.memories.save_result(
            task_id=task_id,
            query=task["query"],
            summary=result.analysis or result.message,
            title=result.result_title or "查询结果",
            columns=result.columns,
            rows=result.rows,
            user_id=scope.user_id,
            column_labels=result.column_labels,
        )
        result.saved = True

    def delete_memory(self, memory_id: str, user_id: str | None = None) -> None:
        scope = self.access_controller.resolve(user_id)
        self.memories.delete(
            memory_id,
            scope.user_id,
            include_all="admin" in scope.roles,
        )

    def list_memories(self, user_id: str | None = None) -> list[dict[str, Any]]:
        scope = self.access_controller.resolve(user_id)
        memories = self.memories.list(
            scope.user_id,
            include_all="admin" in scope.roles,
        )
        return [
            {
                **item,
                "column_labels": {
                    **column_labels(item.get("columns") or []),
                    **dict(item.get("column_labels") or {}),
                },
            }
            if item.get("kind") == "result_table"
            else item
            for item in memories
        ]

    def list_mcp_tools(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """返回当前用户可用的数据库 MCP 工具定义。"""
        scope = self.access_controller.resolve(user_id)
        return self.workflow.database_mcp_client(scope.public()).list_tools()

    def list_skills(self) -> list[dict[str, Any]]:
        """返回当前启用的应用级Skill配置。"""
        return self.workflow.skills.list()

    def visible_schema(self, user_id: str | None = None) -> list[dict[str, Any]]:
        return self.access_controller.filter_schema(
            self.access_controller.resolve(user_id)
        )

    def status(self) -> dict[str, Any]:
        return {
            **self.config.public_status(),
            "workflow_engine": "langgraph",
            "human_in_the_loop": "interrupt+checkpointer",
            "mcp": {
                "transport": "in_process",
                "tool_count": len(self.list_mcp_tools()),
            },
            "skills": [item["name"] for item in self.list_skills()],
            "schema_catalog": {
                "mode": "full_prompt",
                "token_estimate": "characters_div_4",
            },
        }

    def _payload(
        self,
        task_id: str,
        query: str,
        session_id: str,
        workspace: dict[str, Any],
        access_scope: dict[str, Any] | None = None,
        *,
        resume_id: str | None = None,
        target: TargetJobRequest | None = None,
        use_jd_rag: bool = False,
    ) -> dict[str, Any]:
        analysis_context, analysis_sources = self.context.analysis_context(session_id, workspace)
        return {
            "task_id": task_id,
            "query": query,
            "session_id": session_id,
            "workspace": self.context.query_workspace(workspace),
            "resume_id": resume_id,
            "target": target.model_dump(mode="json") if target else None,
            "use_jd_rag": use_jd_rag,
            "access_scope": access_scope or self.access_controller.resolve(None).public(),
            "route_context": self.context.route_context(session_id, workspace),
            "short_term_context": self.context.short_term_context(session_id),
            "recent_result_context": self.context.recent_result_context(session_id),
            "analysis_context": analysis_context,
            "analysis_sources": analysis_sources,
            "tool_facts": {},
            "execution_log": [],
            "tool_calls": [],
            "clarification": None,
        }

    _normalize_workspace = staticmethod(SessionContext.normalize_workspace)
    _query_workspace = staticmethod(SessionContext.query_workspace)
