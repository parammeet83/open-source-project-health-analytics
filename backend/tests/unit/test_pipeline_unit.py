"""Orchestration logic without a database: status, exit codes, watermarks, dry runs, reports."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
import responses

from github_fakes import FakeGitHub, FakeRepo, as_issue_stream_item, issue_item, pr_item, repo_item
from oss_health.cli import main
from oss_health.github.client import GitHubClient
from oss_health.github.extract import ISSUES, PULL_REQUESTS, ResourceExtract, plan_window
from oss_health.pipeline import RepoOutcome, compute_status, decide_watermark, run_pipeline
from oss_health.report import LATEST_NAME, write_report
from oss_health.tracking import CheckResult

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
TOKEN = "ghp_" + "S3cr3tT0k3n" * 4


def _extract(resource, *, window_complete=True, open_complete=True, include_open=True,
             items=None):  # fmt: skip
    w = plan_window(watermark=None, coverage_start=None, now=NOW, history_days=90,
                    overlap_hours=24, backfill_open_items=include_open, full_refresh=False)  # fmt: skip
    ex = ResourceExtract(resource, w, started_at=NOW)
    ex.window_items = items or []
    ex.window_complete, ex.open_complete = window_complete, open_complete
    return ex


def test_decide_watermark_rules():
    item = {"updated_at": "2026-10-20T00:00:00Z"}
    newest = datetime(2026, 10, 20, tzinfo=UTC)
    assert decide_watermark(_extract(ISSUES, items=[item]), rejection_ok=True)[0] == newest
    assert decide_watermark(_extract(ISSUES), rejection_ok=True) == (
        NOW,
        "advanced (no items in window)",
    )
    assert decide_watermark(_extract(ISSUES, items=[item]), rejection_ok=False)[0] is None
    assert decide_watermark(_extract(ISSUES, items=[item], open_complete=False),
                            rejection_ok=True)[0] is None  # fmt: skip
    # Ascending issue series: partial progress is safe.
    value, reason = decide_watermark(_extract(ISSUES, items=[item], window_complete=False),
                                     rejection_ok=True)  # fmt: skip
    assert value == newest and "partial" in reason
    # Descending PR series: an incomplete series must hold the watermark.
    assert decide_watermark(_extract(PULL_REQUESTS, items=[item], window_complete=False),
                            rejection_ok=True)[0] is None  # fmt: skip


def _outcome(status):
    o = RepoOutcome("a/b")
    o.status = status
    return o


def test_compute_status():
    ok, bad = _outcome("succeeded"), _outcome("failed")
    err = CheckResult("x", "validate", "issue", "error", "failed")
    warn = CheckResult("y", "validate", "issue", "warning", "failed")
    assert compute_status([ok, ok], [warn], fatal=False) == "success"
    assert compute_status([ok, bad], [], fatal=False) == "partial_success"
    assert compute_status([ok], [err], fatal=False) == "partial_success"
    assert compute_status([bad, bad], [], fatal=False) == "failed"
    assert compute_status([ok], [], fatal=True) == "failed"
    assert compute_status([], [], fatal=False) == "failed"


def _fake_world(rsps):
    fake = FakeGitHub(rsps)
    pr = pr_item(5000, 3, created="2026-10-01", updated="2026-10-10", state="closed",
                 closed="2026-10-10", merged="2026-10-10")  # fmt: skip
    fake.add(FakeRepo(
        "acme/alpha", repo_item(1, "acme/alpha"),
        issues=[issue_item(11, 1, created="2026-10-01", updated="2026-10-02"),
                issue_item(12, 2, created="2018-01-01", updated="2019-01-01"),
                as_issue_stream_item(pr, 13)],
        pulls=[pr],
    ))  # fmt: skip
    fake.add(FakeRepo("acme/beta", repo_item(2, "acme/beta")))
    return fake


def _client(settings, token=None):
    return GitHubClient(settings.github, token, sleep=lambda s: None)


@responses.activate
def test_dry_run_success_writes_report(settings):
    _fake_world(responses.mock)
    result = run_pipeline(settings, dry_run=True, client=_client(settings), now=lambda: NOW)
    assert result.status == "success" and result.exit_code == 0
    alpha = result.repositories[0]
    assert alpha.entities["issue"] == {"extracted": 2, "rejected": 0}
    assert alpha.entities["pull_request"] == {"extracted": 1, "rejected": 0}
    excluded = [c for c in result.checks if c.check_name == "issues_endpoint_prs_excluded"]
    assert excluded[0].observed_value == 1
    report = json.loads((settings.reports_path / LATEST_NAME).read_text(encoding="utf-8"))
    assert report["status"] == "success" and report["dry_run"] is True
    assert report["run_id"] == str(result.run_id)
    assert {"failed_checks", "completeness_warnings", "checks", "totals", "errors"} <= set(report)


@responses.activate
def test_server_error_for_one_repo_is_partial(settings):
    fake = _fake_world(responses.mock)
    fake.repos["acme/beta"].fail_status["pulls"] = 502
    result = run_pipeline(settings, dry_run=True, client=_client(settings), now=lambda: NOW)
    assert result.status == "partial_success" and result.exit_code == 2
    beta = result.repositories[1]
    assert beta.status == "failed" and beta.error_type == "GitHubTransientError"
    assert any(c.check_name == "repository_processing" and c.status == "failed"
               for c in result.checks)  # fmt: skip


@responses.activate
def test_auth_failure_is_fatal(settings):
    fake = _fake_world(responses.mock)
    fake.repos["acme/alpha"].fail_status["repo"] = 401
    result = run_pipeline(settings, dry_run=True, client=_client(settings, TOKEN),
                          now=lambda: NOW)  # fmt: skip
    assert result.status == "failed" and result.exit_code == 1
    assert [r.status for r in result.repositories] == ["failed", "failed"]
    assert result.repositories[1].error_type == "NotAttempted"


@responses.activate
def test_all_repos_failing_is_failed_not_empty_success(settings):
    fake = _fake_world(responses.mock)
    for repo in fake.repos.values():
        repo.fail_status["issues"] = 500
    result = run_pipeline(settings, dry_run=True, client=_client(settings), now=lambda: NOW)
    assert result.status == "failed" and result.exit_code == 1
    assert result.totals["extracted"] == 0


@responses.activate
def test_not_found_repository(settings):
    _fake_world(responses.mock)
    result = run_pipeline(settings, dry_run=True, repositories=["acme/alpha", "acme/missing"],
                          client=_client(settings), now=lambda: NOW)  # fmt: skip
    assert result.status == "partial_success"
    assert result.repositories[1].error_type == "GitHubNotFoundError"


def test_missing_database_url_is_not_started(settings):
    result = run_pipeline(settings, now=lambda: NOW)
    assert result.status == "not_started" and result.exit_code == 3
    assert result.run_id is None
    report = json.loads((settings.reports_path / LATEST_NAME).read_text(encoding="utf-8"))
    assert report["status"] == "not_started"
    assert any("PIPELINE_DATABASE_URL" in e for e in report["errors"])


def test_unreachable_database_is_not_started(settings):
    from dataclasses import replace

    unreachable = replace(settings, database_url="postgresql://u:p4ssw0rd-x@127.0.0.1:1/nope_db")
    result = run_pipeline(unreachable, now=lambda: NOW)
    assert result.exit_code == 3
    assert "Database unreachable" in " ".join(result.errors)
    assert "p4ssw0rd-x" not in (settings.reports_path / LATEST_NAME).read_text(encoding="utf-8")


def test_invalid_repository_override_is_not_started(settings):
    result = run_pipeline(settings, dry_run=True, repositories=["bad name"], now=lambda: NOW)
    assert result.status == "not_started"


def test_report_is_redacted_and_atomic(tmp_path):
    path = write_report({"run_id": "abc", "errors": [f"leaked {TOKEN}"]}, tmp_path)
    content = path.read_text(encoding="utf-8")
    assert TOKEN not in content and "***" in content
    assert (tmp_path / LATEST_NAME).read_text(encoding="utf-8") == content
    assert not list(tmp_path.glob(".tmp_*"))
    not_started = write_report({"run_id": None, "status": "not_started"}, tmp_path)
    assert "not_started" in not_started.name


def test_cli_check_config_and_bad_config(tmp_path, capsys):
    assert main(["check-config"]) == 0
    assert "Configuration OK" in capsys.readouterr().out
    bad = tmp_path / "bad.toml"
    bad.write_text("[pipeline]\nrepositories = []", encoding="utf-8")
    assert main(["--config", str(bad), "check-config"]) == 3
    assert main(["--config", str(bad), "run", "--dry-run"]) == 3


def test_cli_run_without_database_exits_3(tmp_path, monkeypatch):
    cfg = tmp_path / "p.toml"
    cfg.write_text(
        '[pipeline]\nrepositories = ["a/b"]\n[output]\nwrite_log_file = false\n'
        f'reports_dir = "{(tmp_path / "r").as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.delenv("PIPELINE_DATABASE_URL", raising=False)
    monkeypatch.setattr("oss_health.config.BACKEND_DIR", tmp_path)  # no .env pick-up
    assert main(["--config", str(cfg), "run"]) == 3


@pytest.mark.parametrize("days", [1, 3])
def test_backfill_window_matches_history(days):
    w = plan_window(watermark=None, coverage_start=None, now=NOW, history_days=days,
                    overlap_hours=24, backfill_open_items=True, full_refresh=False)  # fmt: skip
    assert NOW - w.since == timedelta(days=days)
