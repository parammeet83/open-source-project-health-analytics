"""End-to-end pipeline runs against the dedicated test database with a mocked GitHub API."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import responses
from sqlalchemy import text

from github_fakes import FakeGitHub, FakeRepo, as_issue_stream_item, issue_item, pr_item, repo_item
from oss_health.db import PIPELINE_LOCK_KEY
from oss_health.github.client import GitHubClient
from oss_health.pipeline import run_pipeline

pytestmark = pytest.mark.integration


def ago(days: float = 0, hours: float = 0) -> str:
    moment = datetime.now(UTC) - timedelta(days=days, hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def world(rsps) -> FakeGitHub:
    fake = FakeGitHub(rsps)
    merged = pr_item(9001, 3, created=ago(10), updated=ago(8), state="closed", closed=ago(8),
                     merged=ago(8))  # fmt: skip
    unmerged = pr_item(9002, 4, created=ago(20), updated=ago(15), state="closed", closed=ago(15))
    open_pr = pr_item(9003, 5, created=ago(3), updated=ago(2), draft=True)
    fake.add(FakeRepo(
        "acme/alpha", repo_item(101, "acme/alpha", stars=1000, forks=100),
        issues=[
            issue_item(1, 1, created=ago(30), updated=ago(5), state="closed", closed=ago(5)),
            issue_item(2, 2, created=ago(4), updated=ago(1)),
            issue_item(3, 6, created=ago(800), updated=ago(700)),  # old untouched open issue
            issue_item(4, 7, created=ago(60), updated=ago(2), login="ci[bot]", user_type="Bot"),
            as_issue_stream_item(merged, 7001),
            as_issue_stream_item(unmerged, 7002),
            as_issue_stream_item(open_pr, 7003),
        ],
        pulls=[merged, unmerged, open_pr],
    ))  # fmt: skip
    fake.add(FakeRepo(
        "acme/beta", repo_item(202, "acme/beta", stars=50, forks=5),
        issues=[issue_item(21, 1, created=ago(12), updated=ago(3))],
        pulls=[pr_item(9101, 2, created=ago(9), updated=ago(6), state="closed", closed=ago(6),
                       merged=ago(6))],
    ))  # fmt: skip
    return fake


def client_for(settings):
    return GitHubClient(settings.github, None, sleep=lambda s: None)


def counts(engine) -> dict[str, int]:
    with engine.connect() as conn:
        return {
            t: conn.execute(text(f"SELECT count(*) FROM oss_health.{t}")).scalar_one()
            for t in ("repositories", "repository_snapshots", "github_issues", "pull_requests",
                      "extraction_watermarks", "pipeline_runs")
        }  # fmt: skip


def run(settings, engine, **kwargs):
    return run_pipeline(settings, client=client_for(settings), engine=engine, **kwargs)


@responses.activate
def test_first_run_loads_everything(settings, clean_db):
    world(responses.mock)
    result = run(settings, clean_db)
    assert result.status == "success", result.errors
    assert result.exit_code == 0
    assert counts(clean_db) == {
        "repositories": 2, "repository_snapshots": 2, "github_issues": 5, "pull_requests": 4,
        "extraction_watermarks": 4, "pipeline_runs": 1,
    }  # fmt: skip
    with clean_db.connect() as conn:
        run_row = conn.execute(text("SELECT * FROM oss_health.pipeline_runs")).mappings().one()
        assert run_row["status"] == "success" and run_row["exit_code"] == 0
        assert run_row["repositories_succeeded"] == 2 and run_row["finished_at"] is not None
        assert "github_token" not in json.dumps(run_row["config_snapshot"]).replace(
            "github_token_configured", ""
        )
        # The old untouched open issue is present (open-item backfill).
        assert (
            conn.execute(
                text("SELECT state FROM oss_health.github_issues WHERE issue_number = 6")
            ).scalar_one()
            == "open"
        )
        # No PR leaked into the issues table.
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM oss_health.github_issues WHERE issue_id IN (7001, 7002, 7003)"
                )
            ).scalar_one()
            == 0
        )
        failed = conn.execute(
            text(
                "SELECT check_name FROM oss_health.data_quality_checks WHERE status = 'failed' "
                "AND severity = 'error'"
            )
        ).all()
        assert failed == []
        wm = conn.execute(
            text(
                "SELECT resource, high_watermark, coverage_start_at FROM oss_health.extraction_watermarks "
                "WHERE repository_id = 101 ORDER BY resource"
            )
        ).all()
        assert [w.resource for w in wm] == ["issues", "pull_requests"]
        for w in wm:
            assert w.coverage_start_at < w.high_watermark
            assert w.coverage_start_at < datetime.now(UTC) - timedelta(days=89)
    report = json.loads((settings.reports_path / "latest_quality_report.json").read_text("utf-8"))
    assert report["status"] == "success" and report["run_id"] == str(result.run_id)


@responses.activate
def test_rerun_is_idempotent(settings, clean_db):
    world(responses.mock)
    assert run(settings, clean_db).status == "success"
    before = counts(clean_db)
    with clean_db.connect() as conn:
        stamps = conn.execute(
            text("SELECT max(etl_updated_at) FROM oss_health.github_issues")
        ).scalar_one()
        snapshot_stamp = conn.execute(
            text("SELECT max(captured_at) FROM oss_health.repository_snapshots")
        ).scalar_one()
    second = run(settings, clean_db)
    assert second.status == "success"
    assert second.totals["inserted"] == 0 and second.totals["updated"] == 0
    after = counts(clean_db)
    assert after["pipeline_runs"] == 2
    after.pop("pipeline_runs"), before.pop("pipeline_runs")
    assert after == before
    assert all(r.modes == {"issues": "incremental", "pull_requests": "incremental"}
               for r in second.repositories)  # fmt: skip
    with clean_db.connect() as conn:
        assert (
            conn.execute(
                text("SELECT max(etl_updated_at) FROM oss_health.github_issues")
            ).scalar_one()
            == stamps
        )
        assert (
            conn.execute(
                text("SELECT max(captured_at) FROM oss_health.repository_snapshots")
            ).scalar_one()
            == snapshot_stamp
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM oss_health.repository_snapshots "
                    "GROUP BY repository_id, snapshot_date HAVING count(*) > 1"
                )
            ).first()
            is None
        )


@responses.activate
def test_updates_apply_and_stale_versions_never_overwrite(settings, clean_db):
    fake = world(responses.mock)
    run(settings, clean_db)
    alpha = fake.repos["acme/alpha"]
    issue2 = next(i for i in alpha.issues if i["number"] == 2)
    issue2.update(state="closed", closed_at=ago(0, 1), updated_at=ago(0, 1), title="Fixed")
    alpha.repo["stargazers_count"] = 1010
    result = run(settings, clean_db)
    assert result.repositories[0].entities["issue"]["updated"] == 1
    assert result.repositories[0].entities["repository_snapshot"]["updated"] == 1
    with clean_db.connect() as conn:
        row = conn.execute(
            text("SELECT state, title FROM oss_health.github_issues WHERE issue_id = 2")
        ).one()
        assert tuple(row) == ("closed", "Fixed")
        assert (
            conn.execute(
                text(
                    "SELECT stargazers_count FROM oss_health.repository_snapshots WHERE repository_id = 101"
                )
            ).scalar_one()
            == 1010
        )
    # A stale copy (older updated_at) must not overwrite the newer stored row.
    issue2.update(state="open", closed_at=None, updated_at=ago(0, 5), title="Stale title")
    stale = run(settings, clean_db, full_refresh=False)
    assert stale.status == "success"
    with clean_db.connect() as conn:
        assert (
            conn.execute(
                text("SELECT title FROM oss_health.github_issues WHERE issue_id = 2")
            ).scalar_one()
            == "Fixed"
        )


@responses.activate
def test_partial_failure_keeps_other_repos_and_holds_watermarks(settings, clean_db):
    fake = world(responses.mock)
    fake.repos["acme/beta"].fail_status["pulls"] = 502
    result = run(settings, clean_db)
    assert result.status == "partial_success" and result.exit_code == 2
    with clean_db.connect() as conn:
        run_row = conn.execute(text("SELECT * FROM oss_health.pipeline_runs")).mappings().one()
        assert run_row["status"] == "partial_success" and run_row["repositories_failed"] == 1
        assert "502" in run_row["error_message"]
        assert (
            conn.execute(
                text("SELECT count(*) FROM oss_health.repositories WHERE repository_id = 202")
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM oss_health.extraction_watermarks WHERE repository_id = 202"
                )
            ).scalar_one()
            == 0
        )
        assert (
            conn.execute(
                text("SELECT count(*) FROM oss_health.github_issues WHERE repository_id = 101")
            ).scalar_one()
            == 4
        )
        failed = conn.execute(
            text(
                "SELECT check_name, repository_full_name, repository_id FROM oss_health.data_quality_checks "
                "WHERE status = 'failed' AND severity = 'error'"
            )
        ).all()
        assert ("repository_processing", "acme/beta", None) in [tuple(f) for f in failed]
        view = conn.execute(
            text("SELECT status, error_checks_failed FROM analytics.fact_pipeline_run")
        ).one()
        assert view.status == "partial_success" and view.error_checks_failed >= 1


@responses.activate
def test_load_failure_rolls_back_repository(settings, clean_db, monkeypatch):
    world(responses.mock)

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure mid-load")

    from sqlalchemy.exc import SQLAlchemyError

    class SimulatedDbError(SQLAlchemyError):
        pass

    def fail_prs(*args, **kwargs):
        raise SimulatedDbError("simulated failure mid-load")

    monkeypatch.setattr("oss_health.pipeline.upsert_pull_requests", fail_prs)
    result = run(settings, clean_db)
    assert result.status == "failed" and result.exit_code == 1  # both repos failed
    assert counts(clean_db) == {
        "repositories": 0, "repository_snapshots": 0, "github_issues": 0, "pull_requests": 0,
        "extraction_watermarks": 0, "pipeline_runs": 1,
    }  # fmt: skip
    with clean_db.connect() as conn:
        assert (
            conn.execute(text("SELECT status FROM oss_health.pipeline_runs")).scalar_one()
            == "failed"
        )
        assert (
            conn.execute(text("SELECT count(*) FROM oss_health.data_quality_checks")).scalar_one()
            > 0
        )
    del boom  # fmt: skip


@responses.activate
def test_rejections_are_quarantined_and_high_rate_holds_watermark(settings, clean_db):
    fake = world(responses.mock)
    beta = fake.repos["acme/beta"]
    beta.issues.append(issue_item(22, 9, created=ago(5), updated=ago(1), state="closed",
                                  closed=ago(9)))  # closed before created  # fmt: skip
    result = run(settings, clean_db)
    # 1 of 2 beta issues rejected (50% > 5%) -> error check -> partial, issue watermark held.
    assert result.status == "partial_success"
    beta_outcome = result.repositories[1]
    assert beta_outcome.watermarks["issues"].startswith("held")
    assert beta_outcome.watermarks["pull_requests"].startswith("advanced")
    with clean_db.connect() as conn:
        rej = conn.execute(
            text(
                "SELECT entity, source_number, check_name, failure_reason FROM oss_health.rejected_records"
            )
        ).one()
        assert rej.source_number == 9 and rej.check_name == "closed_after_created"
        assert (
            conn.execute(
                text("SELECT count(*) FROM oss_health.github_issues WHERE repository_id = 202")
            ).scalar_one()
            == 1
        )
        assert (
            conn.execute(
                text(
                    "SELECT count(*) FROM oss_health.extraction_watermarks "
                    "WHERE repository_id = 202 AND resource = 'issues'"
                )
            ).scalar_one()
            == 0
        )


@responses.activate
def test_overlap_check_detects_double_counting(settings, clean_db):
    world(responses.mock)
    run(settings, clean_db)
    with clean_db.begin() as conn:  # simulate a bug that loaded PR #3 as an issue too
        conn.execute(
            text(
                "INSERT INTO oss_health.github_issues (issue_id, node_id, repository_id, issue_number, "
                "title, state, html_url, created_at, updated_at, etl_run_id) SELECT 7777, 'X', 101, 3, "
                "'dup', 'closed', 'u', now() - interval '2 days', now() - interval '2 days', run_id "
                "FROM oss_health.pipeline_runs LIMIT 1"
            )
        )
    result = run(settings, clean_db)
    overlap = next(c for c in result.checks if c.check_name == "issue_pr_number_overlap")
    assert overlap.status == "failed" and overlap.records_failed == 1
    assert result.status == "partial_success" and result.exit_code == 2


@responses.activate
def test_concurrent_run_is_refused(settings, clean_db):
    world(responses.mock)
    holder = clean_db.connect()
    holder.execute(text("SELECT pg_advisory_lock(:k)"), {"k": PIPELINE_LOCK_KEY})
    try:
        result = run(settings, clean_db)
        assert result.status == "not_started" and result.exit_code == 3
        assert "already in progress" in " ".join(result.errors)
    finally:
        holder.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PIPELINE_LOCK_KEY})
        holder.close()
    assert counts(clean_db)["pipeline_runs"] == 0


@responses.activate
def test_stale_running_rows_are_closed(settings, clean_db):
    world(responses.mock)
    with clean_db.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO oss_health.pipeline_runs (run_id, status, trigger_type, started_at, "
                "pipeline_version, config_snapshot) VALUES (gen_random_uuid(), 'running', 'scheduled', "
                "now() - interval '10 hours', 'old', '{}')"
            )
        )
    run(settings, clean_db)
    with clean_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT status, error_type FROM oss_health.pipeline_runs WHERE pipeline_version = 'old'"
            )
        ).one()
    assert tuple(row) == ("failed", "AbandonedRun")


@responses.activate
def test_auth_failure_marks_run_failed(settings, clean_db):
    fake = world(responses.mock)
    fake.repos["acme/alpha"].fail_status["issues"] = 401
    result = run(settings, clean_db)
    assert result.status == "failed" and result.exit_code == 1
    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT status, exit_code FROM oss_health.pipeline_runs")).one()
    assert tuple(row) == ("failed", 1)


@responses.activate
def test_lock_released_after_run(settings, clean_db):
    world(responses.mock)
    run(settings, clean_db)
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": PIPELINE_LOCK_KEY}
                            ).scalar_one() is True  # fmt: skip
        conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": PIPELINE_LOCK_KEY})


@responses.activate
def test_removed_repository_becomes_untracked(settings, clean_db):
    world(responses.mock)
    run(settings, clean_db)
    only_alpha = replace(settings, repositories=("acme/alpha",))
    assert run(only_alpha, clean_db).status == "success"
    with clean_db.connect() as conn:
        tracked = dict(
            conn.execute(text("SELECT full_name, is_tracked FROM oss_health.repositories")).all()
        )
        assert tracked == {"acme/alpha": True, "acme/beta": False}
        assert conn.execute(text("SELECT count(*) FROM analytics.dim_repository")).scalar_one() == 1


def test_parallel_lock_attempts_only_one_wins(clean_db):
    from oss_health.errors import PreconditionError
    from oss_health.tracking import RunLock

    results: list[str] = []
    first = RunLock(clean_db)
    first.acquire()

    def contender():
        try:
            RunLock(clean_db).acquire()
            results.append("acquired")
        except PreconditionError:
            results.append("refused")

    t = threading.Thread(target=contender)
    t.start()
    t.join()
    first.release()
    assert results == ["refused"]
