# 数据查询Skill

## 适用范围

用于根据字段级Schema图生成并执行单数据库查询。必须从输入的MCP工具列表中选择工具，不能绕过MCP执行数据库查询。

## 输入

- 用户独立问题
- 目标数据库
- Schema图和字段检索结果
- 用户确认字段与参数
- 当前用户可用的MCP工具列表
- 已完成的工具调用结果

## 执行要求

1. 相对时间会影响SQL时，先调用时间工具获得明确日期。
2. 信息充分后生成一条DuckDB `SELECT`或`WITH`，调用当前数据库对应的`query_*`工具。
3. 同一数据库需要的数据必须在一条SQL中查完。
4. 只能使用Schema图中的表、字段和关联，明确列出返回字段，不使用`SELECT *`。
   生成SQL时使用表的`name`和字段的`sql_name`，`id`仅用于Schema内部唯一标识。
5. 不自行补充会改变业务口径的条件；缺失信息会实质改变结果时，才向用户询问。
6. 输出字段使用简短中文别名，最多返回200行。

## 输出格式

调用工具：

```json
{"action":"call_tool","tool_name":"...","arguments":{},"reason":"..."}
```

需要询问：

```json
{
  "action":"clarify",
  "reason":"...",
  "clarification":{
    "parameter":"...",
    "question":"...",
    "reason":"...",
    "options":[
      {"id":"...","label":"...","description":"...","recommended":true}
    ]
  }
}
```
