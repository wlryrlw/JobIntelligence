
import re

from ..contracts import SqlErrorKind

_RETRYABLE = {
    SqlErrorKind.SYNTAX,
    SqlErrorKind.UNKNOWN_FIELD,
    SqlErrorKind.UNKNOWN_TABLE,
    SqlErrorKind.TYPE_BINDING,
}


def classify_sql_error(error: str | BaseException | None) -> tuple[SqlErrorKind, bool]:
    """把 DuckDB/sqlglot/访问控制错误归一化为有限集合。"""

    message = str(error or "").strip()
    lowered = message.lower()
    if not message:
        return SqlErrorKind.UNKNOWN, False
    if any(term in lowered for term in ("permission", "无权", "权限")):
        kind = SqlErrorKind.PERMISSION
    elif any(term in lowered for term in (
        "select *", "禁止", "write", "attach", "detach", "file function",
        "read_csv", "read_text", "跨库访问", "不允许",
    )):
        kind = SqlErrorKind.SECURITY
    elif any(term in lowered for term in ("no such file", "不存在", "database", "数据库文件夹")):
        kind = SqlErrorKind.DATABASE_NOT_FOUND
    elif any(term in lowered for term in ("timeout", "timed out", "超时")):
        kind = SqlErrorKind.TIMEOUT
    elif any(term in lowered for term in (
        "column", "field", "binder error", "does not exist", "unknown identifier", "字段",
    )):
        kind = SqlErrorKind.UNKNOWN_FIELD
    elif any(term in lowered for term in ("table", "relation", "数据表")):
        kind = SqlErrorKind.UNKNOWN_TABLE
    elif any(term in lowered for term in ("type", "cannot cast", "conversion", "类型")):
        kind = SqlErrorKind.TYPE_BINDING
    elif any(term in lowered for term in ("syntax", "parser", "parse error", "unexpected token", "语法")):
        kind = SqlErrorKind.SYNTAX
    else:
        kind = SqlErrorKind.UNKNOWN
    return kind, kind in _RETRYABLE


def bounded_error(error: str | BaseException | None, limit: int = 1200) -> str:
    message = re.sub(r"\s+", " ", str(error or "")).strip()
    return message[:limit]


__all__ = ["bounded_error", "classify_sql_error"]
