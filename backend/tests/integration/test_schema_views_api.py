"""Migrations, constraints, permissions, analytics views and the API against the test DB."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import responses
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError

from github_fakes import FakeGitHub, FakeRepo, as_issue_stream_item, issue_item, pr_item, repo_item
from oss_health.api.main import create_app
from oss_health.api.settings import ApiSettings
from oss_health.github.client import GitHubClient
from oss_health.pipeline import run_pipeline
from oss_health.sql_files import ANALYTICS_VIEWS

from .conftest import alembic_config, role_engine

pytestmark = pytest.mark.integration


def ago(days: float = 0) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_migration_round_trip(engine, db_url):
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    with engine.connect() as conn:
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.schemata "
                    "WHERE schema_name IN ('oss_health', 'analytics')"
                )
            ).scalar_one()
            == 0
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        views = (
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.views WHERE table_schema = 'analytics'"
                )
            )
            .scalars()
            .all()
        )
        assert sorted(views) == sorted(ANALYTICS_VIEWS) and len(views) == 8
        tables = (
            conn.execute(
                text(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'oss_health'"
                )
            )
            .scalars()
            .all()
        )
        assert len(tables) == 8


def _seed_run(conn) -> str:
    return conn.execute(text(
        "INSERT INTO oss_health.pipeline_runs (run_id, trigger_type, pipeline_version, "
        "config_snapshot) VALUES (gen_random_uuid(), 'manual', 't', '{}') RETURNING run_id"
    )).scalar_one()  # fmt: skip


def _seed_repo(conn, run_id) -> None:
    conn.execute(text(
        "INSERT INTO oss_health.repositories (repository_id, node_id, full_name, owner_login, "
        "repo_name, html_url, is_fork, is_archived, created_at, updated_at, etl_run_id) VALUES "
        "(1, 'R1', 'a/b', 'a', 'b', 'u', false, false, now() - interval '1 year', now(), :r)"
    ), {"r": run_id})  # fmt: skip


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO oss_health.github_issues (issue_id, node_id, repository_id, issue_number, "
        "title, state, html_url, created_at, updated_at, etl_run_id) VALUES (5, 'n', 1, 1, 't', "
        "'weird', 'u', now(), now(), :r)",
        "INSERT INTO oss_health.github_issues (issue_id, node_id, repository_id, issue_number, "
        "title, state, html_url, created_at, updated_at, closed_at, etl_run_id) VALUES (5, 'n', 1, "
        "1, 't', 'closed', 'u', now(), now(), now() - interval '1 day', :r)",
        "INSERT INTO oss_health.pull_requests (pull_request_id, node_id, repository_id, pr_number, "
        "title, state, html_url, created_at, updated_at, merged_at, etl_run_id) VALUES (5, 'n', 1, "
        "1, 't', 'open', 'u', now(), now(), now(), :r)",
        "INSERT INTO oss_health.repository_snapshots (repository_id, snapshot_date, captured_at, "
        "stargazers_count, forks_count, open_issues_and_prs_count, etl_run_id) VALUES (1, "
        "'2001-01-01', now(), 1, 1, 1, :r)",
        "INSERT INTO oss_health.repository_snapshots (repository_id, snapshot_date, captured_at, "
        "stargazers_count, forks_count, open_issues_and_prs_count, etl_run_id) VALUES (1, "
        "(now() AT TIME ZONE 'UTC')::date, now(), -5, 1, 1, :r)",
        "INSERT INTO oss_health.github_issues (issue_id, node_id, repository_id, issue_number, "
        "title, state, html_url, created_at, updated_at, etl_run_id) VALUES (5, 'n', 999, 1, 't', "
        "'open', 'u', now(), now(), :r)",
        "UPDATE oss_health.pipeline_runs SET status = 'success' WHERE run_id = :r",
    ],
)
def test_constraints_reject_invalid_rows(clean_db, statement):
    with clean_db.begin() as conn:
        run_id = _seed_run(conn)
        _seed_repo(conn, run_id)
    with pytest.raises(IntegrityError), clean_db.begin() as conn:
        conn.execute(text(statement), {"r": run_id})


def test_unique_keys(clean_db):
    with clean_db.begin() as conn:
        run_id = _seed_run(conn)
        _seed_repo(conn, run_id)
        conn.execute(text(
            "INSERT INTO oss_health.data_quality_checks (run_id, check_name, check_stage, entity, "
            "severity, status) VALUES (:r, 'c', 'post_load', 'run', 'error', 'passed')"
        ), {"r": run_id})  # fmt: skip
    with pytest.raises(IntegrityError), clean_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO oss_health.data_quality_checks (run_id, check_name, check_stage, entity, "
            "severity, status) VALUES (:r, 'c', 'post_load', 'run', 'error', 'failed')"
        ), {"r": run_id})  # NULL repository counts as the same key  # fmt: skip
    with pytest.raises(IntegrityError), clean_db.begin() as conn:
        conn.execute(text(
            "INSERT INTO oss_health.repositories (repository_id, node_id, full_name, owner_login, "
            "repo_name, html_url, is_fork, is_archived, created_at, updated_at, etl_run_id) VALUES "
            "(2, 'R2', 'A/B', 'a', 'b', 'u', false, false, now(), now(), :r)"
        ), {"r": run_id})  # case-insensitive full_name  # fmt: skip


def _world(rsps) -> None:
    fake = FakeGitHub(rsps)
    merged = pr_item(9001, 10, created=ago(5), updated=ago(3), state="closed", closed=ago(3),
                     merged=ago(3))  # 48h to merge  # fmt: skip
    unmerged = pr_item(9002, 11, created=ago(6), updated=ago(4), state="closed", closed=ago(4))
    fake.add(FakeRepo(
        "acme/alpha", repo_item(101, "acme/alpha", stars=1000, forks=100),
        issues=[
            issue_item(1, 1, created=ago(2), updated=ago(1)),             # 0-7 bucket
            issue_item(2, 2, created=ago(45), updated=ago(1)),            # 31-90 bucket
            issue_item(3, 3, created=ago(500), updated=ago(400)),         # >365, untouched
            issue_item(4, 4, created=ago(9), updated=ago(1), state="closed", closed=ago(1)),
            as_issue_stream_item(merged, 7001), as_issue_stream_item(unmerged, 7002),
        ],
        pulls=[merged, unmerged],
    ))  # fmt: skip


@responses.activate
def test_views_compute_documented_metrics(settings, clean_db):
    _world(responses.mock)
    result = run_pipeline(settings, repositories=["acme/alpha"],
                          client=GitHubClient(settings.github, sleep=lambda s: None),
                          engine=clean_db)  # fmt: skip
    assert result.status == "success", result.errors
    with clean_db.connect() as conn:
        repo = conn.execute(text("SELECT * FROM analytics.dim_repository")).mappings().one()
        assert repo["stars_latest"] == 1000 and repo["forks_latest"] == 100
        assert repo["open_issues"] == 3 and repo["closed_issues_in_coverage"] == 1
        assert repo["open_prs"] == 0 and repo["merged_prs_in_coverage"] == 1
        assert repo["closed_unmerged_prs_in_coverage"] == 1
        expected_cov = (datetime.now(UTC) - timedelta(days=89)).date()
        assert repo["coverage_start_date"] in (expected_cov, expected_cov - timedelta(days=1))
        buckets = dict(
            conn.execute(
                text(
                    "SELECT backlog_age_bucket, count(*) FROM analytics.fact_issue WHERE is_open "
                    "GROUP BY 1"
                )
            ).all()
        )
        assert buckets == {"0-7 days": 1, "31-90 days": 1, ">365 days": 1}
        pr = conn.execute(
            text(
                "SELECT outcome, hours_to_merge FROM analytics.fact_pull_request ORDER BY pr_number"
            )
        ).all()
        assert pr[0].outcome == "merged" and float(pr[0].hours_to_merge) == pytest.approx(
            48, abs=0.1
        )
        assert pr[1].outcome == "closed_unmerged" and pr[1].hours_to_merge is None
        weekly = conn.execute(
            text(
                "SELECT sum(issues_opened), sum(issues_closed), sum(prs_merged), "
                "sum(prs_closed_unmerged), bool_or(NOT is_within_coverage) "
                "FROM analytics.agg_weekly_activity"
            )
        ).one()
        # The 500-day-old issue is before coverage and is not counted as weekly activity.
        assert tuple(weekly[:4]) == (3, 1, 1, 1)
        weeks = (
            conn.execute(text("SELECT week_start_date FROM analytics.agg_weekly_activity"))
            .scalars()
            .all()
        )
        assert all(w.weekday() == 0 for w in weeks)  # Monday-start weeks
        run_view = conn.execute(text("SELECT * FROM analytics.fact_pipeline_run")).mappings().one()
        assert run_view["status"] == "success" and run_view["rejection_rate"] == 0
        snaps = conn.execute(
            text("SELECT count(*) FROM analytics.fact_repository_snapshot")
        ).scalar_one()
        assert snaps == 1
        dates = conn.execute(text("SELECT min(date), max(date) FROM analytics.dim_date")).one()
        assert dates[1] == datetime.now(UTC).date()


def test_dim_repository_reports_null_not_zero_before_backfill(clean_db):
    with clean_db.begin() as conn:
        run_id = _seed_run(conn)
        _seed_repo(conn, run_id)
    with clean_db.connect() as conn:
        repo = conn.execute(text("SELECT * FROM analytics.dim_repository")).mappings().one()
    assert repo["open_issues"] is None and repo["stars_latest"] is None
    assert repo["coverage_start_date"] is None


def test_role_permissions(clean_db, db_url):
    reader = role_engine(db_url, "oss_health_reader")
    app = role_engine(db_url, "oss_health_app")
    try:
        with reader.connect() as conn:
            assert (
                conn.execute(text("SELECT count(*) FROM analytics.fact_pipeline_run")).scalar_one()
                == 0
            )
        for stmt in (
            "SELECT * FROM oss_health.github_issues",
            "INSERT INTO oss_health.pipeline_runs (run_id, trigger_type, pipeline_version, "
            "config_snapshot) VALUES (gen_random_uuid(), 'manual', 'x', '{}')",
            "CREATE TABLE analytics.evil (x int)",
            "DELETE FROM oss_health.pipeline_runs",
        ):
            with pytest.raises(DBAPIError), reader.begin() as conn:
                conn.execute(text(stmt))
        with app.begin() as conn:
            run_id = _seed_run(conn)
            conn.execute(
                text("UPDATE oss_health.pipeline_runs SET records_extracted = 1 WHERE run_id = :r"),
                {"r": run_id},
            )
            conn.execute(text("DELETE FROM oss_health.rejected_records"))
        for stmt in (
            "DELETE FROM oss_health.github_issues",
            "TRUNCATE oss_health.pipeline_runs",
            "DROP TABLE oss_health.github_issues",
            "SELECT * FROM analytics.fact_issue",
        ):
            with pytest.raises(DBAPIError), app.begin() as conn:
                conn.execute(text(stmt))
    finally:
        reader.dispose()
        app.dispose()


@responses.activate
def test_api_against_database_with_reader_role(settings, clean_db, db_url):
    reader = role_engine(db_url, "oss_health_reader")
    api = create_app(ApiSettings(rate_limit_per_minute=0), engine=reader)
    client = TestClient(api, raise_server_exceptions=False)
    try:
        # Before any run: nothing is reported as zero or as healthy.
        empty = client.get("/api/v1/summary").json()
        assert empty["repositories"]["repository_count"] == 0
        assert empty["repositories"]["stars_total"] is None
        assert empty["pull_requests"]["merge_rate"] is None
        assert empty["freshness"]["status"] == "no_successful_run"
        assert client.get("/api/v1/quality/latest").json()["run"] is None
        assert client.get("/health/ready").json() == {"status": "ok", "database": "ok"}

        _world(responses.mock)
        run_pipeline(
            settings,
            repositories=["acme/alpha"],
            client=GitHubClient(settings.github, sleep=lambda s: None),
            engine=clean_db,
        )

        summary = client.get("/api/v1/summary").json()
        assert summary["repositories"]["repository_count"] == 1
        assert summary["repositories"]["stars_total"] == 1000
        assert summary["pull_requests"]["merge_rate"] == pytest.approx(0.5)
        assert summary["pull_requests"]["median_hours_to_merge"] == pytest.approx(48, abs=0.1)
        assert summary["freshness"]["status"] == "fresh"
        assert summary["runs"]["success_rate"] == 1.0

        repos = client.get("/api/v1/repositories?search=alpha&sort=name&order=asc").json()
        assert repos["total"] == 1 and repos["items"][0]["full_name"] == "acme/alpha"
        assert client.get("/api/v1/repositories?search=zzz").json()["total"] == 0
        detail = client.get("/api/v1/repositories/101").json()
        assert detail["open_issues"] == 3 and detail["coverage_start_date"]
        assert client.get("/api/v1/repositories/999").status_code == 404

        issues = client.get("/api/v1/analytics/issues?repository_id=101").json()
        assert issues["totals"]["open_issues"] == 3
        assert [b["bucket"] for b in issues["backlog_age"]] == [
            "0-7 days",
            "31-90 days",
            ">365 days",
        ]
        assert issues["oldest_open"][0]["issue_number"] == 3

        prs = client.get("/api/v1/analytics/pull-requests?days=30").json()
        assert prs["merged_in_window"] == 1 and prs["closed_unmerged_in_window"] == 1

        activity = client.get("/api/v1/analytics/activity?weeks=52").json()["items"]
        assert sum(w["issues_opened"] for w in activity) == 3
        assert all(w["week_start_date"] for w in activity)

        stars = client.get("/api/v1/analytics/stars-forks?repository_id=101").json()["items"]
        assert len(stars) == 1 and stars[0]["stargazers_count"] == 1000

        runs = client.get("/api/v1/runs?limit=5").json()
        assert runs["items"][0]["status"] == "success"
        quality = client.get("/api/v1/quality/latest").json()
        assert quality["run"]["status"] == "success"
        assert any(c["check_name"] == "issue_pr_number_overlap" for c in quality["checks"])
        assert "postgres" not in str(quality).lower()
    finally:
        reader.dispose()
