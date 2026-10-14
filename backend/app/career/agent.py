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


class CareerAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendations: list[str] = Field(default_factory=list, max_length=20)
    rewrite_directions: list[str] = Field(default_factory=list, max_length=20)
    interview_checklist: list[str] = Field(default_factory=list, max_length=20)


class JobRequirementAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1, max_length=12000)


class CareerAgent:
    """只处理简历证据、岗位画像和建议，不抓网页、不编写 SQL。"""

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
                suggestion = f"补足 {item.skill} 的真实项目或学习证据，再考虑写入简历。"
            elif item.skill in project_skills:
                status = "covered"
                suggestion = "保留现有项目证据，并核对技术动作和结果是否与 JD 表述一致。"
            else:
                status = "partial"
                suggestion = f"已有 {item.skill} 提及；建议补充真实项目中的职责、技术动作和可验证结果。"
            coverage.append(
                SkillCoverageItem(
                    skill=item.skill,
                    category=item.category,
                    status=status,
                    requirement=item.requirement,
                    jd_evidence=item.jd_evidence,
                    resume_evidence=resume_evidence,
                    suggestion=suggestion,
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
        if rag_documents:
            facts.append(
                f"JD RAG：从当前数据集检索并重排了 {len(rag_documents)} 段岗位证据；仅作为本次分析的可追溯上下文。"
            )
        recommendations = [
            item.suggestion
            for item in coverage
            if item.suggestion and item.status != "covered"
        ]
        rewrite_directions = [
            f"只有在确有真实经历时，为 {item.skill} 补充项目背景、你的具体动作和可验证结果；没有量化证据时不要编造。"
            for item in coverage
            if item.status in {"partial", "missing"}
        ]
        interview_checklist = [
            f"准备一个真实例子说明你如何使用 {item.skill}，并能解释个人职责和结果。"
            for item in coverage
            if item.status in {"covered", "partial"}
        ]
        if self.use_llm:
            advice = self._llm_advice(resume, profile, coverage, rag_documents or [])
            recommendations = advice.recommendations or recommendations
            rewrite_directions = advice.rewrite_directions or rewrite_directions
            interview_checklist = advice.interview_checklist or interview_checklist

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
            recommendations=list(dict.fromkeys(recommendations))[:20],
            skill_coverage=coverage,
            rewrite_directions=list(dict.fromkeys(rewrite_directions))[:20],
            interview_checklist=list(dict.fromkeys(interview_checklist))[:20],
        )

    def answer_job_requirement(
        self,
        query: str,
        profile: JobProfile,
        rag_documents: list[RetrievedJd],
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
        if self.use_llm:
            if self.model_client is None:
                raise CareerAgentError("启用岗位要求 LLM 整理时必须提供 ModelClient")
            payload = {
                "query": query,
                "target": profile.target.model_dump(mode="json"),
                "profile_skills": skill_lines,
                "retrieved_jd_evidence": evidence_lines,
                "instruction": "只能根据给定 JD 证据总结要求；无法由证据支持的内容必须明确说未发现，不能补造市场事实。",
            }
            try:
                raw = self.model_client.chat_json(
                    "你是岗位要求分析器。只返回严格 JSON：{\"answer\":\"Markdown回答\"}。"
                    "将岗位要求分为核心技能、工作内容、经验与学历、加分项，并在结论中保留 [JD n] 引用。",
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

    def _llm_advice(
        self,
        resume: ResumeEvidence,
        profile: JobProfile,
        coverage: list[SkillCoverageItem],
        rag_documents: list[RetrievedJd],
    ) -> CareerAdvice:
        if self.model_client is None:
            raise CareerAgentError("启用建议层 LLM 时必须提供 ModelClient")
        payload = {
            "resume_id": resume.resume_id,
            "facts": [
                {
                    "skill": item.skill,
                    "status": item.status,
                    "requirement": item.requirement,
                    "jd_evidence": [evidence.quote for evidence in item.jd_evidence],
                    "resume_evidence": [evidence.quote for evidence in item.resume_evidence],
                }
                for item in coverage
            ],
            "instruction": "只组织建议语言，不添加输入中不存在的经历、技术、指标或结果。",
            "retrieved_jd_evidence": [
                {
                    "title": item.title,
                    "company": item.company,
                    "quote": item.text[:700],
                }
                for item in rag_documents[:8]
            ],
        }
        system = (
            "你是求职建议语言组织器。只返回严格 JSON："
            '{"recommendations":[],"rewrite_directions":[],"interview_checklist":[]}'
            "。事实已由程序计算，你不能新增事实；STAR 改写只能使用给定证据。"
        )
        try:
            raw = self.model_client.chat_json(
                system,
                json.dumps(payload, ensure_ascii=False),
            )
            return CareerAdvice.model_validate(raw)
        except QueryTimeoutError:
            raise
        except (RuntimeError, TypeError, ValueError, ValidationError) as exc:
            raise CareerAgentError(f"建议层 structured output 校验失败：{type(exc).__name__}") from exc


__all__ = ["CareerAdvice", "CareerAgent", "CareerAgentError"]
