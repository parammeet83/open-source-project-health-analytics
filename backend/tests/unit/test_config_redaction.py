"""Configuration loading, database URL safety and secret redaction."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from oss_health.config import DEFAULT_CONFIG_PATH, load_settings
from oss_health.db import assert_safe_test_database, normalize_database_url, safe_url_display
from oss_health.errors import ConfigError, UnsafeDatabaseTargetError
from oss_health.logging_setup import configure_logging
from oss_health.redaction import redact, register_secret, safe_error_message

TOKEN = "ghp_" + "A1b2C3d4" * 5


def write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pipeline.toml"
    path.write_text(body, encoding="utf-8")
    return path


def test_default_config_loads_without_token_or_database():
    settings = load_settings(DEFAULT_CONFIG_PATH, env={})
    assert "psf/requests" in settings.repositories
    assert "microsoft/vscode" in settings.repositories
    assert settings.github_token is None and settings.database_url is None
    assert settings.extraction.history_days == 90
    assert settings.extraction.incremental_overlap_hours == 24


def test_secrets_never_in_public_dict(tmp_path):
    settings = load_settings(DEFAULT_CONFIG_PATH, env={
        "GITHUB_TOKEN": TOKEN,
        "PIPELINE_DATABASE_URL": "postgresql://app:s3cretpassword@db.example.com/x",
    })  # fmt: skip
    text = str(settings.public_dict()) + repr(settings)
    assert TOKEN not in text and "s3cretpassword" not in text
    assert settings.public_dict()["github_token_configured"] is True


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("[pipeline]\nrepositories = []", "at least one"),
        ('[pipeline]\nrepositories = ["not a repo"]', "Invalid repository"),
        ('[pipeline]\nrepositories = ["a/b", "A/B"]', "more than once"),
        ('[pipeline]\nrepositories = ["a/b"]\n[github]\nper_page = 500', "per_page"),
        ('[pipeline]\nrepositories = ["a/b"]\n[github]\nunknown = 1', "Unknown setting"),
        ('[pipeline]\nrepositories = ["a/b"]\n[github]\nper_page = "x"', "must be int"),
        ('[pipeline]\nrepositories = ["a/b"]\n[nope]\nx = 1', "Unknown config sections"),
        ('[pipeline]\nrepositories = ["a/b"]\n[github]\napi_base_url = "http://x"', "https"),
        ("not = [valid toml", "not valid TOML"),
    ],
)
def test_invalid_config(tmp_path, body, message):
    with pytest.raises(ConfigError, match=message):
        load_settings(write(tmp_path, body), env={})


def test_require_token(tmp_path):
    path = write(tmp_path, '[pipeline]\nrepositories = ["a/b"]\n[github]\nrequire_token = true')
    with pytest.raises(ConfigError, match="GITHUB_TOKEN"):
        load_settings(path, env={})
    assert load_settings(path, env={"GITHUB_TOKEN": TOKEN}).github_token == TOKEN


def test_missing_config_file(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_settings(tmp_path / "missing.toml", env={})


def test_remote_database_requires_tls():
    url = normalize_database_url("postgresql://u:p@ep-x.neon.tech/db")
    assert url.query["sslmode"] == "require"
    assert url.drivername == "postgresql+psycopg"
    kept = normalize_database_url("postgres://u:p@ep-x.neon.tech/db?sslmode=verify-full")
    assert kept.query["sslmode"] == "verify-full"
    with pytest.raises(ConfigError, match="TLS"):
        normalize_database_url("postgresql://u:p@ep-x.neon.tech/db?sslmode=disable")
    local = normalize_database_url("postgresql://u:p@localhost:5432/db")
    assert "sslmode" not in local.query
    assert "p@" not in safe_url_display("postgresql://u:p@localhost/db")


def test_test_database_guard():
    assert_safe_test_database("postgresql://u@localhost/osp_health_test")
    with pytest.raises(UnsafeDatabaseTargetError, match="does not end"):
        assert_safe_test_database("postgresql://u@localhost/osp_health_dev")
    with pytest.raises(UnsafeDatabaseTargetError, match="non-local"):
        assert_safe_test_database("postgresql://u@ep-x.neon.tech/neondb_test")
    with pytest.raises(UnsafeDatabaseTargetError, match="same as"):
        assert_safe_test_database(
            "postgresql://u@localhost:5432/osp_health_test",
            ["postgresql://other@localhost:5432/osp_health_test"],
        )


@pytest.mark.parametrize(
    "secret_text",
    [
        f"token {TOKEN}",
        "github_pat_" + "x" * 40,
        "Authorization: Bearer abcdefghijklmnop",
        "postgresql://user:hunter2hunter2@host/db",
        "password=hunter2hunter2",
        "gho_" + "z" * 36,
    ],
)
def test_redaction_patterns(secret_text):
    out = redact(f"error while using {secret_text} ok")
    assert "***" in out
    for fragment in ("hunter2hunter2", "A1b2C3d4A1b2", "xxxxxxxxxx", "abcdefghijklmnop", "zzzzzz"):
        assert fragment not in out


def test_registered_secret_is_masked_anywhere():
    register_secret("plain-looking-value-123")
    assert "plain-looking-value-123" not in redact("value=plain-looking-value-123;")
    assert "***" in safe_error_message(RuntimeError("x plain-looking-value-123 y"))


def test_logging_redacts_messages_and_args(caplog, tmp_path):
    configure_logging("INFO", tmp_path / "logs")
    log_file = next((tmp_path / "logs").glob("*.log"))
    logger = logging.getLogger("oss_health.test")
    logger.addHandler(caplog.handler)
    caplog.handler.addFilter(logging.getLogger().handlers[0].filters[0])
    logger.error("connecting with %s", f"postgresql://app:{'q' * 16}@h/db and {TOKEN}")
    for handler in logging.getLogger().handlers:
        handler.flush()
    text = caplog.text + log_file.read_text(encoding="utf-8")
    assert TOKEN not in text and "q" * 16 not in text
    assert "***" in text
    logger.removeHandler(caplog.handler)
