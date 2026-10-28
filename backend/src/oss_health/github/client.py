"""GitHub REST client with timeouts, bounded retries, rate-limit handling and pagination.

No failure path returns an empty result: every error raises a typed ``GitHubError``.
"""

from __future__ import annotations

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin

import requests

from oss_health.config import GitHubSettings
from oss_health.errors import (
    GitHubAuthError,
    GitHubClientError,
    GitHubNotFoundError,
    GitHubResponseError,
    GitHubTransientError,
    RateLimitExceededError,
)

logger = logging.getLogger(__name__)

RETRYABLE_STATUS = {500, 502, 503, 504}


@dataclass
class PageResult:
    """Items from a paginated series plus completeness information."""

    items: list[dict[str, Any]] = field(default_factory=list)
    pages: int = 0
    complete: bool = True
    stopped_early: bool = False


def _int_header(response: requests.Response, name: str) -> int | None:
    value = response.headers.get(name)
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None


class GitHubClient:
    def __init__(
        self,
        settings: GitHubSettings,
        token: str | None = None,
        *,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
        rng: Callable[[], float] = random.random,
    ) -> None:
        self.settings = settings
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": settings.api_version,
                "User-Agent": settings.user_agent,
            }
        )
        if token:
            self._session.headers["Authorization"] = f"Bearer {token}"
        self._sleep = sleep
        self._clock = clock
        self._rng = rng
        self.request_count = 0
        self.rate_limit_remaining: int | None = None
        self.rate_limit_reset: int | None = None
        self.rate_limit_waits = 0
        self.redirects: list[str] = []

    # ------------------------------------------------------------------ helpers
    def _url(self, path_or_url: str) -> str:
        if path_or_url.startswith("https://"):
            if not path_or_url.startswith(self.settings.api_base_url):
                raise GitHubResponseError("Refusing to follow a pagination link to another host")
            return path_or_url
        return urljoin(self.settings.api_base_url.rstrip("/") + "/", path_or_url.lstrip("/"))

    def _backoff(self, attempt: int) -> float:
        base = min(
            self.settings.backoff_max_seconds,
            self.settings.backoff_base_seconds * (2**attempt),
        )
        # "Equal jitter": between 50% and 100% of the exponential delay.
        return base * (0.5 + self._rng() / 2)

    def _record_rate_headers(self, response: requests.Response) -> None:
        remaining = _int_header(response, "X-RateLimit-Remaining")
        reset = _int_header(response, "X-RateLimit-Reset")
        if remaining is not None:
            self.rate_limit_remaining = remaining
        if reset is not None:
            self.rate_limit_reset = reset

    def rate_limit_wait_seconds(self, response: requests.Response, attempt: int) -> float | None:
        """Seconds to wait for a 403/429 response, or None if it is not a rate limit."""
        retry_after = response.headers.get("Retry-After")
        if retry_after is not None:
            try:
                return max(0.0, float(retry_after))
            except ValueError:
                pass
        remaining = _int_header(response, "X-RateLimit-Remaining")
        reset = _int_header(response, "X-RateLimit-Reset")
        if remaining == 0 and reset is not None:
            return max(0.0, reset - self._clock()) + 1.0
        body = (response.text or "")[:2000].lower()
        if response.status_code == 429 or "rate limit" in body or "abuse" in body:
            # Secondary limit without guidance: wait at least a minute, growing exponentially.
            return self.settings.secondary_limit_min_wait_seconds * (2**attempt)
        return None

    def _throttle_if_low(self) -> None:
        remaining, reset = self.rate_limit_remaining, self.rate_limit_reset
        if remaining is None or reset is None or remaining >= self.settings.rate_limit_buffer:
            return
        wait = max(0.0, reset - self._clock()) + 1.0
        if wait <= self.settings.max_rate_limit_wait_seconds:
            logger.warning(
                "Rate limit nearly exhausted (%s left); waiting %.0fs for reset", remaining, wait
            )
            self.rate_limit_waits += 1
            self._sleep(wait)
            self.rate_limit_remaining = None

    # ------------------------------------------------------------------ requests
    def request(self, path_or_url: str, params: dict[str, Any] | None = None) -> requests.Response:
        """GET with bounded retries. Returns a 2xx response or raises a GitHubError."""
        url = self._url(path_or_url)
        timeout = (self.settings.connect_timeout_seconds, self.settings.read_timeout_seconds)
        max_retries = self.settings.max_retries
        attempt = 0
        while True:
            try:
                response = self._session.get(url, params=params, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                if attempt >= max_retries:
                    raise GitHubTransientError(
                        f"GET {url} failed after {attempt + 1} attempts: {type(exc).__name__}"
                    ) from exc
                delay = self._backoff(attempt)
                logger.warning("GET %s: %s; retrying in %.1fs", url, type(exc).__name__, delay)
                self._sleep(delay)
                attempt += 1
                continue

            self.request_count += 1
            self._record_rate_headers(response)
            status = response.status_code

            if 200 <= status < 300:
                if response.history:
                    self.redirects.append(url)
                self._throttle_if_low()
                return response

            if status in (403, 429):
                wait = self.rate_limit_wait_seconds(response, attempt)
                if wait is None:
                    raise GitHubClientError(f"GET {url} returned {status} (forbidden)")
                if wait > self.settings.max_rate_limit_wait_seconds:
                    raise RateLimitExceededError(
                        f"Rate limit wait of {wait:.0f}s exceeds the configured maximum of "
                        f"{self.settings.max_rate_limit_wait_seconds:.0f}s"
                    )
                if attempt >= max_retries:
                    raise RateLimitExceededError(
                        f"GET {url} still rate limited after {attempt + 1} attempts"
                    )
                logger.warning("GET %s rate limited (%s); waiting %.0fs", url, status, wait)
                self.rate_limit_waits += 1
                self._sleep(wait)
                # The budget seen before the wait is stale once the window has reset.
                self.rate_limit_remaining = None
                attempt += 1
                continue

            if status == 401:
                raise GitHubAuthError("GitHub rejected the credentials (401)")
            if status in (404, 410):
                raise GitHubNotFoundError(f"GET {url} returned {status}")
            if status in RETRYABLE_STATUS:
                if attempt >= max_retries:
                    raise GitHubTransientError(
                        f"GET {url} returned {status} after {attempt + 1} attempts"
                    )
                delay = self._backoff(attempt)
                logger.warning("GET %s returned %s; retrying in %.1fs", url, status, delay)
                self._sleep(delay)
                attempt += 1
                continue
            raise GitHubClientError(f"GET {url} returned {status}")

    @staticmethod
    def _json(response: requests.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubResponseError(f"Invalid JSON from {response.url}") from exc

    def get_object(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        data = self._json(self.request(path, params))
        if not isinstance(data, dict):
            raise GitHubResponseError(f"Expected a JSON object from {path}")
        return data

    def paginate(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        *,
        stop_when: Callable[[list[dict[str, Any]]], bool] | None = None,
        max_pages: int | None = None,
    ) -> PageResult:
        """Follow ``Link: rel="next"`` until exhausted, ``stop_when`` is true, or the page cap.

        Reaching the page cap with more pages available sets ``complete = False``.
        """
        cap = max_pages or self.settings.max_pages_per_series
        query = dict(params or {})
        query.setdefault("per_page", self.settings.per_page)
        result = PageResult()
        next_url: str | None = path
        next_params: dict[str, Any] | None = query
        while next_url is not None:
            if result.pages >= cap:
                result.complete = False
                logger.warning("Page cap of %s reached for %s; series incomplete", cap, path)
                break
            response = self.request(next_url, next_params)
            page = self._json(response)
            if not isinstance(page, list) or not all(isinstance(i, dict) for i in page):
                raise GitHubResponseError(f"Expected a JSON array of objects from {path}")
            result.pages += 1
            result.items.extend(page)
            link = response.links.get("next", {}).get("url")
            next_url, next_params = (link, None) if link else (None, None)
            if next_url is not None and stop_when is not None and stop_when(page):
                result.stopped_early = True
                break
        return result

    def rate_limit_snapshot(self) -> dict[str, Any] | None:
        """Preflight call to /rate_limit (does not count against the primary limit)."""
        try:
            data = self.get_object("/rate_limit")
        except (GitHubTransientError, GitHubResponseError, GitHubClientError) as exc:
            logger.warning("Rate-limit preflight failed: %s", exc)
            return None
        core = (data.get("resources") or {}).get("core") or {}
        self.rate_limit_remaining = core.get("remaining", self.rate_limit_remaining)
        self.rate_limit_reset = core.get("reset", self.rate_limit_reset)
        return {"limit": core.get("limit"), "remaining": core.get("remaining")}
