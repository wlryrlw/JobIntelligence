from __future__ import annotations

import csv
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from app.database import DATABASE_LABELS, SCHEMA
from app.etl.models import CrawlRun, Job, JobDetail, JobSkill
from app.etl.pipeline import EtlError, read_rows, run_etl
from app.etl.salary import parse_salary
from app.etl.schema import declared_schema, generate_schema, load_market_schema
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController


@pytest.mark.parametrize("text,low,high,months,period", [
    ("40-70K·13薪",40,70,13,"month"), ("25-35K",25,35,12,"month"),
    ("1-2万/月",10,20,12,"month"), ("300-500元/天",6.6,11,12,"day"),
    ("年薪 30-50 万",25,500/12,12,"year"), ("30-50万/年",25,500/12,12,"year"),
    ("300000元/年",25,25,12,"year"), ("200-260K·24薪",200,260,24,"month"),
    ("10K",10,10,12,"month"), ("10k-20K",10,20,12,"month"),
    ("１．５－２万/月",15,20,12,"month"), ("100-200元/日",2.2,4.4,12,"day"),
    ("8000-12000元/月",8,12,12,"month"),
])
def test_salary_units(text, low, high, months, period):
    result = parse_salary(text)
    assert result.salary_parse_status == "parsed"
    assert result.salary_min_k == pytest.approx(low)
    assert result.salary_max_k == pytest.approx(high)
    assert result.salary_months == months
    assert result.salary_period == period
    assert result.salary_annual_min_k == pytest.approx(low * months)
    assert result.salary_annual_max_k == pytest.approx(high * months)
    assert result.salary_raw == text


@pytest.mark.parametrize("text,status", [
    ("薪资面议","negotiable"),("面议","negotiable"),("底薪 + 提成","unsupported"),
    ("50-80元/小时","unsupported"),("30-50万","unsupported"),("100-200","unsupported"),
    ("","invalid"),("0-10K","invalid"),("30-20K","invalid"),("20K·0薪","invalid"),
    ("20K·99薪","invalid"),("1K-2万/月","invalid"),("年薪30万/月","invalid"),
    ("100元/天·13薪","invalid"),("负10K","unsupported"),
])
def test_salary_failure_retains_raw(text, status):
    result = parse_salary(text)
    assert result.salary_parse_status == status
    assert result.salary_raw == text
    assert result.salary_min_k is None and result.salary_annual_max_k is None
    assert result.is_negotiable == (status == "negotiable")


def source(tmp_path: Path, *, day=1, rows=None, details=None, status="unknown") -> Path:
    payload = {"scraped_at": f"2026-09-{day:02d}T12:00:00+08:00", "keyword": "AI", "city": "上海", "status":status,
               "jobs": rows if rows is not None else [{"job_id":"one","title":"工程师","salary":"20-30K","salary_source":"fixture","location":"上海·徐汇区","tags":"1-3年 | 本科 | Python3","skills":"python | Python3 | RAG","welfare":"年假｜年假|五险"}],
               "details": details or []}
    path = tmp_path / f"source-{day}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def etl(tmp_path, path):
    return run_etl(path, None, tmp_path / "databases", tmp_path / "raw")


def test_pipeline_schema_types_evidence_and_idempotence(tmp_path):
    path=source(tmp_path, details=[{"job_id":"one","company":"公司甲","jd":"Python 用于开发\n引号\"和逗号,测试", "skill_tags":["Python3","RAG"]}])
    report=etl(tmp_path,path)
    folder=tmp_path/"databases/jobs_market"
    first=folder.resolve()
    assert etl(tmp_path,path)==report
    assert folder.resolve()==first
    jobs=read_rows(folder/"jobs.csv",Job)
    assert len(jobs)==1 and jobs[0].company=="公司甲"
    assert jobs[0].skills==["Python","RAG"]
    assert jobs[0].welfare==["年假","五险"]
    assert jobs[0].degree=="本科" and jobs[0].experience=="1-3年"
    assert jobs[0].first_seen_at.hour==4
    skills=read_rows(folder/"job_skills.csv",JobSkill)
    assert {s.source for s in skills}=={"list_tags","detail_tags"}
    assert all(s.evidence_text and s.run_id==report.run_id for s in skills)
    detail=read_rows(folder/"job_details.csv",JobDetail)[0]
    assert '\n' in detail.jd_text and len(detail.jd_hash)==64
    schema=load_market_schema(folder)
    for table in schema.tables:
        with (folder/f'{table.name}.csv').open(newline='') as f:
            assert next(csv.reader(f))==[c.name for c in table.fields]
    engine=DuckDbEngine(tmp_path/"databases")
    with engine.connect("jobs_market") as con:
        assert con.execute('SELECT salary_min_k, is_negotiable FROM jobs').fetchone()==(20.0,False)
        actual={r[0]:r[1] for r in con.execute('DESCRIBE jobs').fetchall()}
        assert actual=={f.name:f.source_type for f in schema.tables[0].fields}
    run=read_rows(folder/"crawl_runs.csv",CrawlRun)[0]
    assert run.requested_pages is None and run.source_status=="unknown"
    assert (tmp_path/"raw"/report.run_id/"jobs.json").read_bytes()==path.read_bytes()


