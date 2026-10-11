from pathlib import Path

from app.database import DATABASE_LABELS, SCHEMA
from app.etl.pipeline import run_etl
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController


def test_runtime_schema_contains_only_the_offeragent_job_database() -> None:
    assert DATABASE_LABELS == {"jobs_market": "岗位市场数据源"}
    assert {table["database"] for table in SCHEMA} == {"jobs_market"}
    assert all(table["id"].startswith("jobs_market.") for table in SCHEMA)


def test_published_job_snapshot_is_queryable_through_duckdb(tmp_path: Path) -> None:
    database_root = tmp_path / "databases"
    run_etl(
        Path(__file__).resolve().parents[2] / "data" / "fixtures" / "jobs_seed.json",
        None,
        database_root,
        tmp_path / "raw",
    )

    with DuckDbEngine(database_root).connect("jobs_market") as connection:
        count = connection.execute("SELECT COUNT(job_id) FROM jobs").fetchone()[0]

    assert count == 6
    scope = AccessController().resolve("demo_candidate")
    assert scope.allowed_databases == frozenset({"jobs_market"})
