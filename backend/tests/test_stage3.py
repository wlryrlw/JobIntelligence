from __future__ import annotations

import io
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pypdf import PdfWriter

from app.api import routes
from app.career import CareerAgent, ResumeStore, SkillCatalog
from app.career.resume import ResumeLimits, ResumeParseError, extract_resume_evidence, parse_resume
from app.career.skills import SkillExtractionError
from app.career.profile import JobProfileBuilder
from app.contracts import TargetJobRequest
from app.models import QueryResult
from app.coordinator import Coordinator
from app.etl.pipeline import run_etl
from app.workflows.query_graph import QueryWorkflow


RESUME_TEXT = (
    "姓名：张三\n"
    "邮箱：zhangsan@example.com\n"
    "电话：13800138000\n"
    "项目：使用 Python 和 FastAPI 构建异步服务，性能提升 20%。\n"
)


def build_market(tmp_path: Path) -> tuple[Path, object]:
    database_root = tmp_path / "databases"
    report = run_etl(
        Path("data/fixtures/jobs_seed.json"),
        None,
        database_root,
        tmp_path / "raw",
    )
    return database_root, report


def test_resume_parse_redacts_pii_and_keeps_structured_evidence() -> None:
    parsed = parse_resume("resume.txt", RESUME_TEXT.encode("utf-8"))

    assert parsed.redaction_count == 3
    assert "zhangsan@example.com" not in parsed.redacted_text
    assert "13800138000" not in parsed.redacted_text
    evidence = extract_resume_evidence(parsed, "resume_123456")
    assert {item.skill for item in evidence.skills} >= {"Python", "FastAPI"}
    assert evidence.quantitative_evidence == ["20%"]
    assert all("zhangsan@example.com" not in item.quote for item in evidence.projects[0].evidence)


def test_resume_parse_rejects_unsupported_and_oversized_inputs() -> None:
    with pytest.raises(ResumeParseError, match="只支持"):
        parse_resume("resume.docx", RESUME_TEXT.encode("utf-8"))
    with pytest.raises(ResumeParseError, match="超过"):
        parse_resume(
            "resume.txt",
            b"valid enough resume text",
            ResumeLimits(max_bytes=5),
        )


def test_pdf_resume_parser_reads_text_and_enforces_page_limit() -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    from pypdf.generic import (
        ArrayObject,
        DecodedStreamObject,
        DictionaryObject,
        NameObject,
    )

    font = writer._add_object(
        DictionaryObject({
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        })
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 10 10 Td (Python FastAPI) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)

    parsed = parse_resume("resume.pdf", output.getvalue())
    assert parsed.page_count == 1
    assert "Python" in parsed.text


class JsonModelStub:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def chat_json(self, _system: str, _user: str) -> dict[str, object]:
        return self.payload


def test_skill_catalog_strict_llm_output_requires_original_jd_evidence() -> None:
    catalog = SkillCatalog()
    result = catalog.extract_jd_llm(
        "Python 必须，FastAPI 加分。",
        JsonModelStub(
            {
                "skills": [
                    {
                        "skill": "Python",
                        "canonical": "Python",
                        "category": "language",
                        "evidence": "Python 必须",
                        "confidence": 0.9,
                        "requirement": "must_have",
                    },
                    {
                        "skill": "FastAPI",
                        "canonical": "FastAPI",
                        "category": "backend",
                        "evidence": "FastAPI 加分",
                        "confidence": 0.8,
                        "requirement": "preferred",
                    },
                ]
            }
        ),
    )
    assert [item.skill for item in result] == ["Python", "FastAPI"]

    with pytest.raises(SkillExtractionError, match="不在 JD 原文"):
        catalog.extract_jd_llm(
            "需要 Python。",
            JsonModelStub(
                {
                    "skills": [
                        {
                            "skill": "Python",
                            "canonical": "Python",
                            "category": "language",
                            "evidence": "FastAPI",
                            "confidence": 0.9,
                            "requirement": "must_have",
                        }
                    ]
                }
            ),
        )


def test_job_profile_uses_valid_jd_denominator_and_salary_percentiles(tmp_path: Path) -> None:
    database_root, _ = build_market(tmp_path)
    profile = JobProfileBuilder(database_root).build(
        TargetJobRequest(keyword="AI Agent", city="上海")
    )

    assert profile.sample_size == 2
    assert profile.valid_jd_count == 2
    python_skill = next(item for item in profile.skills if item.skill == "Python")
    assert python_skill.denominator == 2
    assert python_skill.occurrence_rate == 1
    assert python_skill.jd_evidence
    assert profile.salary_p50_k is not None
    assert profile.small_sample_warning is not None


