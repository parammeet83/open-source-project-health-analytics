"""CLI database commands against the dedicated test database."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from oss_health.cli import main

from .conftest import ROLE_PASSWORDS

pytestmark = pytest.mark.integration


@pytest.fixture
def migration_env(monkeypatch, engine, db_url):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", db_url.render_as_string(hide_password=False))
    monkeypatch.setenv("OSS_HEALTH_APP_PASSWORD", ROLE_PASSWORDS["oss_health_app"])
    monkeypatch.setenv("OSS_HEALTH_READER_PASSWORD", ROLE_PASSWORDS["oss_health_reader"])
    return engine


def test_db_upgrade_is_idempotent(migration_env, capsys):
    assert main(["db", "upgrade"]) == 0
    assert "Migrations applied" in capsys.readouterr().out


def test_bootstrap_and_verify_roles(migration_env, capsys):
    assert main(["db", "bootstrap-roles"]) == 0
    out = capsys.readouterr().out
    assert "Grants applied." in out and "FAIL" not in out
    assert ROLE_PASSWORDS["oss_health_app"] not in out
    with migration_env.connect() as conn:
        settings = conn.execute(
            text("SELECT rolconnlimit, rolconfig FROM pg_roles WHERE rolname = 'oss_health_reader'")
        ).one()
    assert settings.rolconnlimit == 10
    assert "default_transaction_read_only=on" in settings.rolconfig
    assert main(["db", "verify-roles"]) == 0


def test_bootstrap_requires_strong_passwords(migration_env, monkeypatch):
    monkeypatch.setenv("OSS_HEALTH_READER_PASSWORD", "short")
    assert main(["db", "bootstrap-roles"]) == 3


def test_db_commands_require_migration_url():
    # The autouse fixture removes MIGRATION_DATABASE_URL / DATABASE_URL_UNPOOLED and env files.
    assert main(["db", "upgrade"]) == 3


def test_neon_unpooled_url_is_the_migration_fallback(monkeypatch, db_url, engine):
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", db_url.render_as_string(hide_password=False))
    assert main(["db", "upgrade"]) == 0


def test_bootstrap_write_env_creates_role_urls(migration_env, monkeypatch, tmp_path, capsys):
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    target = tmp_path / ".env.roles"
    monkeypatch.setattr("oss_health.cli.ROLES_ENV_FILE", target)
    assert main(["db", "bootstrap-roles", "--write-env"]) == 0
    out = capsys.readouterr().out
    values = dict(
        line.split("=", 1)
        for line in target.read_text().splitlines()
        if "=" in line and not line.startswith("#")
    )
    assert set(values) == {"OSS_HEALTH_APP_PASSWORD", "OSS_HEALTH_READER_PASSWORD",
                           "PIPELINE_DATABASE_URL", "API_DATABASE_URL"}  # fmt: skip
    for secret in values.values():
        assert secret not in out
    reader = make_url(values["API_DATABASE_URL"])
    assert reader.username == "oss_health_reader"
    engine = create_engine(reader.set(drivername="postgresql+psycopg"))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT current_user")).scalar_one() == "oss_health_reader"
    engine.dispose()
