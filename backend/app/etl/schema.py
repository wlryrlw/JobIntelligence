from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Literal

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from .models import TABLE_MODELS


LABELS = dict(item.split(":", 1) for item in """
salary_raw:原始薪资 salary_min_k:月薪下限K salary_max_k:月薪上限K salary_months:薪资月数
salary_annual_min_k:年薪下限K salary_annual_max_k:年薪上限K is_negotiable:是否面议
salary_unit:原始薪资单位 salary_period:原始计薪周期 salary_parse_status:薪资解析状态 salary_conversion_rule:薪资换算规则
job_id:岗位编号 title:岗位名称 company:公司名称 city:城市 district:区县 location_raw:原始工作地点
tags:岗位标签 skills:规范技能 welfare:福利 experience:经验要求 degree:学历要求 company_scale:公司规模
company_stage:融资阶段 company_industry:公司行业 boss_active_status:招聘者活跃状态 job_link:岗位链接 keyword:搜索关键词
salary_source:薪资来源 first_seen_at:首次出现时间 last_seen_at:最近出现时间 latest_scraped_at:最近采集时间
dataset_version:数据集版本 captured_at:详情采集时间 jd_text:职位描述 jd_hash:职位描述哈希 detail_status:详情状态
skill_tags:详情技能标签 skill_canonical:标准技能名 skill_display:原始技能名 category:技能类别 source:证据来源
evidence_text:技能证据 confidence:标签抽取置信度 extractor_version:抽取器版本 extracted_at:抽取时间
run_id:采集批次 requested_pages:请求页数 completed_pages:完成页数 status:导入状态 source_status:源采集状态
job_count:批次岗位数 detail_count:批次详情数 started_at:源采集时间 finished_at:导入完成时间
failure_code:失败码 scraper_version:采集器版本 etl_version:ETL版本 source_hash:输入内容哈希
""".split())
TABLE_LABELS = {"jobs": "岗位市场快照", "job_details": "岗位详情", "job_skills": "技能证据", "crawl_runs": "采集与导入批次"}


class Profile(BaseModel):
    row_count: int = 0
    null_count: int = 0
    distinct_count: int = 0
    sample_values: list[str] = Field(default_factory=list)


class ColumnSchema(BaseModel):
    model_config = ConfigDict(extra="allow")
    name: str
    label: str
    type: str
    source_type: Literal["VARCHAR", "DOUBLE", "BIGINT", "BOOLEAN", "TIMESTAMP"]
    description: str
    aliases: list[str] = Field(default_factory=list)
    role: str = "dimension"
    aggregation: str = "group"
    data_profile: Profile = Field(default_factory=Profile)


class TableSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    name: str
    label: str
    database: str = "jobs_market"
    domain: str = "求职市场"
    description: str
    primary_key: list[str]
    row_count: int = 0
    fields: list[ColumnSchema]


class Relation(BaseModel):
    left_table: str
    left_field: str
    right_table: str
    right_field: str
    description: str


class MarketSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dataset_version: str
    tables: list[TableSchema]
    relations: list[Relation]


