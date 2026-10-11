from __future__ import annotations

import json
import uuid
from collections import Counter

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ..contracts import CareerReport, JobProfile, ResumeEvidence, SkillCoverageItem, TargetJobRequest
from ..model_client import ModelClient
from ..query_runtime import QueryTimeoutError
from .profile import JobProfileBuilder


class CareerAgentError(ValueError):
    """求职分析失败，不返回伪造的覆盖结论。"""


class CareerAdvice(BaseModel):
    model_config = ConfigDict(extra="forbid")

    recommendations: list[str] = Field(default_factory=list, max_length=20)
    rewrite_directions: list[str] = Field(default_factory=list, max_length=20)
    interview_checklist: list[str] = Field(default_factory=list, max_length=20)


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

    def analyze(self, resume: ResumeEvidence, profile: JobProfile) -> CareerReport:
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
            advice = self._llm_advice(resume, profile, coverage)
            recommendations = advice.recommendations or recommendations
            rewrite_directions = advice.rewrite_directions or rewrite_directions
            interview_checklist = advice.interview_checklist or interview_checklist

        summary = (
            f"已根据 {profile.valid_jd_count} 份有效 JD 和简历结构化证据完成分析。"
            f"目标技能中已覆盖 {counts['covered']} 项，部分覆盖 {counts['partial']} 项，"
            f"缺口 {counts['missing']} 项。该覆盖分析不代表录用概率。"
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

    def _llm_advice(
        self,
        resume: ResumeEvidence,
        profile: JobProfile,
        coverage: list[SkillCoverageItem],
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
