# 本地MCP链路

```text
SingleDatabaseAgent
→ LocalMcpClient.list_tools
→ MCP tools/list
→ 大模型选择工具并生成参数
→ LocalMcpClient.call_tool
→ MCP tools/call
→ 时间结果或数据库查询结果
```

`server.py`负责注册工具，`client.py`负责工具发现和调用，`tools/`包含实际工具实现。
当前使用进程内传输，不需要端口；工具发现、JSON Schema校验和调用仍经过MCP协议层。

数据库工具根据后端生成的`AccessScope`按请求注册。无权访问数据库的用户看不到对应工具；
数据库工具执行SQL时会再次校验数据库、表、只读语句、多语句和`SELECT *`规则。

启动FastAPI后，可以通过`GET /api/mcp/tools`查看模型实际收到的工具名称、描述、输入Schema、输出Schema和只读标记。
