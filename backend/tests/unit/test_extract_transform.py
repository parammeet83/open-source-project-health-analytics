"""Extraction windows, issue/PR routing, transformation and null handling."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pandas as pd
import responses

from github_fakes import (
    FakeGitHub,
    FakeRepo,
    as_issue_stream_item,
    issue_item,
    pr_item,
    repo_item,
)
from oss_health.github.client import GitHubClient
from oss_health.github.extract import (
    extract_issues,
    extract_pull_requests,
    parse_github_datetime,
    plan_window,
)
from oss_health.transform import (
    is_bot,
    transform_issues,
    transform_pull_requests,
    transform_repository,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def window(mode="backfill", include_open=True, days=90):
    return plan_window(
        watermark=None if mode == "backfill" else NOW - timedelta(days=1),
        coverage_start=None,
        now=NOW,
        history_days=days,
        overlap_hours=24,
        backfill_open_items=include_open,
        full_refresh=False,
    )


def test_plan_window_backfill_and_incremental():
    backfill = window()
    assert backfill.mode == "backfill"
    assert backfill.since == NOW - timedelta(days=90)
    assert backfill.include_open is True
    assert backfill.coverage_start == backfill.since

    wm = NOW - timedelta(hours=10)
    inc = plan_window(watermark=wm, coverage_start=NOW - timedelta(days=90), now=NOW,
                      history_days=90, overlap_hours=24, backfill_open_items=True,
                      full_refresh=False)  # fmt: skip
    assert inc.mode == "incremental"
    assert inc.since == wm - timedelta(hours=24)
    assert inc.include_open is False
    assert inc.coverage_start == NOW - timedelta(days=90)


def test_full_refresh_never_shrinks_coverage():
    old_cov = NOW - timedelta(days=400)
    w = plan_window(watermark=NOW, coverage_start=old_cov, now=NOW, history_days=90,
                    overlap_hours=24, backfill_open_items=True, full_refresh=True)  # fmt: skip
    assert w.mode == "backfill" and w.coverage_start == old_cov


def test_parse_github_datetime():
    assert parse_github_datetime("2026-01-02T03:04:05Z") == datetime(
        2026, 1, 2, 3, 4, 5, tzinfo=UTC
    )
    assert parse_github_datetime("garbage") is None
    assert parse_github_datetime(None) is None


@responses.activate
def test_extract_issues_backfill_includes_old_open_items(gh_settings):
    fake = FakeGitHub(responses.mock)
    old_open = issue_item(1, 1, created="2020-01-01", updated="2021-01-01")  # untouched for years
    recent = issue_item(2, 2, created="2026-10-01", updated="2026-10-20", state="closed",
                        closed="2026-10-20")  # fmt: skip
    fake.add(FakeRepo("o/r", repo_item(1, "o/r"), issues=[old_open, recent]))
    client = GitHubClient(gh_settings, sleep=lambda s: None)
    result = extract_issues(client, "o/r", window())
    numbers = sorted(i["number"] for i in result.items)
    assert numbers == [1, 2]
    assert [i["number"] for i in result.window_items] == [2]
    assert [i["number"] for i in result.open_items] == [1]
    assert result.complete
    window_call = next(c for c in fake.calls if "since=" in c)
    assert "sort=updated" in window_call and "direction=asc" in window_call


@responses.activate
def test_extract_pulls_uses_no_since_and_stops_at_window(gh_settings):
    fake = FakeGitHub(responses.mock)
    pulls = [
        pr_item(100 + n, n, created="2026-01-01", updated=f"2026-10-{n:02d}")
        for n in range(1, 21)
    ] + [pr_item(900, 99, created="2019-01-01", updated="2019-06-01", state="closed",
                 closed="2019-06-01")]  # fmt: skip
    fake.add(FakeRepo("o/r", repo_item(1, "o/r"), pulls=pulls))
    client = GitHubClient(gh_settings, sleep=lambda s: None)
    w = plan_window(watermark=datetime(2026, 10, 16, tzinfo=UTC), coverage_start=None, now=NOW,
                    history_days=90, overlap_hours=24, backfill_open_items=True,
                    full_refresh=False)  # fmt: skip
    result = extract_pull_requests(client, "o/r", w)
    assert all("since=" not in c for c in fake.calls), "pulls endpoint has no since parameter"
    assert all("direction=desc" in c for c in fake.calls if "state=all" in c)
    kept = sorted(i["number"] for i in result.window_items)
    assert kept == list(range(15, 21))  # updated on/after Oct 15 (watermark - 24h)
    assert result.pages < 11, "paging stopped once items were older than the window"
    assert result.open_items == []  # incremental mode does not re-fetch open items


def test_issue_stream_excludes_pull_requests():
    pr = pr_item(500, 7, created="2026-10-01", updated="2026-10-02")
    items = [
        issue_item(1, 5, created="2026-10-01", updated="2026-10-02"),
        as_issue_stream_item(pr, issue_id=9007),
    ]
    result = transform_issues(items, repository_id=42)
    assert result.excluded_pull_requests == 1
    assert result.frame["issue_number"].tolist() == [5]
    assert not result.frame["is_pull_request"].any()


def test_transform_issue_types_and_nulls():
    items = [
        issue_item(1, 5, created="2026-10-01", updated="2026-10-02", login=None,
                   labels=("bug", "a-label", "bug")),
        issue_item(2, 6, created="2026-10-01T10:00:00", updated="2026-10-03", state="closed",
                   closed="2026-10-03", login="dependabot[bot]", user_type="Bot",
                   state_reason="completed"),
    ]  # fmt: skip
    frame = transform_issues(items, 42).frame
    assert str(frame["issue_id"].dtype) == "Int64"
    assert str(frame["created_at"].dtype) == "datetime64[ns, UTC]"
    first = frame.iloc[0]
    assert pd.isna(first["author_login"])
    assert first["label_names"] == ["a-label", "bug"]
    assert pd.isna(first["closed_at"])
    assert bool(frame.iloc[1]["is_bot_author"]) is True
    assert frame.iloc[1]["repository_id"] == 42


def test_dedup_keeps_most_recent_version():
    items = [
        issue_item(1, 5, created="2026-10-01", updated="2026-10-02", state="open"),
        issue_item(1, 5, created="2026-10-01", updated="2026-10-05", state="closed",
                   closed="2026-10-05"),
    ]  # fmt: skip
    result = transform_issues(items, 42)
    assert result.duplicates_dropped == 1
    assert len(result.frame) == 1
    assert result.frame.iloc[0]["state"] == "closed"


def test_transform_pull_requests_fields():
    items = [
        pr_item(10, 3, created="2026-10-01", updated="2026-10-04", state="closed",
                closed="2026-10-04", merged="2026-10-04", draft=False),
        pr_item(11, 4, created="2026-10-01", updated="2026-10-02", draft=True,
                login="renovate[bot]"),
    ]  # fmt: skip
    items[1]["head"]["repo"] = None  # source fork deleted
    frame = transform_pull_requests(items, 42).frame
    assert frame.iloc[0]["merge_commit_sha"] == "abc123"
    assert frame.iloc[0]["requested_reviewer_count"] == 1
    assert pd.isna(frame.iloc[1]["head_repo_full_name"])
    assert bool(frame.iloc[1]["is_draft"]) and bool(frame.iloc[1]["is_bot_author"])


def test_transform_repository_and_snapshot():
    repo = repo_item(7, "acme/alpha", stars=1234, forks=56)
    repo["license"] = {"spdx_id": "NOASSERTION"}
    repo_tr, snap_tr = transform_repository(repo, NOW)
    r = repo_tr.frame.iloc[0]
    assert r["full_name"] == "acme/alpha" and r["owner_login"] == "acme"
    assert pd.isna(r["license_spdx_id"])
    s = snap_tr.frame.iloc[0]
    assert s["stargazers_count"] == 1234 and s["forks_count"] == 56
    assert s["snapshot_date"] == NOW.date()
    assert s["open_issues_and_prs_count"] == 7


def test_unparseable_values_become_null_for_validation():
    item = issue_item(1, 5, created="not-a-date", updated="2026-10-02")
    item["id"] = "abc"
    frame = transform_issues([item], 42).frame
    assert pd.isna(frame.iloc[0]["issue_id"])
    assert pd.isna(frame.iloc[0]["created_at"])


def test_is_bot():
    assert is_bot({"login": "x[bot]", "type": "User"})
    assert is_bot({"login": "x", "type": "Bot"})
    assert not is_bot({"login": "x", "type": "User"})
    assert not is_bot(None)
