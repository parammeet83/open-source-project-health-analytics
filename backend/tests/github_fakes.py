"""A deterministic in-memory GitHub REST API built on ``responses`` (no network access)."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import responses

API = "https://api.github.com"
NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def ts(value: str) -> str:
    """Accept 'YYYY-MM-DD' or a full ISO string; return GitHub's Z format."""
    if len(value) == 10:
        value += "T00:00:00"
    return value.replace("+00:00", "") + ("Z" if not value.endswith("Z") else "")


def repo_item(repo_id: int, full_name: str, *, stars: int = 100, forks: int = 10) -> dict[str, Any]:
    owner, name = full_name.split("/")
    return {
        "id": repo_id,
        "node_id": f"R_{repo_id}",
        "name": name,
        "full_name": full_name,
        "owner": {"login": owner},
        "description": f"{name} description",
        "html_url": f"https://github.com/{full_name}",
        "default_branch": "main",
        "language": "Python",
        "license": {"spdx_id": "BSD-3-Clause"},
        "topics": ["python", "data"],
        "fork": False,
        "archived": False,
        "created_at": "2010-01-01T00:00:00Z",
        "updated_at": "2026-10-27T00:00:00Z",
        "pushed_at": "2026-10-27T00:00:00Z",
        "stargazers_count": stars,
        "forks_count": forks,
        "subscribers_count": 50,
        "open_issues_count": 7,
        "size": 1234,
    }


def issue_item(
    issue_id: int,
    number: int,
    *,
    created: str,
    updated: str,
    state: str = "open",
    closed: str | None = None,
    is_pr: bool = False,
    login: str | None = "alice",
    user_type: str = "User",
    labels: tuple[str, ...] = (),
    state_reason: str | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": issue_id,
        "node_id": f"I_{issue_id}",
        "number": number,
        "title": f"Issue {number}",
        "state": state,
        "state_reason": state_reason,
        "user": None if login is None else {"login": login, "type": user_type},
        "author_association": "CONTRIBUTOR",
        "labels": [{"name": label} for label in labels],
        "assignees": [],
        "comments": 2,
        "locked": False,
        "milestone": None,
        "html_url": f"https://github.com/o/r/issues/{number}",
        "created_at": ts(created),
        "updated_at": ts(updated),
        "closed_at": ts(closed) if closed else None,
    }
    if is_pr:
        item["pull_request"] = {"url": "https://api.github.com/repos/o/r/pulls/1"}
    return item


def pr_item(
    pr_id: int,
    number: int,
    *,
    created: str,
    updated: str,
    state: str = "open",
    closed: str | None = None,
    merged: str | None = None,
    draft: bool = False,
    login: str = "bob",
) -> dict[str, Any]:
    return {
        "id": pr_id,
        "node_id": f"PR_{pr_id}",
        "number": number,
        "title": f"PR {number}",
        "state": state,
        "draft": draft,
        "user": {"login": login, "type": "Bot" if login.endswith("[bot]") else "User"},
        "author_association": "MEMBER",
        "labels": [],
        "requested_reviewers": [{"login": "rev"}],
        "base": {"ref": "main"},
        "head": {"ref": f"feature-{number}", "repo": {"full_name": "fork/r"}},
        "html_url": f"https://github.com/o/r/pull/{number}",
        "created_at": ts(created),
        "updated_at": ts(updated),
        "closed_at": ts(closed) if closed else None,
        "merged_at": ts(merged) if merged else None,
        "merge_commit_sha": "abc123" if merged else None,
    }


def as_issue_stream_item(pr: dict[str, Any], issue_id: int) -> dict[str, Any]:
    """How a pull request appears in the /issues stream: a *different* id plus the PR marker."""
    return issue_item(
        issue_id,
        pr["number"],
        created=pr["created_at"],
        updated=pr["updated_at"],
        state=pr["state"],
        closed=pr["closed_at"],
        is_pr=True,
        login=pr["user"]["login"],
    )


@dataclass
class FakeRepo:
    full_name: str
    repo: dict[str, Any]
    issues: list[dict[str, Any]] = field(default_factory=list)  # includes PR stream items
    pulls: list[dict[str, Any]] = field(default_factory=list)
    fail_status: dict[str, int] = field(default_factory=dict)  # endpoint -> status


class FakeGitHub:
    """Register with an active ``responses.RequestsMock``; serves repos, issues and pulls."""

    def __init__(self, rsps: responses.RequestsMock, per_page_default: int = 100) -> None:
        self.repos: dict[str, FakeRepo] = {}
        self.calls: list[str] = []
        self.per_page_default = per_page_default
        pattern = re.compile(rf"^{re.escape(API)}/.*")
        rsps.add_callback(responses.GET, pattern, callback=self._handle)

    def add(self, repo: FakeRepo) -> FakeRepo:
        self.repos[repo.full_name.lower()] = repo
        return repo

    @staticmethod
    def _json(status: int, body: Any, headers: dict[str, str] | None = None):
        base = {"X-RateLimit-Remaining": "4999", "X-RateLimit-Reset": "9999999999"}
        base.update(headers or {})
        return status, base, json.dumps(body)

    def _handle(self, request):
        url = urlparse(request.url)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        path = url.path
        self.calls.append(f"{path}?{url.query}")
        if path == "/rate_limit":
            return self._json(200, {"resources": {"core": {"limit": 5000, "remaining": 4999}}})
        match = re.match(r"^/repos/([^/]+)/([^/]+)(?:/(issues|pulls))?$", path)
        if not match:
            return self._json(404, {"message": "Not Found"})
        repo = self.repos.get(f"{match.group(1)}/{match.group(2)}".lower())
        if repo is None:
            return self._json(404, {"message": "Not Found"})
        endpoint = match.group(3) or "repo"
        if endpoint in repo.fail_status:
            return self._json(repo.fail_status[endpoint], {"message": "boom"})
        if endpoint == "repo":
            return self._json(200, repo.repo)
        items = self._select(repo, endpoint, query)
        return self._page(request.url, items, query)

    def _select(self, repo: FakeRepo, endpoint: str, query: dict[str, str]) -> list[dict]:
        state = query.get("state", "open")
        source = repo.issues if endpoint == "issues" else repo.pulls
        items = [i for i in source if state == "all" or i["state"] == state]
        if endpoint == "issues" and "since" in query:
            items = [i for i in items if i["updated_at"] >= query["since"]]
        if query.get("sort") == "updated":
            reverse = query.get("direction", "desc") == "desc"
            items = sorted(items, key=lambda i: i["updated_at"], reverse=reverse)
        else:
            items = sorted(items, key=lambda i: i["number"], reverse=True)
        return items

    def _page(self, raw_url: str, items: list[dict], query: dict[str, str]):
        per_page = int(query.get("per_page", self.per_page_default))
        page = int(query.get("page", 1))
        chunk = items[(page - 1) * per_page : page * per_page]
        headers = {}
        if page * per_page < len(items):
            parsed = urlparse(raw_url)
            next_query = dict(query, page=str(page + 1))
            next_url = f"{API}{parsed.path}?{urlencode(next_query)}"
            headers["Link"] = f'<{next_url}>; rel="next"'
        return self._json(200, chunk, headers)