def test_dirty_rows_duplicates_orphans_and_unknown_salary(tmp_path):
    path=source(tmp_path, rows=[{"job_id":"one","salary":"底薪+提成"},{"job_id":"one","title":"最新"}, {"title":"没有标识"},{"job_id":"bad","salary":12}], details=[{"job_id":"ghost","jd":"无对应岗位"},{"jd":"无标识"}])
    report=etl(tmp_path,path)
    assert (report.rejected_jobs,report.duplicate_jobs,report.total_jobs)==(2,1,1)
    assert report.salary_failure_count==1 and report.salary_status_counts=={"unsupported":1}
    assert report.orphan_details==1 and report.rejected_details==1
    job=read_rows(tmp_path/"databases/jobs_market/jobs.csv",Job)[0]
    assert job.salary_raw=="底薪+提成" and job.title=="最新"
    with DuckDbEngine(tmp_path/"databases").connect('jobs_market') as con:
        assert con.execute('SELECT typeof(salary_min_k), salary_min_k FROM jobs').fetchone()==('DOUBLE',None)
        assert con.execute('SELECT COUNT(*) FROM job_details').fetchone()==(0,)
        assert dict((r[0],r[1]) for r in con.execute('DESCRIBE job_details').fetchall())['captured_at']=='TIMESTAMP'


def test_incremental_keeps_missing_jobs_and_history_out_of_order(tmp_path):
    early=source(tmp_path,day=1,details=[{"job_id":"one","jd":"最早详情","company":"旧公司"}])
    a=etl(tmp_path,early)
    later=source(tmp_path,day=3,rows=[{"job_id":"one","salary":"面议","title":"新岗位"},{"job_id":"two","salary":"20K"}],details=[{"job_id":"one","jd":"最新详情","company":"新公司"}])
    b=etl(tmp_path,later)
    middle=source(tmp_path,day=2,rows=[{"job_id":"one","salary":"10K","title":"过时岗位"}],details=[{"job_id":"one","jd":"","company":"过时公司"}])
    c=etl(tmp_path,middle)
    assert len({a.dataset_version,b.dataset_version,c.dataset_version})==3
    folder=tmp_path/'databases/jobs_market'
    jobs={j.job_id:j for j in read_rows(folder/'jobs.csv',Job)}
    assert len(jobs)==2 and jobs['one'].title=='新岗位'
    assert jobs['one'].is_negotiable and jobs['one'].company=='新公司'
    assert jobs['one'].first_seen_at.day==1 and jobs['one'].last_seen_at.day==3
    assert all(j.dataset_version==c.dataset_version for j in jobs.values())
    assert read_rows(folder/'job_details.csv',JobDetail)[0].jd_text=='最新详情'
    assert len(read_rows(folder/'crawl_runs.csv',CrawlRun))==3
    before=folder.resolve()
    assert etl(tmp_path,early)==a and folder.resolve()==before


def test_failed_publication_does_not_change_current(tmp_path):
    etl(tmp_path,source(tmp_path))
    folder=tmp_path/'databases/jobs_market'
    first=folder.resolve()
    with patch('app.etl.pipeline.generate_schema',side_effect=ValueError('模拟schema失败')):
        with pytest.raises(EtlError):
            etl(tmp_path,source(tmp_path,day=2))
    assert folder.resolve()==first
    assert len(list((tmp_path/'raw').glob('*/failure.json')))==1
    assert etl(tmp_path,source(tmp_path,day=2)).dataset_version
    assert folder.resolve()!=first


@pytest.mark.parametrize('status',['failed','blocked','cancelled'])
def test_failed_crawl_is_not_published(tmp_path,status):
    with pytest.raises(EtlError,match='源采集状态'):
        etl(tmp_path,source(tmp_path,status=status))
    assert not (tmp_path/'databases/jobs_market').exists()


def test_corrupt_snapshot_and_schema_are_rejected(tmp_path):
    etl(tmp_path,source(tmp_path))
    folder=tmp_path/'databases/jobs_market'
    with (folder/'jobs.csv').open('a') as f:
        f.write('corrupt\n')
    with pytest.raises(EtlError,match='校验失败'):
        etl(tmp_path,source(tmp_path,day=2))


