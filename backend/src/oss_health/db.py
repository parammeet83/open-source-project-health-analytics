"""Database URL handling, engine creation, TLS enforcement and test-database safety."""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL, make_url

from oss_health.errors import ConfigError, UnsafeDatabaseTargetError

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
SECURE_SSLMODES = {"require", "verify-ca", "verify-full"}
TEST_DB_SUFFIX = "_test"
# Arbitrary constant identifying this pipeline's advisory lock.
PIPELINE_LOCK_KEY = 7_316_220_901


def password_from_url(url: str) -> str | None:
    try:
        return make_url(_with_driver(url)).password
    except Exception:
        return None


def _with_driver(url: str) -> str:
    url = url.strip()
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix) :]
    return url


def normalize_database_url(url: str, *, require_tls_for_remote: bool = True) -> URL:
    """Return a psycopg URL. Remote hosts must use TLS (sslmode=require or stronger)."""
    if not url:
        raise ConfigError("Database URL is empty")
    try:
        parsed = make_url(_with_driver(url))
    except Exception as exc:
        raise ConfigError("Database URL could not be parsed") from exc
    if parsed.get_backend_name() != "postgresql":
        raise ConfigError("Only PostgreSQL database URLs are supported")
    if parsed.drivername != "postgresql+psycopg":
        parsed = parsed.set(drivername="postgresql+psycopg")
    host = (parsed.host or "").lower()
    if require_tls_for_remote and host and host not in LOCAL_HOSTS:
        sslmode = parsed.query.get("sslmode")
        if sslmode is None:
            parsed = parsed.update_query_dict({"sslmode": "require"})
        elif sslmode not in SECURE_SSLMODES:
            raise ConfigError(
                f"Remote database connections must use TLS; sslmode={sslmode!r} is not allowed"
            )
    return parsed


def safe_url_display(url: str | URL) -> str:
    parsed = url if isinstance(url, URL) else make_url(_with_driver(url))
    return parsed.render_as_string(hide_password=True)


def create_db_engine(
    url: str,
    *,
    application_name: str,
    pool_size: int = 2,
    max_overflow: int = 1,
    statement_timeout_ms: int | None = None,
    read_only: bool = False,
) -> Engine:
    parsed = normalize_database_url(url)
    options: list[str] = ["-c timezone=UTC"]
    if statement_timeout_ms:
        options.append(f"-c statement_timeout={int(statement_timeout_ms)}")
    if read_only:
        options.append("-c default_transaction_read_only=on")
    connect_args = {
        "application_name": application_name,
        "connect_timeout": 10,
        "options": " ".join(options),
    }
    return create_engine(
        parsed,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_pre_ping=True,
        pool_recycle=300,
        pool_timeout=10,
        connect_args=connect_args,
    )


def assert_safe_test_database(
    test_url: str, other_urls: Iterable[str | None] = (), *, verify_engine: Engine | None = None
) -> URL:
    """Refuse destructive test operations unless the target is a dedicated local test DB.

    Rules: the database name must end in ``_test``; the host must be local (developer machine or
    CI service container); the URL must not match any other configured database; and, when an
    engine is supplied, the *resolved* ``current_database()`` must match the parsed name.
    """
    parsed = normalize_database_url(test_url, require_tls_for_remote=False)
    name = parsed.database or ""
    if not name.endswith(TEST_DB_SUFFIX):
        raise UnsafeDatabaseTargetError(
            f"Refusing destructive tests: database {name!r} does not end with {TEST_DB_SUFFIX!r}"
        )
    host = (parsed.host or "").lower()
    if host not in LOCAL_HOSTS:
        raise UnsafeDatabaseTargetError(
            f"Refusing destructive tests against non-local host {host!r}"
        )
    for other in other_urls:
        if not other:
            continue
        other_parsed = normalize_database_url(other, require_tls_for_remote=False)
        if (
            (other_parsed.host or "").lower() == host
            and other_parsed.port == parsed.port
            and other_parsed.database == parsed.database
        ):
            raise UnsafeDatabaseTargetError(
                "Refusing destructive tests: the test database is the same as a configured "
                "development/production database"
            )
    if verify_engine is not None:
        with verify_engine.connect() as conn:
            resolved = conn.execute(text("SELECT current_database()")).scalar_one()
        if resolved != name or not str(resolved).endswith(TEST_DB_SUFFIX):
            raise UnsafeDatabaseTargetError(
                f"Resolved database {resolved!r} does not match the expected test database"
            )
    return parsed
