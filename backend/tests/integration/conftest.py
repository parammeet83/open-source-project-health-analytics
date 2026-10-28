"""Integration fixtures. Destructive setup runs ONLY against a verified dedicated test database.

TEST_DATABASE_URL must point at a local database whose name ends in ``_test`` and which differs
from every other configured database URL; ``current_database()`` is checked after connecting.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

from oss_health.config import BACKEND_DIR
from oss_health.db import assert_safe_test_database, normalize_database_url

TEST_URL = os.environ.get("TEST_DATABASE_URL", "").strip()
OTHER_URL_VARS = (
    "PIPELINE_DATABASE_URL",
    "MIGRATION_DATABASE_URL",
    "API_DATABASE_URL",
    "DEV_DATABASE_URL",
)
ROLE_PASSWORDS = {
    "oss_health_app": "test-only-app-password-not-secret",
    "oss_health_reader": "test-only-reader-password-not-secret",
}
CORE_TABLES = (
    "data_quality_checks",
    "rejected_records",
    "extraction_watermarks",
    "repository_snapshots",
    "github_issues",
    "pull_requests",
    "repositories",
    "pipeline_runs",
)


def _verified_engine() -> tuple[Engine, URL]:
    if not TEST_URL:
        pytest.skip("TEST_DATABASE_URL is not set; integration tests skipped")
    url = normalize_database_url(TEST_URL, require_tls_for_remote=False)
    engine = create_engine(url, connect_args={"options": "-c timezone=UTC"})
    others = [os.environ.get(name) for name in OTHER_URL_VARS]
    assert_safe_test_database(TEST_URL, others, verify_engine=engine)
    return engine, url


def alembic_config(url: URL) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.attributes["database_url"] = url.render_as_string(hide_password=False)
    return cfg


@pytest.fixture(scope="session")
def db_url() -> URL:
    return _verified_engine()[1]


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    engine, url = _verified_engine()
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA IF EXISTS analytics CASCADE"))
        conn.execute(text("DROP SCHEMA IF EXISTS oss_health CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS public.alembic_version"))
        for role, password in ROLE_PASSWORDS.items():
            exists = conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = :r"), {"r": role}
            ).first()
            verb = "ALTER" if exists else "CREATE"
            conn.execute(text(f"{verb} ROLE {role} LOGIN PASSWORD '{password}'"))
    command.upgrade(alembic_config(url), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(engine: Engine) -> Engine:
    """Empty every pipeline table before a test (test database only, verified above)."""
    assert engine.url.database and engine.url.database.endswith("_test")
    with engine.begin() as conn:
        conn.execute(text(
            "TRUNCATE " + ", ".join(f"oss_health.{t}" for t in CORE_TABLES) + " CASCADE"
        ))  # fmt: skip
    return engine


def role_engine(db_url: URL, role: str) -> Engine:
    url = db_url.set(username=role, password=ROLE_PASSWORDS[role])
    return create_engine(url, connect_args={"options": "-c timezone=UTC"})
