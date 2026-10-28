"""Public, read-only FastAPI analytics API (deployed as a Vercel Function via backend/index.py).

* Only GET/HEAD/OPTIONS routes exist; there is no endpoint that runs the pipeline, executes SQL,
  changes watermarks, runs migrations or mutates data.
* The service connects with ``API_DATABASE_URL`` (the ``oss_health_reader`` role) and every
  session is additionally forced read-only.
* Errors return generic JSON bodies; stack traces, SQL and configuration are never exposed.
"""

# No ``from __future__ import annotations`` here: FastAPI must evaluate the route annotations
# eagerly so that the per-app ``Depends(get_conn)`` closure resolves.
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Path, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import Connection, Engine
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from oss_health import __version__
from oss_health.api import queries
from oss_health.api.settings import ApiSettings
from oss_health.db import create_db_engine
from oss_health.redaction import safe_error_message

logger = logging.getLogger("oss_health.api")

MAX_DATE_RANGE_DAYS = 730
DAYS = Annotated[int, Query(ge=7, le=365, description="Look-back window in days")]
REPO_FILTER = Annotated[int | None, Query(ge=1, le=2**53, description="Repository id filter")]


class RateLimiter:
    """Fixed-window per-client limiter held in memory.

    This is best-effort abuse protection for a single instance only; it is NOT a reliable global
    limit across multiple serverless function instances (see docs/SECURITY.md).
    """

    def __init__(self, per_minute: int, max_clients: int = 10_000) -> None:
        self.per_minute = per_minute
        self.max_clients = max_clients
        self._hits: OrderedDict[str, tuple[int, int]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, client: str, now: float | None = None) -> bool:
        if self.per_minute <= 0:
            return True
        window = int((now or time.time()) // 60)
        with self._lock:
            start, count = self._hits.get(client, (window, 0))
            if start != window:
                start, count = window, 0
            count += 1
            self._hits[client] = (start, count)
            self._hits.move_to_end(client)
            while len(self._hits) > self.max_clients:
                self._hits.popitem(last=False)
            return count <= self.per_minute


def _client_key(request: Request) -> str:
    # Vercel sets x-vercel-forwarded-for to the connecting client's IP (it is not client
    # controlled). Otherwise use the last X-Forwarded-For entry, which the nearest proxy appends;
    # earlier entries are client-controlled and therefore ignored.
    vercel = request.headers.get("x-vercel-forwarded-for", "")
    if vercel:
        return vercel.split(",")[0].strip()[:64]
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[-1].strip()[:64]
    return request.client.host if request.client else "unknown"


def _iso(value: Any) -> Any:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat().replace("+00:00", "Z")
    if isinstance(value, date):
        return value.isoformat()
    return value


def _normalize(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _normalize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_normalize(v) for v in obj]
    return _iso(obj)


def create_app(settings: ApiSettings | None = None, engine: Engine | None = None) -> FastAPI:
    settings = settings or ApiSettings.from_env()
    state: dict[str, Any] = {"engine": engine}

    def get_engine() -> Engine:
        if state["engine"] is None:
            if not settings.database_url:
                raise HTTPException(status_code=503, detail="database_not_configured")
            state["engine"] = create_db_engine(
                settings.database_url,
                application_name="oss-health-api",
                pool_size=settings.db_pool_size,
                max_overflow=settings.db_max_overflow,
                statement_timeout_ms=settings.statement_timeout_ms,
                read_only=True,
            )
        return state["engine"]

    def get_conn() -> Iterator[Connection]:
        with get_engine().connect() as conn:
            yield conn

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        if state["engine"] is not None:
            state["engine"].dispose()

    app = FastAPI(
        title="Open-Source Project Health Analytics API",
        version=__version__,
        description="Public read-only analytics over GitHub repository health data.",
        docs_url="/docs" if settings.expose_docs else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.expose_docs else None,
        lifespan=lifespan,
    )
    limiter = RateLimiter(settings.rate_limit_per_minute)
    app.state.get_conn = get_conn  # exposed for dependency overrides in tests

    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_origin_regex=settings.cors_allow_origin_regex,
        allow_credentials=False,
        allow_methods=["GET", "HEAD", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
        max_age=600,
    )

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            return JSONResponse({"error": "method_not_allowed"}, status_code=405,
                                headers={"Allow": "GET, HEAD, OPTIONS"})  # fmt: skip
        if len(request.url.query) > 1024:
            return JSONResponse({"error": "query_too_long"}, status_code=414)
        if not request.url.path.startswith("/health") and not limiter.allow(_client_key(request)):
            return JSONResponse({"error": "rate_limited"}, status_code=429,
                                headers={"Retry-After": "60"})  # fmt: skip
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "public, max-age=60"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError):
        problems = [
            {"field": ".".join(str(p) for p in e.get("loc", [])[1:]), "message": e.get("msg")}
            for e in exc.errors()[:10]
        ]
        return JSONResponse({"error": "invalid_request", "problems": problems}, status_code=422)

    @app.exception_handler(StarletteHTTPException)
    async def http_handler(_: Request, exc: StarletteHTTPException):
        code = exc.detail if isinstance(exc, HTTPException) else None
        if not isinstance(code, str) or (exc.status_code == 404 and code == "Not Found"):
            code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "error")
        return JSONResponse({"error": code}, status_code=exc.status_code)

    @app.exception_handler(OperationalError)
    async def db_unavailable(_: Request, exc: OperationalError):
        logger.error("Database unavailable: %s", type(exc.orig).__name__ if exc.orig else "error")
        return JSONResponse({"error": "database_unavailable"}, status_code=503)

    @app.exception_handler(SQLAlchemyError)
    async def db_error(_: Request, exc: SQLAlchemyError):
        logger.error("Database error: %s", safe_error_message(exc)[:300])
        return JSONResponse({"error": "internal_error"}, status_code=500)

    @app.exception_handler(Exception)
    async def unhandled(_: Request, exc: Exception):
        logger.error("Unhandled error: %s", type(exc).__name__)
        return JSONResponse({"error": "internal_error"}, status_code=500)

    # ------------------------------------------------------------------ health
    @app.get("/health/live", tags=["health"])
    def live() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    def ready() -> JSONResponse:
        try:
            with get_engine().connect() as conn:
                queries.ping(conn)
        except HTTPException:
            return JSONResponse({"status": "unavailable", "database": "not_configured"}, 503)
        except SQLAlchemyError:
            return JSONResponse({"status": "unavailable", "database": "unreachable"}, 503)
        return JSONResponse({"status": "ok", "database": "ok"})

    # ------------------------------------------------------------------- API v1
    def freshness(conn: Connection) -> dict[str, Any]:
        latest = queries.last_run(conn)
        success = queries.last_run(conn, status="success")
        now = datetime.now(UTC)
        hours = None
        if success and success.get("finished_at_utc"):
            finished = success["finished_at_utc"].replace(tzinfo=UTC)
            hours = round((now - finished).total_seconds() / 3600, 1)
        if success is None:
            data_status = "no_successful_run"
        elif hours is not None and hours > settings.stale_after_hours:
            data_status = "stale"
        elif latest and latest["status"] != "success":
            data_status = "degraded"
        else:
            data_status = "fresh"
        return {
            "status": data_status,
            "hours_since_last_success": hours,
            "stale_after_hours": settings.stale_after_hours,
            "latest_run": latest,
            "last_successful_run": success,
        }

    @app.get("/api/v1/summary", tags=["analytics"])
    def summary(conn: Annotated[Connection, Depends(get_conn)], days: DAYS = 90):
        data = queries.summary_kpis(conn, days)
        data["freshness"] = freshness(conn)
        data["runs"] = queries.run_statistics(conn, 30)
        return _normalize(data)

    @app.get("/api/v1/repositories", tags=["repositories"])
    def repositories(
        conn: Annotated[Connection, Depends(get_conn)],
        search: Annotated[str | None, Query(max_length=100, pattern=r"^[\w .\-/]*$")] = None,
        language: Annotated[str | None, Query(max_length=40, pattern=r"^[\w .+#\-]*$")] = None,
        sort: Literal["stars", "forks", "open_issues", "open_prs", "name"] = "stars",
        order: Literal["asc", "desc"] = "desc",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
    ):
        items, total = queries.list_repositories(
            conn, search=search or None, language=language or None, sort=sort, order=order,
            limit=limit, offset=offset,
        )  # fmt: skip
        return _normalize({"items": items, "total": total, "limit": limit, "offset": offset})

    @app.get("/api/v1/repositories/{repository_id}", tags=["repositories"])
    def repository(
        conn: Annotated[Connection, Depends(get_conn)],
        repository_id: Annotated[int, Path(ge=1, le=2**53)],
    ):
        repo = queries.get_repository(conn, repository_id)
        if repo is None:
            raise HTTPException(status_code=404, detail="repository_not_found")
        return _normalize(repo)

    @app.get("/api/v1/analytics/stars-forks", tags=["analytics"])
    def stars_forks(
        conn: Annotated[Connection, Depends(get_conn)],
        repository_id: REPO_FILTER = None,
        start: date | None = None,
        end: date | None = None,
    ):
        today = datetime.now(UTC).date()
        end = end or today
        start = start or (end - timedelta(days=365))
        if start > end:
            raise HTTPException(status_code=422, detail="start_after_end")
        if (end - start).days > MAX_DATE_RANGE_DAYS:
            raise HTTPException(status_code=422, detail="date_range_too_large")
        rows = queries.stars_forks(conn, repository_id=repository_id, start=start, end=end)
        return _normalize({"start": start, "end": end, "items": rows})

    @app.get("/api/v1/analytics/activity", tags=["analytics"])
    def activity(
        conn: Annotated[Connection, Depends(get_conn)],
        repository_id: REPO_FILTER = None,
        weeks: Annotated[int, Query(ge=1, le=104)] = 26,
    ):
        rows = queries.weekly_activity(conn, repository_id=repository_id, weeks=weeks)
        return _normalize({"weeks": weeks, "items": rows})

    @app.get("/api/v1/analytics/issues", tags=["analytics"])
    def issues(
        conn: Annotated[Connection, Depends(get_conn)],
        repository_id: REPO_FILTER = None,
        days: DAYS = 90,
    ):
        return _normalize(queries.issue_metrics(conn, repository_id=repository_id, days=days))

    @app.get("/api/v1/analytics/pull-requests", tags=["analytics"])
    def pull_request_metrics(
        conn: Annotated[Connection, Depends(get_conn)],
        repository_id: REPO_FILTER = None,
        days: DAYS = 90,
    ):
        return _normalize(
            queries.pull_request_metrics(conn, repository_id=repository_id, days=days)
        )

    @app.get("/api/v1/runs", tags=["pipeline"])
    def runs(
        conn: Annotated[Connection, Depends(get_conn)],
        limit: Annotated[int, Query(ge=1, le=100)] = 20,
    ):
        return _normalize(
            {
                "items": queries.latest_runs(conn, limit),
                "statistics": queries.run_statistics(conn, 30),
            }
        )

    @app.get("/api/v1/quality/latest", tags=["pipeline"])
    def quality_latest(conn: Annotated[Connection, Depends(get_conn)]):
        run = queries.last_run(conn)
        if run is None:
            return _normalize({"run": None, "checks": [], "freshness": freshness(conn)})
        checks = queries.quality_checks_for_run(conn, str(run["run_id"]))
        return _normalize({"run": run, "checks": checks, "freshness": freshness(conn)})

    return app
