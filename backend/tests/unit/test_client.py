"""GitHub client: pagination, retries, rate limits, Retry-After, error typing."""

from __future__ import annotations

import pytest
import requests
import responses

from oss_health.errors import (
    GitHubAuthError,
    GitHubClientError,
    GitHubNotFoundError,
    GitHubResponseError,
    GitHubTransientError,
    RateLimitExceededError,
)
from oss_health.github.client import GitHubClient

API = "https://api.github.com"


def make_client(gh_settings, fake_time, token=None):
    return GitHubClient(
        gh_settings, token, sleep=fake_time.sleep, clock=fake_time.clock, rng=lambda: 0.5
    )


@responses.activate
def test_pagination_follows_link_header(gh_settings, fake_time):
    responses.get(
        f"{API}/repos/o/r/issues",
        json=[{"id": 1}, {"id": 2}],
        headers={"Link": f'<{API}/repos/o/r/issues?page=2>; rel="next"'},
    )
    responses.get(f"{API}/repos/o/r/issues?page=2", json=[{"id": 3}])
    result = make_client(gh_settings, fake_time).paginate("/repos/o/r/issues", {"state": "all"})
    assert [i["id"] for i in result.items] == [1, 2, 3]
    assert result.pages == 2
    assert result.complete is True
    first = responses.calls[0].request
    assert "per_page=2" in first.url and "state=all" in first.url
    assert first.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert "Authorization" not in first.headers


@responses.activate
def test_token_sent_as_bearer(gh_settings, fake_time):
    responses.get(f"{API}/repos/o/r", json={"id": 1})
    make_client(gh_settings, fake_time, token="ghp_" + "x" * 36).get_object("/repos/o/r")
    assert responses.calls[0].request.headers["Authorization"].startswith("Bearer ghp_")


@responses.activate
def test_empty_first_page_is_complete_and_empty(gh_settings, fake_time):
    responses.get(f"{API}/repos/o/r/pulls", json=[])
    result = make_client(gh_settings, fake_time).paginate("/repos/o/r/pulls")
    assert result.items == [] and result.complete and result.pages == 1


@responses.activate
def test_page_cap_marks_incomplete(gh_settings, fake_time):
    from dataclasses import replace

    settings = replace(gh_settings, max_pages_per_series=2)
    for page in (1, 2, 3):
        url = f"{API}/x" if page == 1 else f"{API}/x?page={page}"
        responses.get(url, json=[{"id": page}],
                      headers={"Link": f'<{API}/x?page={page + 1}>; rel="next"'})  # fmt: skip
    result = make_client(settings, fake_time).paginate("/x")
    assert result.pages == 2
    assert result.complete is False
    assert len(responses.calls) == 2


@responses.activate
def test_stop_when_ends_paging_early(gh_settings, fake_time):
    responses.get(f"{API}/x", json=[{"id": 1}], headers={"Link": f'<{API}/x?page=2>; rel="next"'})
    result = make_client(gh_settings, fake_time).paginate("/x", stop_when=lambda page: True)
    assert result.stopped_early and result.complete and len(responses.calls) == 1


@responses.activate
def test_retries_5xx_then_succeeds_with_backoff(gh_settings, fake_time):
    responses.get(f"{API}/x", status=502)
    responses.get(f"{API}/x", status=503)
    responses.get(f"{API}/x", json={"ok": True})
    client = make_client(gh_settings, fake_time)
    assert client.get_object("/x") == {"ok": True}
    assert fake_time.sleeps == [pytest.approx(1.5), pytest.approx(3.0)]  # 2s, 4s x 0.75 jitter
    assert client.request_count == 3


@responses.activate
def test_retry_exhaustion_raises_transient_never_empty(gh_settings, fake_time):
    for _ in range(4):
        responses.get(f"{API}/x", status=500)
    with pytest.raises(GitHubTransientError, match="after 4 attempts"):
        make_client(gh_settings, fake_time).paginate("/x")
    assert len(responses.calls) == 4  # 1 attempt + max_retries (3)


@responses.activate
def test_timeouts_are_retried_then_raise(gh_settings, fake_time):
    for _ in range(4):
        responses.get(f"{API}/x", body=requests.Timeout("slow"))
    with pytest.raises(GitHubTransientError, match="Timeout"):
        make_client(gh_settings, fake_time).get_object("/x")
    assert len(fake_time.sleeps) == 3


@responses.activate
def test_secondary_limit_honours_retry_after(gh_settings, fake_time):
    responses.get(f"{API}/x", status=403, headers={"Retry-After": "37"},
                  json={"message": "You have exceeded a secondary rate limit"})  # fmt: skip
    responses.get(f"{API}/x", json={"ok": 1})
    client = make_client(gh_settings, fake_time)
    assert client.get_object("/x") == {"ok": 1}
    assert fake_time.sleeps == [37.0]
    assert client.rate_limit_waits == 1


