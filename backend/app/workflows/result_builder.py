from __future__ import annotations

from typing import Any

from ..models import Clarification, Interpretation, QueryResult
from ..querying.models import SqlExecution
from ..querying.data_qa_agent import DataQaResult
from ..querying.column_labels import column_labels
from ..query_runtime import current_query_runtime


class ResultBuilder:
    """将工作流状态转换为 API 响应。"""

    @staticmethod
    def qa(
        task_id: str,
        generated: DataQaResult,
        intent: dict[str, Any],
        analysis_sources: list[dict[str, Any]],
    ) -> QueryResult:
        runtime = current_query_runtime()
        return QueryResult(
            task_id=task_id,
            status="completed",
            route="data_qa",
            message="已进入数据问答，没有重复查询数据库。",
            columns=[],
            rows=[],
            analysis=generated.report.markdown if generated.report else generated.answer,
            route_reason=str(intent.get("reason") or "数据问答"),
            steps=(
                ["一级意图路由：数据问答", "读取可用分析上下文", "生成Markdown报告并调用展示工具"]
                if generated.report
                else ["一级意图路由：数据问答", "读取可用分析上下文", "生成文字回答"]
            ),
            result_title=generated.report.title if generated.report else "结果解读",
            analysis_sources=analysis_sources,
            workflow_mode="qa_report" if generated.report else "qa",
            report=generated.report,
            report_tool_calls=generated.tool_calls,
            elapsed_ms=runtime.elapsed_ms if runtime else None,
        )

    @staticmethod
    def direct_response(
        task_id: str,
        response: str,
        intent: dict[str, Any],
        execution_log: list[dict[str, Any]],
    ) -> QueryResult:
        runtime = current_query_runtime()
        response_type = str(intent.get("response_type") or "answer")
        fallback_used = intent.get("source") == "model_unavailable_fallback"
        return QueryResult(
            task_id=task_id,
            status="completed",
            route="direct_response",
            message="请补充说明" if response_type == "clarification" else "已直接回答",
            columns=[],
            rows=[],
            analysis=response,
            route_reason=str(intent.get("reason") or "OfferAgent直接回答"),
            steps=["识别请求类型", "生成 OfferAgent 回答"],
            execution_log=execution_log,
            workflow_mode="route_fallback" if fallback_used else "direct_response",
            elapsed_ms=runtime.elapsed_ms if runtime else None,
        )

    @staticmethod
    def waiting(state: dict[str, Any]) -> QueryResult:
        runtime = current_query_runtime()
        payload = state.get("clarification") or {}
        clarification = Clarification(
            parameter=str(payload.get("parameter") or "other"),
            question=str(payload.get("question") or "请补充本次查询所需信息"),
            reason=str(payload.get("reason") or "该信息会改变查询结果"),
            options=payload.get("options") or [],
        )
        labels = "、".join(item.label for item in clarification.options)
        return QueryResult(
            task_id=state["task_id"],
            status="waiting_clarification",
            route=(
                "career_advice"
                if str((state.get("intent") or {}).get("action")) == "career_advice"
                else "database_query"
            ),
            message="查询已暂停，等待你的补充信息。",
            clarification=clarification,
            analysis=f"{clarification.question} 你可以直接回复：{labels}。",
            route_reason=str(state.get("intent", {}).get("reason") or "问数"),
            standalone_query=state.get("standalone_query"),
            steps=(
                ["意图路由：career_advice", "检查简历、岗位参数和数据集资源", "等待用户补充"]
                if str((state.get("intent") or {}).get("action")) == "career_advice"
                else ["一次请求预处理：路由并提取查询口径", "读取完整SchemaCatalog", "LangGraph暂停并等待用户回复"]
            ),
            workflow_mode=str(state.get("workflow_mode") or "langgraph_hitl"),
            elapsed_ms=runtime.elapsed_ms if runtime else None,
        )

    @staticmethod
    def completed(
        state: dict[str, Any],
        executions: list[SqlExecution],
        combined: SqlExecution,
        final: dict[str, Any],
        tool_calls: list[dict[str, Any]],
        execution_log: list[dict[str, Any]],
    ) -> QueryResult:
        runtime = current_query_runtime()
        timed_out = next(
            (item for item in reversed(execution_log) if item.get("error_code") == "timeout"),
            None,
        )
        table_labels = [
            str(item.get("label") or item.get("name") or "")
            for item in state.get("schema_tables") or []
            if item.get("label") or item.get("name")
        ]
        metric_columns = [
            column for column in combined.columns
            if any(term in column for term in ("额", "数", "率", "平均", "目标"))
        ]
        dimension_columns = [column for column in combined.columns if column not in metric_columns]
        mode = str(state.get("workflow_mode") or "query_agent")
        steps = [
            "一次预处理：上下文聚合、问答/问数路由、查询口径提取",
            *([f"独立查询改写：{state.get('standalone_query')}"] if state.get("rewritten") else []),
            f"读取完整SchemaCatalog：{len(table_labels)}张表",
            "QueryAgent根据SchemaCatalog生成SQL",
            "AccessScope校验、SQLGlot只读校验并执行DuckDB",
        ]
        if state.get("analysis_sources"):
            steps.append(f"综合分析{len(state['analysis_sources'])}张用户指定历史表")
        valid = bool(final.get("valid", True))
        analysis = str(final.get("analysis") or "").strip()
        if not analysis:
            if valid and combined.rows:
                analysis = f"查询已完成，返回 {len(combined.rows)} 行结果。"
            elif valid:
                analysis = "查询已完成，但数据库没有返回匹配行。"
            else:
                analysis = "结果校验未通过，未返回可用结果。"
        return QueryResult(
            task_id=state["task_id"],
            status="completed" if valid else "failed",
            route="database_query",
            message=(
                "查询完成（结果说明阶段超时）"
                if timed_out and valid
                else "查询完成（无匹配结果）" if valid and not combined.rows
                else "查询完成" if valid else "结果校验未通过"
            ),
            interpretation=Interpretation(
                metric="、".join(metric_columns) or "查询结果指标",
                dimension="、".join(dimension_columns) or "无分组维度",
                time_range="、".join(
                    (state.get("extraction") or {}).get("time_expressions") or []
                ) or "未指定",
                table="、".join(table_labels) or "QueryAgent完整SchemaCatalog",
                assumptions=["查询仅使用当前用户有权访问的完整Schema", "岗位结果上下文按需传入"],
            ),
            steps=steps,
            sql=combined.sql,
            columns=combined.columns,
            column_labels=column_labels(combined.columns),
            rows=combined.rows,
            analysis=analysis,
            route_reason=str(state.get("intent", {}).get("reason") or "问数"),
            execution_log=execution_log,
            tool_calls=tool_calls,
            result_title=final.get("title") or "查询结果",
            analysis_sources=state.get("analysis_sources") or [],
            standalone_query=state.get("standalone_query"),
            workflow_mode=mode,
            dataset_version=state.get("dataset_version"),
            prompt_token_count=state.get("prompt_token_count"),
            elapsed_ms=runtime.elapsed_ms if runtime else None,
            error_code="timeout" if timed_out else None,
            failure_stage=str(timed_out.get("stage")) if timed_out else None,
        )

    @staticmethod
    def failed(state: dict[str, Any], execution: SqlExecution, log: list[dict[str, Any]]) -> QueryResult:
        runtime = current_query_runtime()
        return QueryResult(
            task_id=state["task_id"],
            status="failed",
            route="database_query",
            message="SQL生成或执行失败",
            analysis=execution.error,
            sql=execution.sql or None,
            route_reason=str(state.get("intent", {}).get("reason") or "问数"),
            execution_log=log,
            standalone_query=state.get("standalone_query"),
            workflow_mode=str(state.get("workflow_mode") or "query_agent"),
            elapsed_ms=runtime.elapsed_ms if runtime else None,
        )
