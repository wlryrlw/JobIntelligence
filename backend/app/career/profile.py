from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from ..config import BASE_DIR
from ..contracts import (
    EvidenceRef,
    JobProfile,
    JobProfileSkill,
    SkillMention,
    TargetJobRequest,
)
from ..etl.models import Job, JobDetail
from ..etl.pipeline import read_rows
from ..model_client import ModelClient
from .skills import SkillCatalog


class JobProfileError(ValueError):
    """岗位数据不足以构建可解释画像。"""


class JobProfileBuilder:
    """从已发布岗位快照构建确定性的岗位画像。薪资分位数统一为月度等价 K。"""

    def __init__(
        self,
        database_root: Path | None = None,
        *,
        model_client: ModelClient | None = None,
        use_llm: bool = False,
    ) -> None:
        self.database_root = database_root or BASE_DIR.parent / "data" / "databases"
        self.catalog = SkillCatalog()
        self.model_client = model_client
        self.use_llm = use_llm

    def build(self, target: TargetJobRequest) -> JobProfile:
        folder = self._folder()
        required = [folder / "jobs.csv", folder / "job_details.csv", folder / "_schema.json"]
        missing = [str(path) for path in required if not path.exists()]
        if missing:
            raise JobProfileError(f"岗位数据集不存在或未发布：{', '.join(missing)}")

        jobs = read_rows(folder / "jobs.csv", Job)
        details = {item.job_id: item for item in read_rows(folder / "job_details.csv", JobDetail)}
        matched = [job for job in jobs if self._matches(job, target)]
        if not matched:
            raise JobProfileError("没有符合目标岗位关键词、城市和过滤条件的岗位样本")
        matched = list({job.job_id: job for job in matched}.values())
        valid_details = {
            job.job_id: details[job.job_id]
            for job in matched
            if job.job_id in details and details[job.job_id].jd_text.strip()
        }
        if not valid_details:
            raise JobProfileError("匹配岗位没有有效 JD 文本，不能生成带证据的技能画像")

        skill_evidence: dict[str, list[EvidenceRef]] = defaultdict(list)
        skill_categories: dict[str, str] = {}
        requirement_counts: dict[str, Counter[str]] = defaultdict(Counter)
        mentions_by_job: dict[str, list[SkillMention]] = {}
        for job_id, detail in valid_details.items():
            mentions = self.catalog.extract_jd(
                detail.jd_text,
                model_client=self.model_client,
                use_llm=self.use_llm,
            )
            mentions_by_job[job_id] = mentions
            for mention in mentions:
                if len(skill_evidence[mention.skill]) < 3:
                    skill_evidence[mention.skill].append(mention.evidence)
                skill_categories[mention.skill] = mention.category
                requirement_counts[mention.skill][mention.requirement] += 1

        denominator = len(valid_details)
        sample_note = (
            f"有效 JD 样本仅 {denominator} 份，仅供参考；不代表完整市场。"
            if denominator < 10
            else None
        )
        profile_skills: list[JobProfileSkill] = []
        for skill, evidence in skill_evidence.items():
            count = sum(
                1
                for mentions in mentions_by_job.values()
                if any(item.skill == skill for item in mentions)
            )
            extractor_versions = sorted(
                {
                    mention.extractor_version
                    for mentions in mentions_by_job.values()
                    for mention in mentions
                    if mention.skill == skill
                }
            )
            extractor_version = "+".join(extractor_versions) or "unknown"
            preferred = requirement_counts[skill]["preferred"]
            must_have = requirement_counts[skill]["must_have"]
            requirement = "preferred" if preferred > must_have else "must_have"
            profile_skills.append(
                JobProfileSkill(
                    skill=skill,
                    category=skill_categories.get(skill, "other"),
                    occurrence_count=count,
                    denominator=denominator,
                    occurrence_rate=round(count / denominator, 6),
                    requirement=requirement,
                    jd_evidence=evidence,
                    sample_size_note=sample_note,
                    extractor_version=extractor_version,
                )
            )

        salaries = [
            ((job.salary_annual_min_k + job.salary_annual_max_k) / 2) / 12
            for job in matched
            if job.salary_parse_status == "parsed"
            and job.salary_annual_min_k is not None
            and job.salary_annual_max_k is not None
        ]
        versions = sorted({job.dataset_version for job in matched if job.dataset_version})
        return JobProfile(
            target=target,
            dataset_version=versions[-1] if versions else "unknown",
            sample_size=len(matched),
            valid_jd_count=denominator,
            skills=sorted(profile_skills, key=lambda item: (-item.occurrence_rate, item.skill)),
            salary_sample_count=len(salaries),
            salary_p25_k=_percentile(salaries, 0.25),
            salary_p50_k=_percentile(salaries, 0.50),
            salary_p75_k=_percentile(salaries, 0.75),
            experience_distribution=dict(Counter(job.experience for job in matched)),
            degree_distribution=dict(Counter(job.degree for job in matched)),
            small_sample_warning=sample_note,
        )

    def _folder(self) -> Path:
        root = self.database_root.resolve()
        return root if root.name == "jobs_market" else root / "jobs_market"

    @staticmethod
    def _matches(job: Job, target: TargetJobRequest) -> bool:
        needle = target.keyword.casefold().strip()
        haystack = " ".join([job.keyword, job.title, *job.skills]).casefold()
        if needle not in haystack:
            return False
        if target.city and job.city.casefold() != target.city.casefold():
            return False
        if target.company and target.company.casefold() not in job.company.casefold():
            return False
        if target.experience and job.experience.casefold() != target.experience.casefold():
            return False
        if target.degree and job.degree.casefold() != target.degree.casefold():
            return False
        return True


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return round(ordered[lower] + (ordered[upper] - ordered[lower]) * fraction, 4)


__all__ = ["JobProfileBuilder", "JobProfileError"]
