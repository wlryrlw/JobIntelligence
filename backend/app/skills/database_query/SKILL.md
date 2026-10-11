# 数据查询Skill

## 适用范围

用于根据字段级Schema和结构化查询意图生成单数据库只读SQL。SQL由QueryAgent统一校验并执行，MCP不是内部查询的必经层。

## 输入

- 用户独立问题
- 目标数据库
- SchemaCatalog或字段检索结果
- 用户确认的字段、数据库和参数
- 相对日期表达式

## 执行要求

1. 相对日期表达式和已确认参数会改变查询口径时，必须在SQL生成上下文中明确考虑，不调用外部工具。
2. 信息充分后生成一条DuckDB `SELECT`或`WITH`只读SQL，由QueryAgent执行。
3. 同一数据库需要的数据必须在一条SQL中查完。
4. 只能使用Schema中的表、字段和关联，明确列出返回字段，不使用`SELECT *`。
   生成SQL时使用表的`name`和字段的`sql_name`，`id`仅用于Schema内部唯一标识。
5. 不自行补充会改变业务口径的条件；缺失信息会实质改变结果时，才向用户询问。
6. 输出字段使用简短中文别名，最多返回200行。

## 输出格式

生成SQL：

```json
{"action":"generate_sql","sql":"SELECT ...","explanation":"..."}
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
