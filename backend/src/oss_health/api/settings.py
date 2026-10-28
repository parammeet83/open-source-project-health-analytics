"""API runtime settings, read from environment variables only."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field

from oss_health.errors import ConfigError


@dataclass(frozen=True)
class ApiSettings:
    database_url: str | None = field(default=None, repr=False)
    cors_allowed_origins: tuple[str, ...] = ()
    cors_allow_origin_regex: str | None = None
    rate_limit_per_minute: int = 120
    stale_after_hours: int = 36
    # 3 Cloud Run instances x (2 + 1) = 9 connections, within the reader role's limit of 10.
    db_pool_size: int = 2
    db_max_overflow: int = 1
    statement_timeout_ms: int = 5000
    expose_docs: bool = True

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> ApiSettings:
        env = dict(os.environ if env is None else env)
        origins = tuple(
            o.strip().rstrip("/")
            for o in env.get("CORS_ALLOWED_ORIGINS", "").split(",")
            if o.strip()
        )
        for origin in origins:
            if not re.match(r"^https://[A-Za-z0-9.-]+(:\d+)?$", origin) and not re.match(
                r"^http://(localhost|127\.0\.0\.1)(:\d+)?$", origin
            ):
                raise ConfigError(f"Invalid CORS origin {origin!r}")
        regex = env.get("CORS_ALLOW_ORIGIN_REGEX", "").strip() or None
        if regex is not None:
            if not regex.startswith("^https://") or not regex.endswith("$"):
                raise ConfigError("CORS_ALLOW_ORIGIN_REGEX must be anchored and https-only")
            re.compile(regex)

        def _int(name: str, default: int, low: int, high: int) -> int:
            raw = env.get(name)
            if raw is None or raw == "":
                return default
            try:
                value = int(raw)
            except ValueError as exc:
                raise ConfigError(f"{name} must be an integer") from exc
            if not low <= value <= high:
                raise ConfigError(f"{name} must be between {low} and {high}")
            return value

        return cls(
            database_url=(env.get("API_DATABASE_URL") or "").strip() or None,
            cors_allowed_origins=origins,
            cors_allow_origin_regex=regex,
            rate_limit_per_minute=_int("RATE_LIMIT_PER_MINUTE", 120, 0, 10_000),
            stale_after_hours=_int("STALE_AFTER_HOURS", 36, 1, 720),
            db_pool_size=_int("DB_POOL_SIZE", 2, 1, 20),
            db_max_overflow=_int("DB_MAX_OVERFLOW", 1, 0, 20),
            statement_timeout_ms=_int("API_STATEMENT_TIMEOUT_MS", 5000, 100, 60_000),
            expose_docs=env.get("EXPOSE_API_DOCS", "true").lower() in ("1", "true", "yes"),
        )
