# 本地数据库 MCP 边界

```text
外部调用方
→ LocalMcpClient.list_tools / call_tool
→ MCP tools/list / tools/call
→ query_<database>
→ DuckDbEngine
→ AccessScope + SQLGlot + DuckDB
```

`server.py`负责注册数据库工具，`client.py`负责可选的进程内 MCP 适配，`tools/`只包含数据库查询适配器。
当前使用进程内传输，不需要端口；工具发现、JSON Schema校验和调用仍经过MCP协议层。

正常的 JobIntel 查询不再为了使用 MCP 而经过 MCP client，而是由 `QueryAgent` 直接调用
`SchemaCatalogBuilder` 和 `DuckDbEngine`。MCP 只保留数据库能力，作为外部工具和数据库边界适配器。

数据库工具根据后端生成的`AccessScope`按请求注册。无权访问数据库的用户看不到对应工具；
数据库工具执行SQL时会再次校验数据库、表、只读语句、多语句和`SELECT *`规则。

启动FastAPI后，可以通过`GET /api/mcp/tools`查看当前用户可用的数据库工具名称、描述、输入Schema、输出Schema和只读标记。