def test_career_agent_reports_evidence_backed_coverage(tmp_path: Path) -> None:
    database_root, _ = build_market(tmp_path)
    profile = JobProfileBuilder(database_root).build(
        TargetJobRequest(keyword="AI Agent", city="上海")
    )
    parsed = parse_resume("resume.txt", RESUME_TEXT.encode("utf-8"))
    evidence = extract_resume_evidence(parsed, "resume_123456")
    report = CareerAgent().analyze(evidence, profile)
    coverage = {item.skill: item for item in report.skill_coverage}

    assert coverage["Python"].status == "covered"
    assert coverage["FastAPI"].status == "covered"
    assert coverage["RAG"].status == "missing"
    assert all(item.jd_evidence for item in report.skill_coverage)
    assert all("20%" not in recommendation for recommendation in report.recommendations)
    assert report.job_profile.valid_jd_count == 2


def test_resume_store_is_owner_scoped_and_does_not_store_full_text(tmp_path: Path) -> None:
    store = ResumeStore(tmp_path / "resumes")
    response = store.save("resume.txt", RESUME_TEXT.encode("utf-8"), user_id="user-a")
    record_dir = tmp_path / "resumes" / response.resume_id
    metadata = (record_dir / "metadata.json").read_text(encoding="utf-8")
    evidence = (record_dir / "evidence.json").read_text(encoding="utf-8")

    assert "zhangsan@example.com" not in metadata
    assert "13800138000" not in evidence
    with pytest.raises(PermissionError):
        store.delete(response.resume_id, user_id="user-b")
    store.delete(response.resume_id, user_id="user-a")
    assert not record_dir.exists()


def test_career_workflow_returns_completed_report(tmp_path: Path) -> None:
    database_root, _ = build_market(tmp_path)
    store = ResumeStore(tmp_path / "resumes")
    response = store.save("resume.txt", RESUME_TEXT.encode("utf-8"), user_id="user-a")
    workflow = object.__new__(QueryWorkflow)
    workflow.coordinator = Coordinator(dataset_root=database_root)
    workflow.resume_store = store
    workflow.career_agent = CareerAgent(profile_builder=JobProfileBuilder(database_root))
    state = {
        "task_id": "task-stage3",
        "session_id": "session-stage3",
        "query": "分析上海 AI Agent 岗位和我的技能缺口",
        "resume_id": response.resume_id,
        "target": {"keyword": "AI Agent", "city": "上海"},
        "access_scope": {"user_id": "user-a"},
        "intent": {"action": "career_advice", "reason": "测试"},
    }

    payload = workflow._coordinate_career(state)
    result = QueryResult.model_validate(payload["result"])
    assert result.status == "completed"
    assert result.career_report is not None
    assert result.career_report.resume_id == response.resume_id
    assert payload["workflow_mode"] == "career_agent"


def test_resume_upload_and_delete_api_are_explicit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "resume_store", ResumeStore(tmp_path / "resumes"))
    client = TestClient(app)

    uploaded = client.post(
        "/api/resumes",
        files={"file": ("resume.txt", RESUME_TEXT.encode("utf-8"), "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    resume_id = uploaded.json()["resume_id"]
    assert "zhangsan@example.com" not in uploaded.text

    deleted = client.delete(f"/api/resumes/{resume_id}")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": True, "resume_id": resume_id}


def test_query_api_forwards_explicit_career_context(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: dict[str, object] = {}

    class StubService:
        def submit(
            self,
            query: str,
            session_id: str,
            workspace: dict[str, object] | None,
            user_id: str,
            *,
            resume_id: str | None = None,
            target: TargetJobRequest | None = None,
        ) -> QueryResult:
            calls.update(
                query=query,
                session_id=session_id,
                workspace=workspace,
                user_id=user_id,
                resume_id=resume_id,
                target=target,
            )
            return QueryResult(
                task_id="task-career-context",
                status="completed",
                route="career_advice",
                message="ok",
            )

    app = FastAPI()
    app.include_router(routes.router)
    monkeypatch.setattr(routes, "service", StubService())
    response = TestClient(app).post(
        "/api/query",
        json={
            "query": "请分析我的 AI Agent 简历",
            "session_id": "career-context",
            "resume_id": "resume_123456",
            "target": {"keyword": "AI Agent", "city": "上海"},
        },
    )
    assert response.status_code == 200, response.text
    assert calls["resume_id"] == "resume_123456"
    assert calls["target"] == TargetJobRequest(keyword="AI Agent", city="上海")