@responses.activate
def test_429_retry_after(gh_settings, fake_time):
    responses.get(f"{API}/x", status=429, headers={"Retry-After": "5"})
    responses.get(f"{API}/x", json={"ok": 1})
    make_client(gh_settings, fake_time).get_object("/x")
    assert fake_time.sleeps == [5.0]


@responses.activate
def test_primary_limit_waits_until_reset(gh_settings, fake_time):
    reset = int(fake_time.now) + 120
    responses.get(f"{API}/x", status=403, json={"message": "API rate limit exceeded"},
                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})  # fmt: skip
    responses.get(f"{API}/x", json={"ok": 1})
    make_client(gh_settings, fake_time).get_object("/x")
    assert fake_time.sleeps == [pytest.approx(121.0)]


@responses.activate
def test_primary_limit_beyond_max_wait_raises(gh_settings, fake_time):
    reset = int(fake_time.now) + 3600
    responses.get(f"{API}/x", status=403,
                  headers={"X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(reset)})  # fmt: skip
    with pytest.raises(RateLimitExceededError, match="exceeds the configured maximum"):
        make_client(gh_settings, fake_time).get_object("/x")
    assert fake_time.sleeps == []


@responses.activate
def test_secondary_limit_without_headers_waits_at_least_a_minute(gh_settings, fake_time):
    responses.get(f"{API}/x", status=403, json={"message": "secondary rate limit"})
    responses.get(f"{API}/x", json={"ok": 1})
    make_client(gh_settings, fake_time).get_object("/x")
    assert fake_time.sleeps == [60.0]


@responses.activate
def test_rate_limit_retries_are_bounded(gh_settings, fake_time):
    for _ in range(5):
        responses.get(f"{API}/x", status=429, headers={"Retry-After": "1"})
    with pytest.raises(RateLimitExceededError, match="still rate limited"):
        make_client(gh_settings, fake_time).get_object("/x")
    assert len(responses.calls) == 4


@responses.activate
def test_plain_403_is_client_error(gh_settings, fake_time):
    responses.get(f"{API}/x", status=403, json={"message": "Resource not accessible"})
    with pytest.raises(GitHubClientError):
        make_client(gh_settings, fake_time).get_object("/x")


@pytest.mark.parametrize(
    ("status", "error"),
    [(401, GitHubAuthError), (404, GitHubNotFoundError), (410, GitHubNotFoundError),
     (422, GitHubClientError)],
)  # fmt: skip
@responses.activate
def test_non_retryable_statuses(gh_settings, fake_time, status, error):
    responses.get(f"{API}/x", status=status, json={"message": "no"})
    with pytest.raises(error):
        make_client(gh_settings, fake_time).get_object("/x")
    assert len(responses.calls) == 1


@responses.activate
def test_invalid_json_raises_response_error(gh_settings, fake_time):
    responses.get(f"{API}/x", body="<html>not json</html>", status=200)
    with pytest.raises(GitHubResponseError, match="Invalid JSON"):
        make_client(gh_settings, fake_time).get_object("/x")


@responses.activate
def test_unexpected_payload_shape_raises(gh_settings, fake_time):
    responses.get(f"{API}/x", json={"message": "an object, not a list"})
    with pytest.raises(GitHubResponseError, match="array"):
        make_client(gh_settings, fake_time).paginate("/x")
    responses.get(f"{API}/y", json=[1, 2, 3])
    with pytest.raises(GitHubResponseError):
        make_client(gh_settings, fake_time).paginate("/y")


@responses.activate
def test_refuses_pagination_links_to_other_hosts(gh_settings, fake_time):
    responses.get(f"{API}/x", json=[{"id": 1}],
                  headers={"Link": '<https://evil.example.com/x?page=2>; rel="next"'})  # fmt: skip
    with pytest.raises(GitHubResponseError, match="another host"):
        make_client(gh_settings, fake_time).paginate("/x")


@responses.activate
def test_proactive_throttle_when_budget_low(gh_settings, fake_time):
    reset = int(fake_time.now) + 30
    responses.get(f"{API}/x", json={"ok": 1},
                  headers={"X-RateLimit-Remaining": "3", "X-RateLimit-Reset": str(reset)})  # fmt: skip
    make_client(gh_settings, fake_time).get_object("/x")
    assert fake_time.sleeps == [pytest.approx(31.0)]


@responses.activate
def test_rate_limit_preflight_is_non_fatal_on_server_error(gh_settings, fake_time):
    for _ in range(4):
        responses.get(f"{API}/rate_limit", status=500)
    assert make_client(gh_settings, fake_time).rate_limit_snapshot() is None