def test_missing_identity_and_conflicting_links(tmp_path):
    with pytest.raises(EtlError,match='无有效岗位'):
        etl(tmp_path,source(tmp_path,rows=[{"title":"x"}]))
    with pytest.raises(EtlError,match='不同 job_link'):
        etl(tmp_path,source(tmp_path,rows=[{"job_id":"x","job_link":"https://example.test/a"},{"job_id":"x","job_link":"https://example.test/b"}]))


def test_link_identity_and_external_details(tmp_path):
    jobs=source(tmp_path,rows=[{"job_link":"https://example.test/a","salary":"20K"}])
    details=tmp_path/'details.json'
    details.write_text(json.dumps([{"link":"https://example.test/a","jd":"合成详情"}]))
    report=run_etl(jobs,details,tmp_path/'databases',tmp_path/'raw')
    assert report.missing_detail_count==0
    assert len(read_rows(tmp_path/'databases/jobs_market/jobs.csv',Job)[0].job_id)==16


def test_sql_scope_and_read_only(tmp_path):
    etl(tmp_path,source(tmp_path))
    engine=DuckDbEngine(tmp_path/'databases')
    scope=AccessController().resolve('local_user')
    assert scope.allowed_databases==frozenset({'jobs_market'})
    assert DATABASE_LABELS['jobs_market']=='岗位市场数据源'
    assert len([t for t in SCHEMA if t.get('database')=='jobs_market'])==4
    assert engine.execute('jobs_market','SELECT COUNT(job_id) AS n FROM jobs',scope).rows==[{'n':1}]
    assert engine.execute('jobs_market','WITH counts AS (SELECT COUNT(job_id) AS n FROM jobs) SELECT n FROM counts',scope).success
    for sql in ['SELECT * FROM jobs','DELETE FROM jobs','SELECT job_id FROM jobs; SELECT job_id FROM jobs',
                "SELECT content FROM read_text('/etc/hosts')", "SELECT job_id FROM read_csv('jobs.csv')",'SELECT job_id FROM main.jobs',
                "SELECT content FROM query('SELECT content FROM read_text(\'/etc/hosts\')')"]:
        assert not engine.execute('jobs_market',sql,scope).success, sql
    assert not engine.execute('jobs_market','SELECT job_id FROM jobs',AccessController().resolve('unknown-user')).success


def test_synthetic_seed_is_replayable(tmp_path):
    fixture=Path(__file__).resolve().parents[2]/'data/fixtures/jobs_seed.json'
    report=etl(tmp_path,fixture)
    assert report.total_jobs==6 and report.salary_failure_count==1
    with DuckDbEngine(tmp_path/'databases').connect('jobs_market') as con:
        assert con.execute("SELECT salary_min_k FROM jobs WHERE job_id='synthetic-003'").fetchone()==(25.0,)


def test_writer_lock_and_readers_pin_version(tmp_path):
    import fcntl

    etl(tmp_path,source(tmp_path))
    engine=DuckDbEngine(tmp_path/'databases')
    with engine.connect('jobs_market') as reader:
        with (tmp_path/'databases/.jobs_market.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            with pytest.raises(EtlError,match='另一个 ETL'):
                etl(tmp_path,source(tmp_path,day=2))
        etl(tmp_path,source(tmp_path,day=2,rows=[{'job_id':'one','salary':'40K'}]))
        assert reader.execute('SELECT salary_min_k FROM jobs').fetchone()==(20.0,)
    with engine.connect('jobs_market') as reader:
        assert reader.execute('SELECT salary_min_k FROM jobs').fetchone()==(40.0,)


def test_schema_column_tampering_fails_before_query(tmp_path):
    etl(tmp_path,source(tmp_path))
    path=tmp_path/'databases/jobs_market/jobs.csv'
    path.write_text(path.read_text().replace('salary_min_k,','unexpected_column,',1))
    with pytest.raises(ValueError,match='CSV 列与 Schema'):
        load_market_schema(path.parent)
    assert not DuckDbEngine(tmp_path/'databases').execute('jobs_market','SELECT job_id FROM jobs').success


def test_invalid_json_is_archived_without_publishing(tmp_path):
    path=tmp_path/'invalid.json'
    path.write_text('{broken json')
    with pytest.raises(EtlError):
        etl(tmp_path,path)
    assert not (tmp_path/'databases/jobs_market').exists()
    assert len(list((tmp_path/'raw').glob('*/failure.json')))==1
    assert next((tmp_path/'raw').glob('*/jobs.json')).read_text()=='{broken json'