def declared_schema(version: str = "unpublished") -> MarketSchema:
    tables = []
    for name, model in TABLE_MODELS.items():
        fields = []
        for key, info in model.model_json_schema()["properties"].items():
            value = next((v for v in info.get("anyOf", [info]) if v.get("type") != "null"), info)
            physical = {"integer": "BIGINT", "number": "DOUBLE", "boolean": "BOOLEAN"}.get(value.get("type"), "VARCHAR")
            if value.get("format") == "date-time":
                physical = "TIMESTAMP"
            label = LABELS[key]
            description = label
            if key.endswith("_k"):
                description += "；单位千元人民币；日薪按22天估算，请按salary_period和salary_conversion_rule分组统计"
            if value.get("type") == "array":
                description += "；JSON数组文本，不是管道符拼接"
            if physical == "TIMESTAMP":
                description += "；统一 UTC 时间，CSV 为带+00:00的ISO字符串，DuckDB TIMESTAMP 按UTC解释"
            if key == "confidence":
                description += "；只表示标签原文提取的确定性，不代表岗位要求强度或模型置信度"
            fields.append(ColumnSchema(name=key, label=label, type={"DOUBLE": "数值", "BIGINT": "整数", "BOOLEAN": "布尔", "TIMESTAMP": "日期"}.get(physical, "文本"),
                                       source_type=physical, description=description, aliases=[label],
                                       role="metric" if physical in {"DOUBLE", "BIGINT"} else "dimension",
                                       aggregation="avg" if physical == "DOUBLE" else "group"))
        primary = ["run_id"] if name == "crawl_runs" else (["job_id", "skill_canonical", "source", "evidence_text", "extractor_version", "run_id"] if name == "job_skills" else ["job_id"])
        tables.append(TableSchema(id=f"jobs_market.{name}", name=name, label=TABLE_LABELS[name],
                                  description=TABLE_LABELS[name], primary_key=primary, fields=fields))
    relations = [Relation(left_table="jobs_market.jobs", left_field="job_id", right_table=f"jobs_market.{name}", right_field="job_id", description="岗位关联详情或技能证据") for name in ("job_details", "job_skills")]
    relations.append(Relation(left_table="jobs_market.job_skills", left_field="run_id", right_table="jobs_market.crawl_runs", right_field="run_id", description="技能证据来源批次"))
    return MarketSchema(dataset_version=version, tables=tables, relations=relations)


def csv_expression(path: Path, columns: list[ColumnSchema]) -> str:
    file = path.resolve().as_posix().replace("'", "''")
    types = ", ".join(f"'{column.name}': '{column.source_type}'" for column in columns)
    return f"read_csv('{file}', header=true, auto_detect=false, columns={{{types}}}, nullstr='', strict_mode=true)"


def generate_schema(folder: Path, version: str) -> MarketSchema:
    schema = declared_schema(version)
    tables = []
    with duckdb.connect(":memory:") as connection:
        for table in schema.tables:
            path = folder / f"{table.name}.csv"
            with path.open(newline="", encoding="utf-8") as stream:
                header = next(csv.reader(stream))
            if header != [field.name for field in table.fields]:
                raise ValueError(f"Schema 与 CSV 列不一致：{path}")
            connection.execute(f'CREATE OR REPLACE VIEW current_table AS SELECT * FROM {csv_expression(path, table.fields)}')
            count = connection.execute("SELECT COUNT(*) FROM current_table").fetchone()[0]
            fields = []
            for field in table.fields:
                nulls, distinct = connection.execute(f'SELECT COUNT(*) - COUNT("{field.name}"), COUNT(DISTINCT "{field.name}") FROM current_table').fetchone()
                # 完整 JD、证据和链接不复制到 Schema/Prompt。
                samples = [] if field.name in {"jd_text", "evidence_text", "job_link", "company", "title"} else [str(r[0])[:120] for r in connection.execute(f'SELECT DISTINCT "{field.name}" FROM current_table WHERE "{field.name}" IS NOT NULL ORDER BY 1 LIMIT 3').fetchall()]
                fields.append(field.model_copy(update={"data_profile": Profile(row_count=count, null_count=nulls, distinct_count=distinct, sample_values=samples)}))
            tables.append(table.model_copy(update={"row_count": count, "fields": fields}))
    result = schema.model_copy(update={"tables": tables})
    (folder / "_schema.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
    return result


def load_market_schema(folder: Path) -> MarketSchema:
    schema = MarketSchema.model_validate_json((folder / "_schema.json").read_text(encoding="utf-8"))
    expected = declared_schema(schema.dataset_version)
    if [(t.name, [(f.name, f.source_type) for f in t.fields]) for t in schema.tables] != [(t.name, [(f.name, f.source_type) for f in t.fields]) for t in expected.tables]:
        raise ValueError(f"岗位 Schema 与当前 ETL 契约不匹配，请重新导入：{folder}")
    for table in schema.tables:
        with (folder / f"{table.name}.csv").open(newline="", encoding="utf-8") as stream:
            if next(csv.reader(stream)) != [f.name for f in table.fields]:
                raise ValueError(f"CSV 列与 Schema 不匹配：{table.name}")
    return schema
