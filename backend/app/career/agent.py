from __future__ import annotations

import json
import uuid
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import CareerReport, JobProfile, ResumeEvidence, SkillCoverageItem, TargetJobRequest
from ..model_client import ModelClient
from ..query_runtime import QueryTimeoutError
from ..rag.models import RetrievedJd
from .profile import JobProfileBuilder


class CareerAgentError(ValueError):
    """求职分析失败，不返回伪造的覆盖结论。"""


class JobRequirementAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=12000)


class CareerAgent:
    """只处理简历证据和岗位画像，不抓网页、不编写 SQL。"""

    def __init__(
        self,
        model_client: ModelClient | None = None,
        *,
        profile_builder: JobProfileBuilder | None = None,
        use_llm: bool = False,
    ) -> None:
        self.model_client = model_client
        self.profile_builder = profile_builder or JobProfileBuilder()
        self.use_llm = use_llm

    def build_profile(self, target: TargetJobRequest) -> JobProfile:
        return self.profile_builder.build(target)

    def analyze(
        self,
        resume: ResumeEvidence,
        profile: JobProfile,
        *,
        rag_documents: list[RetrievedJd] | None = None,
    ) -> CareerReport:
        if not resume.skills and not resume.projects:
            raise CareerAgentError("简历没有提取到技能或项目证据，不能生成伪匹配报告")

        resume_by_skill = {}
        for mention in resume.skills:
            resume_by_skill.setdefault(mention.skill, []).append(mention)
        project_skills = {
            skill
            for project in resume.projects
            for skill in project.skills
        }
        coverage: list[SkillCoverageItem] = []
        for item in profile.skills:
            mentions = resume_by_skill.get(item.skill, [])
            resume_evidence = [mention.evidence for mention in mentions[:3]]
            if item.skill not in resume_by_skill:
                status = "missing"
            elif item.skill in project_skills:
                status = "covered"
            else:
                status = "partial"
            coverage.append(
                SkillCoverageItem(
                    skill=item.skill,
                    category=item.category,
                    status=status,
                    requirement=item.requirement,
                    jd_evidence=item.jd_evidence,
                    resume_evidence=resume_evidence,
                    confidence=max([mention.confidence for mention in mentions], default=0.0),
                    extractor_version=item.extractor_version,
                    jd_occurrence_rate=item.occurrence_rate,
                )
            )

        counts = Counter(item.status for item in coverage)
        denominator = len(coverage)
        facts = [
            f"目标岗位：{profile.target.keyword} / {profile.target.city or '未限定城市'}",
            f"岗位样本：{profile.sample_size} 份，其中有效 JD {profile.valid_jd_count} 份",
            f"技能画像：{denominator} 项；出现率分母为有效 JD 数，而不是原始词频",
            f"覆盖结论：已覆盖 {counts['covered']} 项，部分覆盖 {counts['partial']} 项，缺口 {counts['missing']} 项",
        ]
        if profile.small_sample_warning:
            facts.append(profile.small_sample_warning)
        facts.append("薪资分位数统一为月度等价 K；年薪和多薪按年化总额除以 12，日薪按 22 天/月估算。")
        if rag_documents:
            facts.append(
                f"JD RAG：从当前数据集检索并重排了 {len(rag_documents)} 段岗位证据；仅作为本次分析的可追溯上下文。"
            )

        summary = (
            f"已根据 {profile.valid_jd_count} 份有效 JD 和简历结构化证据完成分析。"
            f"目标技能中已覆盖 {counts['covered']} 项，部分覆盖 {counts['partial']} 项，"
            f"缺口 {counts['missing']} 项。"
            + (f"本次同时参考了 {len(rag_documents)} 段 JD 检索证据。" if rag_documents else "")
            + "该覆盖分析不代表录用概率。"
        )
        return CareerReport(
            report_id=f"career_{uuid.uuid4().hex[:12]}",
            title=f"{profile.target.keyword} 求职技能覆盖报告",
            summary=summary,
            resume_id=resume.resume_id,
            target=profile.target,
            job_profile=profile,
            facts=facts,
            skill_coverage=coverage,
        )

    def answer_job_requirement(
        self,
        query: str,
        profile: JobProfile,
        rag_documents: list[RetrievedJd],
        *,
        use_llm: bool | None = None,
    ) -> str:
        if not rag_documents:
            raise CareerAgentError("岗位 JD RAG 没有返回可引用的岗位证据")
        skill_lines = [
            f"- {item.skill}：{item.requirement}，出现在 {item.occurrence_rate:.0%} 的有效 JD 中"
            for item in profile.skills[:12]
        ]
        evidence_lines = [
            f"[JD {index}] {item.title or '未命名岗位'} / {item.company or '未知公司'}：{item.text[:700]}"
            for index, item in enumerate(rag_documents, start=1)
        ]
        should_use_llm = self.use_llm if use_llm is None else use_llm
        if should_use_llm:
            if self.model_client is None or not self.model_client.enabled:
                raise CareerAgentError("岗位要求综合解读需要配置主模型 LLM_API_KEY")
            profile_skills = sorted(
                profile.skills,
                key=lambda item: (
                    item.skill.casefold() not in query.casefold(),
                    -item.occurrence_rate,
                    item.skill.casefold(),
                ),
            )
            skill_summary = [
                {
                    "skill": item.skill,
                    "requirement": item.requirement,
                    "occurrence_count": item.occurrence_count,
                    "valid_jd_denominator": item.denominator,
                    "occurrence_rate": item.occurrence_rate,
                }
                for item in profile_skills[:40]
            ]
            evidence = [
                {
                    "reference": f"[JD {index}]",
                    "job_id": item.job_id,
                    "title": item.title or "未命名岗位",
                    "company": item.company or "未知公司",
                    "city": item.city,
                    "experience": item.experience,
                    "text": item.text,
                }
                for index, item in enumerate(rag_documents, start=1)
            ]
            payload = {
                "query": query,
                "target": profile.target.model_dump(mode="json"),
                "sample_scope": {
                    "job_count": profile.sample_size,
                    "valid_jd_count": profile.valid_jd_count,
                    "small_sample_warning": profile.small_sample_warning,
                },
                "profile_skill_summary": skill_summary,
                "retrieved_jd_evidence": evidence,
                "instruction": (
                    "面向求职者综合归纳检索到的岗位 JD，回答 query 中具体询问的要求。"
                    "profile_skill_summary 是当前目标样本上的统计，retrieved_jd_evidence 是可引用的原文证据。"
                    "不要逐条复述 JD，也不要只输出高频技能清单；归纳不同 JD 的共同点和差异，"
                    "解释这些要求对应的实际工作能力，并给出基于证据的求职准备建议。"
                    "清楚区分‘JD 明确要求’和‘综合解读’，解读可以推断工作含义，但必须标注为推断。"
                    "不得补造未在证据或统计中出现的语言版本、框架、年限、硬性门槛、市场结论或技能要求。"
                    "关于频率，只能使用 profile_skill_summary 中的 count/denominator；"
                    "检索到的 JD 数量不能当作全量岗位频率。没有明确证据的方面要说明当前样本未发现。"
                    "具体岗位事实就近引用 [JD n]；统计结论说明统计分母。"
                ),
            }
            try:
                raw = self.model_client.chat_json(
                    (
                        "你是 OfferAgent 的岗位要求分析顾问。请认真阅读岗位画像统计和检索到的 JD，"
                        "把证据转化成用户能理解、能用于准备求职的综合分析。"
                        "回答应优先聚焦用户问的技术点；例如询问 Python 时，具体总结 JD 明确提到的"
                        "Python 技能、框架、开发场景、工程要求和经验边界，再说明这些要求意味着什么。"
                        "如果证据只提到 Python 而未列出框架或版本，必须直说未明确列出，不能凭常识补全。"
                        "不要照抄长段 JD，不要把每个岗位拆成一条摘要。"
                        "建议使用 Markdown 标题：要求概括、JD 明确要求、综合解读、准备建议、样本说明。"
                        "保留 [JD n] 引用，并用一两句话解释综合解读来自哪些证据。"
                        "只返回严格 JSON：{\"answer\":\"Markdown回答\"}。"
                    ),
                    json.dumps(payload, ensure_ascii=False),
                )
                return JobRequirementAnswer.model_validate(raw).answer
            except QueryTimeoutError:
                raise
            except (RuntimeError, TypeError, ValueError, ValidationError) as exc:
                raise CareerAgentError(f"岗位要求 structured output 校验失败：{type(exc).__name__}") from exc

        lines = [
            f"## {profile.target.city or '目标城市'} {profile.target.keyword} 岗位要求概览",
            f"基于当前已发布岗位库中 {profile.valid_jd_count} 份有效 JD 的结构化画像和 {len(rag_documents)} 段检索证据，以下结论只代表当前样本。",
            "",
            "### 高频技能",
            *(skill_lines or ["当前样本没有提取到稳定技能项。"]),
            "",
            "### JD 证据摘要",
            *[f"- {line}" for line in evidence_lines],
            "",
            "### 使用说明",
            "样本可能受城市、经验、采集时间和 JD 完整度影响；这些要求是岗位市场证据，不等同于个人必须具备的全部条件。",
        ]
        return "\n".join(lines)

__all__ = ["CareerAgent", "CareerAgentError", "JobRequirementAnswer"]
