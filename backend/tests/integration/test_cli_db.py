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


def test_db_commands_require_migration_url(monkeypatch, tmp_path):
    monkeypatch.delenv("MIGRATION_DATABASE_URL", raising=False)
    monkeypatch.setattr("oss_health.cli.BACKEND_DIR", tmp_path / "backend")  # skip real .env
    assert main(["db", "upgrade"]) == 3
