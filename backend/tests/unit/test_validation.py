"""Pandera validation: rejection reasons, quarantine, structural failures."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from github_fakes import issue_item, pr_item, repo_item
from oss_health.errors import ValidationStructureError
from oss_health.transform import transform_issues, transform_pull_requests, transform_repository
from oss_health.validation import (
    duplicate_keys,
    issue_schema,
    pull_request_schema,
    repository_schema,
    snapshot_schema,
    unknown_state_reasons,
    validate_frame,
)


def _validate_issues(items, repository_id=42):
    frame = transform_issues(items, repository_id).frame
    return validate_frame(frame, issue_schema(repository_id), entity="issue",
                          repository_full_name="o/r", id_column="issue_id",
                          number_column="issue_number")  # fmt: skip


def test_valid_rows_pass_untouched():
    outcome = _validate_issues([issue_item(1, 1, created="2026-10-01", updated="2026-10-02")])
    assert outcome.rejected_rows == 0 and len(outcome.valid) == 1 and outcome.rejections == []


def test_each_rule_rejects_with_reason():
    bad_state = issue_item(2, 2, created="2026-10-01", updated="2026-10-02", state="weird")
    closed_before = issue_item(3, 3, created="2026-10-05", updated="2026-10-06", state="closed",
                               closed="2026-10-01")  # fmt: skip
    updated_before = issue_item(4, 4, created="2026-10-05", updated="2026-10-01")
    missing_id = issue_item(5, 5, created="2026-10-01", updated="2026-10-02")
    missing_id["id"] = None
    good = issue_item(6, 6, created="2026-10-01", updated="2026-10-02")
    outcome = _validate_issues([bad_state, closed_before, updated_before, missing_id, good])

    assert outcome.evaluated == 5
    assert outcome.rejected_rows == 4
    assert outcome.valid["issue_number"].tolist() == [6]
    by_number = {}
    for r in outcome.rejections:
        by_number.setdefault(r["source_number"], set()).add(r["check_name"])
    assert "isin(['open', 'closed'])" in by_number[2]
    assert by_number[3] == {"closed_after_created"}
    assert by_number[4] == {"updated_after_created"}
    assert "not_nullable" in by_number[5]
    reason = next(r for r in outcome.rejections if r["source_number"] == 3)
    assert reason["failure_reason"] == "closed_at must be on or after created_at"
    assert reason["record_payload"]["issue_number"] == 3
    assert outcome.rejection_rate == pytest.approx(0.8)


def test_rows_from_another_repository_are_rejected():
    frame = transform_issues(
        [issue_item(1, 1, created="2026-10-01", updated="2026-10-02")], 99
    ).frame
    outcome = validate_frame(frame, issue_schema(42), entity="issue", repository_full_name="o/r",
                             id_column="issue_id", number_column="issue_number")  # fmt: skip
    assert outcome.rejections[0]["check_name"] == "repository_consistency"


def test_pull_request_merge_rules():
    ok = pr_item(1, 1, created="2026-10-01", updated="2026-10-02", state="closed",
                 closed="2026-10-02", merged="2026-10-02")  # fmt: skip
    merged_open = pr_item(2, 2, created="2026-10-01", updated="2026-10-02", state="open",
                          merged="2026-10-02")  # fmt: skip
    frame = transform_pull_requests([ok, merged_open], 42).frame
    outcome = validate_frame(frame, pull_request_schema(42), entity="pull_request",
                             repository_full_name="o/r", id_column="pull_request_id",
                             number_column="pr_number")  # fmt: skip
    assert outcome.valid["pr_number"].tolist() == [1]
    assert outcome.rejections[0]["check_name"] == "merged_requires_closed"


def test_repository_and_snapshot_schemas():
    repo_tr, snap_tr = transform_repository(repo_item(5, "a/b"), datetime(2026, 10, 28, tzinfo=UTC))
    assert validate_frame(repo_tr.frame, repository_schema(), entity="repository",
                          repository_full_name="a/b", id_column="repository_id").rejected_rows == 0  # fmt: skip
    snap = snap_tr.frame.copy()
    snap.loc[0, "stargazers_count"] = -1
    outcome = validate_frame(snap, snapshot_schema(), entity="repository_snapshot",
                             repository_full_name="a/b", id_column="repository_id")  # fmt: skip
    assert outcome.rejected_rows == 1
    assert outcome.rejections[0]["failed_column"] == "stargazers_count"


def test_structural_failure_raises():
    frame = transform_issues(
        [issue_item(1, 1, created="2026-10-01", updated="2026-10-02")], 42
    ).frame
    broken = frame.drop(columns=["state"])
    with pytest.raises(ValidationStructureError):
        validate_frame(broken, issue_schema(42), entity="issue", repository_full_name="o/r",
                       id_column="issue_id")  # fmt: skip


def test_empty_frames_validate():
    outcome = _validate_issues([])
    assert outcome.evaluated == 0 and outcome.rejection_rate == 0.0


def test_unknown_state_reason_and_duplicates():
    items = [
        issue_item(1, 1, created="2026-10-01", updated="2026-10-02", state="closed",
                   closed="2026-10-02", state_reason="completed"),
        issue_item(2, 2, created="2026-10-01", updated="2026-10-02", state="closed",
                   closed="2026-10-02", state_reason="moved_to_mars"),
    ]  # fmt: skip
    frame = transform_issues(items, 42).frame
    assert unknown_state_reasons(frame) == {"moved_to_mars": 1}
    doubled = frame._append(frame.iloc[[0]])
    assert duplicate_keys(doubled, "issue_id") == 1
