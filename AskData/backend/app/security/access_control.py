from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from ..database import SCHEMA


ALL_DATABASES = frozenset(str(table.get("database") or "askdata_mock") for table in SCHEMA)
ALL_TABLES = frozenset(str(table["id"]) for table in SCHEMA)
ECOMMERCE_TABLES = frozenset(
    str(table["id"])
    for table in SCHEMA
    if table.get("database") == "ecommerce_ops"
)
ASKDATA_MOCK_TABLES = frozenset(
    str(table["id"])
    for table in SCHEMA
    if table.get("database", "askdata_mock") == "askdata_mock"
)


@dataclass(frozen=True)
class AccessScope:
    """由后端生成并贯穿整个查询链路的权限范围。"""

    user_id: str
    roles: tuple[str, ...]
    allowed_databases: frozenset[str]
    allowed_tables: frozenset[str]

    def allows_database(self, database: str) -> bool:
        return database in self.allowed_databases

    def allows_table(self, database: str, table_id: str) -> bool:
        return self.allows_database(database) and table_id in self.allowed_tables

    def public(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["roles"] = list(self.roles)
        payload["allowed_databases"] = sorted(self.allowed_databases)
        payload["allowed_tables"] = sorted(self.allowed_tables)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> AccessScope:
        raw = payload or {}
        return cls(
            user_id=str(raw.get("user_id") or "anonymous"),
            roles=tuple(str(item) for item in raw.get("roles") or []),
            allowed_databases=frozenset(
                str(item) for item in raw.get("allowed_databases") or []
            ),
            allowed_tables=frozenset(str(item) for item in raw.get("allowed_tables") or []),
        )


class AccessController:
    """根据用户角色返回数据库和表权限。"""

    DEFAULT_USER = "demo_analyst"
    USER_ROLES = {
        "demo_admin": ("admin",),
        "demo_analyst": ("analyst",),
        "demo_current_sales": ("current_sales",),
    }
    ROLE_POLICIES = {
        "admin": {
            "databases": ALL_DATABASES,
            "tables": ALL_TABLES,
        },
        "analyst": {
            "databases": frozenset({"askdata_mock"}),
            "tables": ASKDATA_MOCK_TABLES,
        },
        "current_sales": {
            "databases": frozenset({"ecommerce_ops"}),
            "tables": ECOMMERCE_TABLES,
        },
    }

    def resolve(self, user_id: str | None) -> AccessScope:
        resolved_user = (user_id or self.DEFAULT_USER).strip() or self.DEFAULT_USER
        roles = self.USER_ROLES.get(resolved_user, ())
        databases: set[str] = set()
        tables: set[str] = set()
        for role in roles:
            policy = self.ROLE_POLICIES[role]
            databases.update(policy["databases"])
            tables.update(policy["tables"])
        return AccessScope(
            user_id=resolved_user,
            roles=roles,
            allowed_databases=frozenset(databases),
            allowed_tables=frozenset(tables),
        )

    @staticmethod
    def filter_schema(scope: AccessScope) -> list[dict[str, Any]]:
        return [
            table
            for table in SCHEMA
            if scope.allows_table(
                str(table.get("database") or "askdata_mock"),
                str(table["id"]),
            )
        ]
