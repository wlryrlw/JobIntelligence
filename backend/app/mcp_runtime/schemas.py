from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DatabaseQueryResult(BaseModel):
    database: str = Field(description="执行查询的数据库名称")
    sql: str = Field(description="实际执行或尝试执行的SQL")
    success: bool = Field(description="SQL是否执行成功")
    columns: list[str] = Field(default_factory=list, description="结果字段")
    rows: list[dict[str, Any]] = Field(default_factory=list, description="查询结果，最多200行")
    row_count: int = Field(default=0, description="返回结果行数")
    error: str | None = Field(default=None, description="执行失败时的错误信息")
