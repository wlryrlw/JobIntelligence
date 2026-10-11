
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

from ..contracts import (
    QueryAgentResult,
    QueryEventType,
    QueryPlan,
    SchemaCatalog,
    SqlAttempt,
    SqlErrorKind,
    SqlExecution,
    SqlGeneration,
    SqlValidation,
)
from ..model_client import ModelClient
from ..security import AccessScope
from .duckdb_engine import DuckDbEngine
from .schema_catalog import SchemaCatalogBuilder
from .sql_errors import bounded_error, classify_sql_error

EventSink = Callable[[str, dict[str, Any]], None]
SqlGenerator = Callable[[str, SchemaCatalog, dict[str, Any] | None], str | dict[str, Any]]


class QueryAgent:
    """NL2SQL 生成、只读校验、执行和有界自修正闭环。"""

    MAX_ATTEMPTS = 3

    def __init__(
        self,
        model_client: ModelClient,
        engine: DuckDbEngine | None = None,
        catalog_builder: SchemaCatalogBuilder | None = None,
        *,
        max_attempts: int = MAX_ATTEMPTS,
        sql_generator: SqlGenerator | None = None,
    ) -> None:
        if not 1 <= max_attempts <= self.MAX_ATTEMPTS:
            raise ValueError(f"max_attempts 必须在 1 到 {self.MAX_ATTEMPTS} 之间")
        self.model_client = model_client
        self.engine = engine or DuckDbEngine()
        self.catalog_builder = catalog_builder or SchemaCatalogBuilder(self.engine.database_root)
        self.max_attempts = max_attempts
        self.sql_generator = sql_generator

    def run(
        self,
        plan: QueryPlan | str | dict[str, Any],
        database: str | None = None,
        *,
        access_scope: AccessScope | dict[str, Any] | None = None,
        event_sink: EventSink | None = None,
    ) -> QueryAgentResult:
        if isinstance(plan, QueryPlan):
            query_plan = plan
        elif isinstance(plan, dict):
            query_plan = QueryPlan.model_validate(plan)
        else:
            query_plan = QueryPlan(query=plan, database=database or "")
        selected_database = self._select_database(query_plan, database, access_scope)
        attempts: list[SqlAttempt] = []
        catalog: SchemaCatalog | None = None
        try:
            catalog = self.catalog_builder.build(selected_database, access_scope)
            self._emit(
                event_sink,
                QueryEventType.SCHEMA_LOADED,
                {
                    "database": selected_database,
                    "dataset_version": catalog.dataset_version,
                    "table_count": len(catalog.tables),
                    "field_count": len(catalog.fields),
                    "prompt_char_count": catalog.prompt_char_count,
                    "prompt_token_count": catalog.prompt_token_count,
                },
            )
        except (PermissionError, ValueError, OSError) as exc:
            kind, _ = classify_sql_error(exc)
            return self._failed(query_plan, selected_database, attempts, kind, str(exc), catalog)

        previous: dict[str, Any] | None = None
        for attempt_number in range(1, self.max_attempts + 1):
            try:
                generated = self._generate(query_plan, catalog, previous)
            except (RuntimeError, ValueError, TypeError, KeyError) as exc:
                kind = SqlErrorKind.MODEL_UNAVAILABLE if isinstance(exc, RuntimeError) else SqlErrorKind.UNKNOWN
                message = bounded_error(exc)
                self._emit(
                    event_sink,
                    QueryEventType.FAILED,
                    {"attempt": attempt_number, "error_kind": kind.value, "error": message},
                )
                return self._failed(query_plan, selected_database, attempts, kind, message, catalog)

            if generated.action == "clarify":
                clarification = generated.clarification
                if clarification is None:
                    raise PipelineStageError(
                        "query_clarification",
                        "QueryAgent澄清动作缺少结构化澄清内容",
                    )
                self._emit(
                    event_sink,
                    QueryEventType.CLARIFICATION,
                    clarification.model_dump(mode="json"),
                )
                return QueryAgentResult(
                    query=query_plan.query,
                    database=selected_database,
                    dataset_version=catalog.dataset_version,
                    status="waiting_clarification",
                    clarification=clarification,
                    schema=catalog,
                )

            sql = generated.sql
            self._emit(
                event_sink,
                QueryEventType.SQL_GENERATED,
                {
                    "attempt": attempt_number,
                    "sql": sql,
                    "explanation": generated.explanation,
                },
            )
            try:
                safe_sql = self.engine.validate_sql(selected_database, sql, self._scope(access_scope))
            except Exception as exc:
                kind, retryable = classify_sql_error(exc)
                record = SqlAttempt(
                    attempt=attempt_number,
                    sql=sql,
                    validation=SqlValidation.REJECTED,
                    execution=SqlExecution.FAILED,
                    error_kind=kind,
                    retryable=retryable,
                )
                attempts.append(record)
                self._emit(
                    event_sink,
                    QueryEventType.SQL_VALIDATED,
                    {
                        "attempt": attempt_number,
                        "validation": record.validation.value,
                        "error_kind": kind.value,
                        "retryable": retryable,
                        "error": bounded_error(exc),
                    },
                )
                if not retryable or attempt_number >= self.max_attempts:
                    return self._failed(
                        query_plan, selected_database, attempts, kind, bounded_error(exc), catalog
                    )
                previous = self._repair_context(sql, kind, str(exc), attempt_number)
                self._emit(event_sink, QueryEventType.SQL_REPAIR, previous)
                continue

            self._emit(
                event_sink,
                QueryEventType.SQL_VALIDATED,
                {"attempt": attempt_number, "validation": SqlValidation.PASSED.value},
            )
            execution = self.engine.execute(
                selected_database, safe_sql, self._scope(access_scope)
            )
            if execution.success:
                record = SqlAttempt(
                    attempt=attempt_number,
                    sql=safe_sql,
                    validation=SqlValidation.PASSED,
                    execution=SqlExecution.SUCCEEDED,
                )
                attempts.append(record)
                self._emit(
                    event_sink,
                    QueryEventType.SQL_EXECUTED,
                    {
                        "attempt": attempt_number,
                        "execution": SqlExecution.SUCCEEDED.value,
                        "row_count": len(execution.rows),
                    },
                )
                self._emit(
                    event_sink,
                    QueryEventType.COMPLETED,
                    {
                        "attempts": len(attempts),
                        "row_count": len(execution.rows),
                        "dataset_version": catalog.dataset_version,
                    },
                )
                return QueryAgentResult(
                    query=query_plan.query,
                    database=selected_database,
                    dataset_version=catalog.dataset_version,
                    status="completed",
                    sql=safe_sql,
                    columns=execution.columns,
                    rows=execution.rows,
                    attempts=attempts,
                    schema=catalog,
                )

            kind, retryable = self._execution_error(execution)
            record = SqlAttempt(
                attempt=attempt_number,
                sql=safe_sql,
                validation=SqlValidation.PASSED,
                execution=SqlExecution.FAILED,
                error_kind=kind,
                retryable=retryable,
            )
            attempts.append(record)
            error = bounded_error(execution.error)
            self._emit(
                event_sink,
                QueryEventType.SQL_EXECUTED,
                {
                    "attempt": attempt_number,
                    "execution": SqlExecution.FAILED.value,
                    "error_kind": kind.value,
                    "retryable": retryable,
                    "error": error,
                },
            )
            if not retryable or attempt_number >= self.max_attempts:
                self._emit(
                    event_sink,
                    QueryEventType.FAILED,
                    {
                        "attempts": len(attempts),
                        "error_kind": kind.value,
                        "error": error,
                    },
                )
                return self._failed(query_plan, selected_database, attempts, kind, error, catalog)
            previous = self._repair_context(safe_sql, kind, error, attempt_number)
            self._emit(event_sink, QueryEventType.SQL_REPAIR, previous)

        return self._failed(
            query_plan,
            selected_database,
            attempts,
            SqlErrorKind.UNKNOWN,
            "SQL 执行尝试已耗尽",
            catalog,
        )

    async def arun(self, *args: Any, **kwargs: Any) -> QueryAgentResult:
        """在 FastAPI/异步 LangGraph 节点中运行，避免阻塞事件循环。"""

        return await asyncio.to_thread(self.run, *args, **kwargs)

    def _generate(
        self,
        plan: QueryPlan,
        catalog: SchemaCatalog,
        previous: dict[str, Any] | None,
    ) -> SqlGeneration:
        if self.sql_generator:
            raw = self.sql_generator(plan.query, catalog, previous)
            if isinstance(raw, str):
                return SqlGeneration(sql=raw)
            return SqlGeneration.model_validate(raw)

        system = (
            "你是严格的 NL2SQL 生成器。只能输出 JSON。信息不足且会改变结果口径时，"
            "返回 {\"action\":\"clarify\",\"clarification\":{...}}；否则返回 "
            '{"action":"generate_sql","sql":"一条SELECT或WITH SQL","explanation":"简短说明"}。'
            "SQL 必须只使用下面完整 Schema 中的表和字段，显式列出字段，禁止 SELECT *、"
            "多语句、写操作、文件函数和跨库引用。"
            "相对日期表达式和已确认参数必须被纳入查询口径；不要再次调用外部工具。"
            + chr(10)
            + catalog.render()
        )
        if previous:
            user_payload = {
                "query": plan.query,
                "repair": {
                    "failed_sql": previous["failed_sql"],
                    "error_kind": previous["error_kind"],
                    "error": previous["error"],
                    "attempt": previous["attempt"],
                },
                "instruction": "只修正导致错误的 SQL，保持用户问题和结果口径不变。",
            }
        else:
            user_payload = {
                "query": plan.query,
                "filters": plan.filters,
                "metrics": plan.metrics,
                "dimensions": plan.dimensions,
                "time_expressions": plan.time_expressions,
                "confirmed_parameters": plan.confirmed_parameters,
            }
        payload = self.model_client.chat_json(
            system, json.dumps(user_payload, ensure_ascii=False)
        )
        if not isinstance(payload, dict):
            raise ValueError("SQL 生成器必须返回JSON对象")
        return SqlGeneration.model_validate(payload)

    @staticmethod
    def _repair_context(
        sql: str, kind: SqlErrorKind, error: str, attempt: int
    ) -> dict[str, Any]:
        return {
            "attempt": attempt,
            "failed_sql": sql,
            "error_kind": kind.value,
            "error": bounded_error(error),
        }

    @staticmethod
    def _execution_error(execution: SqlExecution) -> tuple[SqlErrorKind, bool]:
        if execution.error_kind:
            try:
                kind = SqlErrorKind(execution.error_kind)
                return kind, bool(execution.retryable)
            except ValueError:
                pass
        return classify_sql_error(execution.error)

    @staticmethod
    def _scope(access_scope: AccessScope | dict[str, Any] | None) -> AccessScope | None:
        if access_scope is None or isinstance(access_scope, AccessScope):
            return access_scope
        return AccessScope.from_dict(access_scope)

    @staticmethod
    def _select_database(
        plan: QueryPlan,
        database: str | None,
        access_scope: AccessScope | dict[str, Any] | None,
    ) -> str:
        selected = (database or plan.database).strip()
        if selected:
            return selected
        scope = QueryAgent._scope(access_scope)
        if scope and scope.allowed_databases:
            return sorted(scope.allowed_databases)[0]
        return "jobs_market"

    @staticmethod
    def _failed(
        plan: QueryPlan,
        database: str,
        attempts: list[SqlAttempt],
        kind: SqlErrorKind,
        error: str,
        catalog: SchemaCatalog | None,
    ) -> QueryAgentResult:
        return QueryAgentResult(
            query=plan.query,
            database=database,
            dataset_version=catalog.dataset_version if catalog else plan.dataset_version,
            status="failed",
            attempts=attempts,
            error_kind=kind,
            error=bounded_error(error, 2000),
            schema=catalog,
        )

    @staticmethod
    def _emit(
        sink: EventSink | None, event_type: QueryEventType | str, payload: dict[str, Any]
    ) -> None:
        if sink:
            sink(str(event_type), payload)


__all__ = ["QueryAgent", "EventSink", "SqlGenerator"]
