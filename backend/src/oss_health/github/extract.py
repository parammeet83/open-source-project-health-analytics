"""Endpoint-specific extraction for one repository.

* Issues: ``/issues?state=all&since=â€¦&sort=updated&direction=asc`` (the stream also contains
  pull requests; they are routed out during transformation).
* Pull requests: ``/pulls?state=all&sort=updated&direction=desc``. This endpoint has no ``since``
  parameter, so paging stops once a page's oldest item is older than the window start.
* Backfill also fetches **all currently open** issues and PRs so old, untouched open items are
  present for backlog-age metrics.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from oss_health.github.client import GitHubClient, PageResult

logger = logging.getLogger(__name__)

ISSUES = "issues"
PULL_REQUESTS = "pull_requests"


def parse_github_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def iso_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class ResourceWindow:
    """How one resource (issues or PRs) of one repository is extracted in this run."""

    mode: str  # "backfill" or "incremental"
    since: datetime
    include_open: bool
    coverage_start: datetime


def plan_window(
    *,
    watermark: datetime | None,
    coverage_start: datetime | None,
    now: datetime,
    history_days: int,
    overlap_hours: int,
    backfill_open_items: bool,
    full_refresh: bool,
) -> ResourceWindow:
    if watermark is None or full_refresh:
        start = now - timedelta(days=history_days)
        coverage = min(start, coverage_start) if coverage_start else start
        return ResourceWindow("backfill", start, backfill_open_items, coverage)
    since = watermark - timedelta(hours=overlap_hours)
    return ResourceWindow("incremental", since, False, coverage_start or since)


@dataclass
class ResourceExtract:
    resource: str
    window: ResourceWindow
    window_items: list[dict[str, Any]] = field(default_factory=list)
    open_items: list[dict[str, Any]] = field(default_factory=list)
    window_complete: bool = True
    open_complete: bool = True
    pages: int = 0
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))

    @property
    def items(self) -> list[dict[str, Any]]:
        return self.window_items + self.open_items

    @property
    def complete(self) -> bool:
        return self.window_complete and self.open_complete

    @property
    def window_max_updated_at(self) -> datetime | None:
        values = [parse_github_datetime(i.get("updated_at")) for i in self.window_items]
        present = [v for v in values if v is not None]
        return max(present) if present else None


def _split(full_name: str) -> tuple[str, str]:
    owner, name = full_name.split("/", 1)
    return owner, name


def extract_repository_metadata(client: GitHubClient, full_name: str) -> dict[str, Any]:
    owner, name = _split(full_name)
    return client.get_object(f"/repos/{owner}/{name}")


def extract_issues(client: GitHubClient, full_name: str, window: ResourceWindow) -> ResourceExtract:
    owner, name = _split(full_name)
    result = ResourceExtract(ISSUES, window, started_at=datetime.now(UTC))
    page: PageResult = client.paginate(
        f"/repos/{owner}/{name}/issues",
        {"state": "all", "since": iso_z(window.since), "sort": "updated", "direction": "asc"},
    )
    result.window_items, result.window_complete, result.pages = (
        page.items,
        page.complete,
        page.pages,
    )
    if window.include_open:
        open_page = client.paginate(f"/repos/{owner}/{name}/issues", {"state": "open"})
        result.open_items, result.open_complete = open_page.items, open_page.complete
        result.pages += open_page.pages
    return result


def extract_pull_requests(
    client: GitHubClient, full_name: str, window: ResourceWindow
) -> ResourceExtract:
    owner, name = _split(full_name)
    result = ResourceExtract(PULL_REQUESTS, window, started_at=datetime.now(UTC))

    def older_than_window(page: list[dict[str, Any]]) -> bool:
        if not page:
            return True
        oldest = parse_github_datetime(page[-1].get("updated_at"))
        return oldest is not None and oldest < window.since

    page = client.paginate(
        f"/repos/{owner}/{name}/pulls",
        {"state": "all", "sort": "updated", "direction": "desc"},
        stop_when=older_than_window,
    )
    kept: list[dict[str, Any]] = []
    for item in page.items:
        updated = parse_github_datetime(item.get("updated_at"))
        # Keep unparseable rows so validation can reject them with a reason.
        if updated is None or updated >= window.since:
            kept.append(item)
    result.window_items, result.window_complete, result.pages = kept, page.complete, page.pages
    if window.include_open:
        open_page = client.paginate(f"/repos/{owner}/{name}/pulls", {"state": "open"})
        result.open_items, result.open_complete = open_page.items, open_page.complete
        result.pages += open_page.pages
    return result
