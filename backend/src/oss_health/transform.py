"""pandas normalization of GitHub JSON into typed frames (field mappings: docs/TRD.md §5)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pandas as pd

ISSUE_COLUMNS = [
    "issue_id", "node_id", "repository_id", "issue_number", "title", "state", "state_reason",
    "author_login", "author_type", "author_association", "is_bot_author", "label_names",
    "assignee_count", "comments_count", "is_locked", "milestone_title", "html_url",
    "created_at", "updated_at", "closed_at", "is_pull_request",
]  # fmt: skip

PR_COLUMNS = [
    "pull_request_id", "node_id", "repository_id", "pr_number", "title", "state", "is_draft",
    "author_login", "author_type", "author_association", "is_bot_author", "label_names",
    "requested_reviewer_count", "base_ref", "head_ref", "head_repo_full_name", "html_url",
    "created_at", "updated_at", "closed_at", "merged_at", "merge_commit_sha",
]  # fmt: skip

REPOSITORY_COLUMNS = [
    "repository_id", "node_id", "full_name", "owner_login", "repo_name", "description",
    "html_url", "default_branch", "primary_language", "license_spdx_id", "topics", "is_fork",
    "is_archived", "created_at", "updated_at", "pushed_at",
]  # fmt: skip

SNAPSHOT_COLUMNS = [
    "repository_id", "snapshot_date", "captured_at", "stargazers_count", "forks_count",
    "subscribers_count", "open_issues_and_prs_count", "size_kb",
]  # fmt: skip

INT_COLUMNS = {
    "issue_id", "repository_id", "issue_number", "assignee_count", "comments_count",
    "pull_request_id", "pr_number", "requested_reviewer_count", "stargazers_count",
    "forks_count", "subscribers_count", "open_issues_and_prs_count", "size_kb",
}  # fmt: skip
BOOL_COLUMNS = {"is_bot_author", "is_locked", "is_draft", "is_fork", "is_archived"}
DATETIME_COLUMNS = {
    "created_at",
    "updated_at",
    "closed_at",
    "merged_at",
    "pushed_at",
    "captured_at",
}
LIST_COLUMNS = {"label_names", "topics"}

OPTIONAL_TEXT = {
    "state_reason", "author_login", "author_type", "author_association", "milestone_title",
    "base_ref", "head_ref", "head_repo_full_name", "merge_commit_sha", "description",
    "default_branch", "primary_language", "license_spdx_id",
}  # fmt: skip


@dataclass
class TransformResult:
    frame: pd.DataFrame
    duplicates_dropped: int = 0
    excluded_pull_requests: int = 0
    null_optional_counts: dict[str, int] | None = None


def _get(data: Any, *path: str) -> Any:
    for key in path:
        if not isinstance(data, dict):
            return None
        data = data.get(key)
    return data


def _text(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        value = str(value)
    value = value.strip()
    return value


def _optional_text(value: Any) -> str | None:
    text = _text(value)
    return text or None


def _names(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    names = {str(v.get("name")).strip() for v in values if isinstance(v, dict) and v.get("name")}
    return sorted(names)


def _count(values: Any) -> int:
    return len(values) if isinstance(values, list) else 0


def _bool(value: Any) -> bool:
    return bool(value) if isinstance(value, bool) else False


def is_bot(user: Any) -> bool:
    if not isinstance(user, dict):
        return False
    login = str(user.get("login") or "")
    return user.get("type") == "Bot" or login.endswith("[bot]")


def _typed_frame(rows: list[dict[str, Any]], columns: list[str]) -> pd.DataFrame:
    frame = pd.DataFrame(rows, columns=columns)
    for column in columns:
        if column in INT_COLUMNS:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype("Int64")
        elif column in BOOL_COLUMNS or column == "is_pull_request":
            frame[column] = frame[column].fillna(False).astype("bool")
        elif column in DATETIME_COLUMNS:
            frame[column] = pd.to_datetime(
                frame[column], errors="coerce", utc=True, format="ISO8601"
            )
        elif column in LIST_COLUMNS:
            frame[column] = frame[column].apply(lambda v: v if isinstance(v, list) else [])
        elif column == "snapshot_date":
            frame[column] = pd.to_datetime(frame[column]).dt.date
        else:
            frame[column] = frame[column].astype("string")
    return frame


def _dedupe(frame: pd.DataFrame, key: str) -> tuple[pd.DataFrame, int]:
    """Keep the most recently updated row per key. Rows with a null key are left for validation."""
    if frame.empty:
        return frame, 0
    keyed = frame[frame[key].notna()]
    unkeyed = frame[frame[key].isna()]
    ordered = keyed.sort_values("updated_at", ascending=False, na_position="last", kind="stable")
    deduped = ordered.drop_duplicates(subset=[key], keep="first")
    dropped = len(keyed) - len(deduped)
    result = pd.concat([deduped, unkeyed]).sort_index()
    return result, dropped


def _null_counts(frame: pd.DataFrame) -> dict[str, int]:
    return {c: int(frame[c].isna().sum()) for c in frame.columns if c in OPTIONAL_TEXT}


def transform_repository(
    repo: dict[str, Any], captured_at: datetime
) -> tuple[TransformResult, TransformResult]:
    license_id = _get(repo, "license", "spdx_id")
    repo_row = {
        "repository_id": repo.get("id"),
        "node_id": _text(repo.get("node_id")),
        "full_name": _text(repo.get("full_name")),
        "owner_login": _text(_get(repo, "owner", "login")),
        "repo_name": _text(repo.get("name")),
        "description": _optional_text(repo.get("description")),
        "html_url": _text(repo.get("html_url")),
        "default_branch": _optional_text(repo.get("default_branch")),
        "primary_language": _optional_text(repo.get("language")),
        "license_spdx_id": None if license_id in (None, "NOASSERTION") else _text(license_id),
        "topics": sorted({str(t) for t in repo.get("topics") or [] if isinstance(t, str)}),
        "is_fork": _bool(repo.get("fork")),
        "is_archived": _bool(repo.get("archived")),
        "created_at": repo.get("created_at"),
        "updated_at": repo.get("updated_at"),
        "pushed_at": repo.get("pushed_at"),
    }
    snapshot_row = {
        "repository_id": repo.get("id"),
        "snapshot_date": captured_at.date(),
        "captured_at": captured_at.isoformat(),
        "stargazers_count": repo.get("stargazers_count"),
        "forks_count": repo.get("forks_count"),
        "subscribers_count": repo.get("subscribers_count"),
        "open_issues_and_prs_count": repo.get("open_issues_count"),
        "size_kb": repo.get("size"),
    }
    repo_frame = _typed_frame([repo_row], REPOSITORY_COLUMNS)
    snapshot_frame = _typed_frame([snapshot_row], SNAPSHOT_COLUMNS)
    return (
        TransformResult(repo_frame, null_optional_counts=_null_counts(repo_frame)),
        TransformResult(snapshot_frame),
    )


def transform_issues(items: list[dict[str, Any]], repository_id: int) -> TransformResult:
    """Normalize the /issues stream. Items carrying a ``pull_request`` key are excluded."""
    rows: list[dict[str, Any]] = []
    excluded = 0
    for item in items:
        if "pull_request" in item:
            excluded += 1
            continue
        user = item.get("user")
        rows.append(
            {
                "issue_id": item.get("id"),
                "node_id": _text(item.get("node_id")),
                "repository_id": repository_id,
                "issue_number": item.get("number"),
                "title": _text(item.get("title")),
                "state": _text(item.get("state")),
                "state_reason": _optional_text(item.get("state_reason")),
                "author_login": _optional_text(_get(user, "login")),
                "author_type": _optional_text(_get(user, "type")),
                "author_association": _optional_text(item.get("author_association")),
                "is_bot_author": is_bot(user),
                "label_names": _names(item.get("labels")),
                "assignee_count": _count(item.get("assignees")),
                "comments_count": item.get("comments"),
                "is_locked": _bool(item.get("locked")),
                "milestone_title": _optional_text(_get(item, "milestone", "title")),
                "html_url": _text(item.get("html_url")),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "closed_at": item.get("closed_at"),
                "is_pull_request": False,
            }
        )
    frame, dropped = _dedupe(_typed_frame(rows, ISSUE_COLUMNS), "issue_id")
    return TransformResult(frame, dropped, excluded, _null_counts(frame))


def transform_pull_requests(items: list[dict[str, Any]], repository_id: int) -> TransformResult:
    rows: list[dict[str, Any]] = []
    for item in items:
        user = item.get("user")
        rows.append(
            {
                "pull_request_id": item.get("id"),
                "node_id": _text(item.get("node_id")),
                "repository_id": repository_id,
                "pr_number": item.get("number"),
                "title": _text(item.get("title")),
                "state": _text(item.get("state")),
                "is_draft": _bool(item.get("draft")),
                "author_login": _optional_text(_get(user, "login")),
                "author_type": _optional_text(_get(user, "type")),
                "author_association": _optional_text(item.get("author_association")),
                "is_bot_author": is_bot(user),
                "label_names": _names(item.get("labels")),
                "requested_reviewer_count": _count(item.get("requested_reviewers")),
                "base_ref": _optional_text(_get(item, "base", "ref")),
                "head_ref": _optional_text(_get(item, "head", "ref")),
                "head_repo_full_name": _optional_text(_get(item, "head", "repo", "full_name")),
                "html_url": _text(item.get("html_url")),
                "created_at": item.get("created_at"),
                "updated_at": item.get("updated_at"),
                "closed_at": item.get("closed_at"),
                "merged_at": item.get("merged_at"),
                "merge_commit_sha": _optional_text(item.get("merge_commit_sha")),
            }
        )
    frame, dropped = _dedupe(_typed_frame(rows, PR_COLUMNS), "pull_request_id")
    return TransformResult(frame, dropped, 0, _null_counts(frame))
