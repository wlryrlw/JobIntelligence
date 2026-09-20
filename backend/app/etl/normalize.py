from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime

from .models import Job, JobDetail, JobSkill, SourceJob
from .salary import parse_salary


EXTRACTOR_VERSION = "tags-v1"
ALIASES = {
    "python": "Python", "python3": "Python", "py": "Python", "java": "Java",
    "javascript": "JavaScript", "js": "JavaScript", "typescript": "TypeScript", "ts": "TypeScript",
    "c++": "C++", "c#": "C#", "golang": "Go", "go": "Go", "sql": "SQL",
    "pytorch": "PyTorch", "torch": "PyTorch", "tensorflow": "TensorFlow",
    "langchain": "LangChain", "langgraph": "LangGraph", "fastapi": "FastAPI",
    "docker": "Docker", "kubernetes": "Kubernetes", "k8s": "Kubernetes",
    "mysql": "MySQL", "redis": "Redis", "postgresql": "PostgreSQL",
    "rag": "RAG", "检索增强生成": "RAG", "llm": "LLM", "大语言模型": "LLM",
    "大模型": "LLM", "agent": "Agent", "aiagent": "Agent", "智能体": "Agent",
    "linux": "Linux", "git": "Git", "vue": "Vue", "vue3": "Vue", "react": "React",
}
DEGREES = {"学历不限", "初中及以下", "中专", "高中", "大专", "本科", "硕士", "博士"}
EXPERIENCES = {"经验不限", "应届", "在校", "1年以内", "10年以上"}


def split_values(value: str | list[str]) -> list[str]:
    values = [value] if isinstance(value, str) else value
    return list(dict.fromkeys(part.strip() for item in values for part in re.split(r"[|｜]", item) if part.strip()))


def canonical_skill(text: str) -> str:
    key = re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).casefold()
    return ALIASES.get(key, text.strip())


def degree_tag(tag: str) -> str | None:
    normalized = tag.removesuffix("及以上").removesuffix("以上")
    return normalized if normalized in DEGREES else None


def experience_tag(tag: str) -> str | None:
    normalized = {"应届生": "应届", "应届毕业生": "应届", "在校生": "在校", "在校/应届": "在校/应届"}.get(tag, tag)
    if normalized in EXPERIENCES or normalized == "在校/应届" or re.fullmatch(r"\d+-\d+年", normalized):
        return normalized
    return None


def identity(source: SourceJob) -> str:
    link = (source.job_link or source.link).strip()
    expected = hashlib.md5(link.encode()).hexdigest()[:16] if link else ""
    return source.job_id.strip() or expected


def make_job(source: SourceJob, timestamp: datetime, keyword: str, version: str) -> Job:
    tags = split_values(source.tags or source.tags_list)
    location = [p.strip() for p in re.split(r"[·•]", source.location)]
    return Job(
        **parse_salary(source.salary).model_dump(), job_id=identity(source), title=source.title.strip(),
        company=source.company.strip(), city=location[0], district=location[1] if len(location) > 1 else "",
        location_raw=source.location, tags=tags,
        skills=list(dict.fromkeys(canonical_skill(x) for x in split_values(source.skills))),
        welfare=split_values(source.welfare),
        experience=next((v for tag in tags if (v := experience_tag(tag))), "未知"),
        degree=next((v for tag in tags if (v := degree_tag(tag))), "未知"),
        company_scale=source.company_scale, company_stage=source.company_stage,
        company_industry=source.company_industry, boss_active_status=source.boss_active_status,
        job_link=(source.job_link or source.link).strip(), keyword=keyword, salary_source=source.salary_source,
        first_seen_at=timestamp, last_seen_at=timestamp, latest_scraped_at=timestamp, dataset_version=version,
    )


def merge_job(old: Job, new: Job) -> Job:
    newer, older = (new, old) if new.latest_scraped_at >= old.latest_scraped_at else (old, new)
    data = newer.model_dump()
    # 缺失新字段不能擦掉旧详情；已提供的新值（包括面议/未知薪资）必须保留。
    for name in ("title", "company", "city", "district", "location_raw", "company_scale", "company_stage",
                 "company_industry", "boss_active_status", "job_link", "keyword", "salary_source"):
        data[name] = data[name] or getattr(older, name)
    for name in ("tags", "skills", "welfare"):
        data[name] = data[name] or getattr(older, name)
    for name in ("degree", "experience"):
        if data[name] == "未知":
            data[name] = getattr(older, name)
    if not newer.salary_raw.strip():
        data.update({name: getattr(older, name) for name in type(parse_salary("")).model_fields})
    data.update(first_seen_at=min(old.first_seen_at, new.first_seen_at),
                last_seen_at=max(old.last_seen_at, new.last_seen_at), dataset_version=new.dataset_version)
    return Job.model_validate(data)


def make_detail(source: SourceJob, timestamp: datetime) -> JobDetail:
    jd = source.jd.strip()
    return JobDetail(job_id=identity(source), captured_at=source.captured_at or timestamp,
                     jd_text=jd, jd_hash=hashlib.sha256(jd.encode()).hexdigest(),
                     detail_status="complete" if jd else "missing", company=source.company,
                     skill_tags=split_values(source.skill_tags))


def extract_tags(source: SourceJob, timestamp: datetime, run_id: str, *, detail: bool) -> list[JobSkill]:
    tags = split_values(source.skill_tags) if detail else split_values(source.skills) + split_values(source.tags or source.tags_list)
    result = []
    for tag in dict.fromkeys(tags):
        if degree_tag(tag) or experience_tag(tag):
            continue
        skill = canonical_skill(tag)
        # 未知 skills/skill_tags 保留；混合 tags 中只抽已知技能，避免把招聘标签当技术能力。
        if not detail and tag not in split_values(source.skills) and skill not in ALIASES.values():
            continue
        result.append(JobSkill(job_id=identity(source), skill_canonical=skill, skill_display=tag,
                               category="technology" if skill in ALIASES.values() else "other",
                               source="detail_tags" if detail else "list_tags", evidence_text=tag,
                               confidence=1.0, extractor_version=EXTRACTOR_VERSION,
                               extracted_at=source.captured_at or timestamp, run_id=run_id))
    return result
