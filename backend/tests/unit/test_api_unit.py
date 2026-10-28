"""Public API behaviour that does not need a database: validation, read-only, CORS, limits."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from oss_health.api.main import RateLimiter, create_app
from oss_health.api.settings import ApiSettings
from oss_health.errors import ConfigError

ORIGIN = "https://oss-health.example.vercel.app"


class ExplodingConn:
    """Any query reaching the database in these tests is a bug."""

    def execute(self, *args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("unexpected database access")


@pytest.fixture
def client():
    app = create_app(ApiSettings(cors_allowed_origins=(ORIGIN,), rate_limit_per_minute=1000))
    app.dependency_overrides[app.state.get_conn] = lambda: ExplodingConn()
    return TestClient(app, raise_server_exceptions=False)


def test_live(client):
    r = client.get("/health/live")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert r.headers["x-content-type-options"] == "nosniff"


def test_ready_without_database_is_503(client):
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["status"] == "unavailable"


@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", ["/api/v1/runs", "/api/v1/pipeline/run", "/api/v1/summary"])
def test_write_methods_rejected(client, method, path):
    r = getattr(client, method)(path)
    assert r.status_code == 405
    assert r.json() == {"error": "method_not_allowed"}


def test_no_pipeline_or_admin_routes(client):
    paths = {route.path for route in client.app.routes}
    assert not any("pipeline" in p or "admin" in p or "migrat" in p or "sql" in p for p in paths)
    for p in ("/api/v1/pipeline/run", "/api/v1/admin", "/api/v1/sql"):
        r = client.get(p)
        assert r.status_code == 404 and r.json() == {"error": "not_found"}


@pytest.mark.parametrize(
    "url",
    [
        "/api/v1/repositories?limit=1000",
        "/api/v1/repositories?limit=0",
        "/api/v1/repositories?offset=-1",
        "/api/v1/repositories?sort=password",
        "/api/v1/repositories?order=sideways",
        "/api/v1/repositories?search=%27%3B%20DROP%20TABLE%20x%3B--",
        "/api/v1/repositories/0",
        "/api/v1/repositories/abc",
        "/api/v1/analytics/activity?weeks=500",
        "/api/v1/analytics/issues?days=5000",
        "/api/v1/analytics/pull-requests?days=1",
        "/api/v1/analytics/stars-forks?start=not-a-date",
        "/api/v1/runs?limit=101",
        "/api/v1/summary?days=0",
    ],
)
def test_invalid_parameters_are_rejected_without_db_access(client, url):
    r = client.get(url)
    assert r.status_code == 422, (url, r.text)
    body = r.json()
    assert body["error"] == "invalid_request"
    assert "Traceback" not in r.text


def test_date_range_bounds(client):
    r = client.get("/api/v1/analytics/stars-forks?start=2020-01-01&end=2026-01-01")
    assert r.status_code == 422 and r.json()["error"] == "date_range_too_large"
    r = client.get("/api/v1/analytics/stars-forks?start=2026-02-01&end=2026-01-01")
    assert r.json()["error"] == "start_after_end"


def test_oversized_query_string(client):
    r = client.get("/api/v1/repositories?search=" + "a" * 2000)
    assert r.status_code == 414


def test_cors_allows_only_configured_origin(client):
    ok = client.options("/api/v1/summary", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "GET"})  # fmt: skip
    assert ok.headers.get("access-control-allow-origin") == ORIGIN
    evil = client.options("/api/v1/summary", headers={
        "Origin": "https://evil.example.com", "Access-Control-Request-Method": "GET"})  # fmt: skip
    assert "access-control-allow-origin" not in evil.headers
    post = client.options("/api/v1/summary", headers={
        "Origin": ORIGIN, "Access-Control-Request-Method": "POST"})  # fmt: skip
    assert post.status_code == 400


def test_unexpected_error_is_generic():
    app = create_app(ApiSettings(rate_limit_per_minute=0))

    class Boom:
        def execute(self, *a, **k):
            raise RuntimeError("secret internals: postgresql://u:pw@host/db")

    app.dependency_overrides[app.state.get_conn] = lambda: Boom()
    r = TestClient(app, raise_server_exceptions=False).get("/api/v1/runs")
    assert r.status_code == 500 and r.json() == {"error": "internal_error"}
    assert "postgresql" not in r.text


def test_numeric_sums_serialize_as_integers():
    from decimal import Decimal

    from oss_health.api.queries import _clean

    assert _clean(Decimal("158")) == 158 and isinstance(_clean(Decimal("158")), int)
    assert _clean(Decimal("0.524675")) == pytest.approx(0.524675)
    assert _clean(None) is None


def test_client_key_prefers_vercel_header_and_ignores_spoofable_entries():
    from starlette.requests import Request

    from oss_health.api.main import _client_key

    def request(headers: dict[str, str]) -> Request:
        raw = [(k.lower().encode(), v.encode()) for k, v in headers.items()]
        return Request({"type": "http", "headers": raw, "client": ("10.0.0.1", 1234)})

    assert _client_key(request({"x-vercel-forwarded-for": "203.0.113.7",
                                "x-forwarded-for": "1.1.1.1, 203.0.113.7"})) == "203.0.113.7"  # fmt: skip
    assert _client_key(request({"x-forwarded-for": "6.6.6.6, 198.51.100.2"})) == "198.51.100.2"
    assert _client_key(request({})) == "10.0.0.1"


def test_rate_limiter_window():
    limiter = RateLimiter(per_minute=3)
    assert [limiter.allow("1.2.3.4", now=60.0) for _ in range(4)] == [True, True, True, False]
    assert limiter.allow("5.6.7.8", now=60.0)
    assert limiter.allow("1.2.3.4", now=125.0)  # next window
    assert RateLimiter(per_minute=0).allow("x")


def test_rate_limit_returns_429():
    app = create_app(ApiSettings(rate_limit_per_minute=2))
    app.dependency_overrides[app.state.get_conn] = lambda: ExplodingConn()
    c = TestClient(app, raise_server_exceptions=False)
    codes = [c.get("/api/v1/runs?limit=0").status_code for _ in range(3)]
    assert codes == [422, 422, 429]
    assert c.get("/health/live").status_code == 200  # health is never rate limited


def test_api_settings_validation():
    s = ApiSettings.from_env({"CORS_ALLOWED_ORIGINS": f"{ORIGIN}, http://localhost:5173",
                              "RATE_LIMIT_PER_MINUTE": "60"})  # fmt: skip
    assert s.cors_allowed_origins == (ORIGIN, "http://localhost:5173")
    with pytest.raises(ConfigError):
        ApiSettings.from_env({"CORS_ALLOWED_ORIGINS": "*"})
    with pytest.raises(ConfigError):
        ApiSettings.from_env({"CORS_ALLOWED_ORIGINS": "http://evil.example.com"})
    with pytest.raises(ConfigError):
        ApiSettings.from_env({"CORS_ALLOW_ORIGIN_REGEX": ".*"})
    with pytest.raises(ConfigError):
        ApiSettings.from_env({"RATE_LIMIT_PER_MINUTE": "lots"})
    assert ApiSettings.from_env({}).database_url is None
