"""Database URL validation and the secret-safe `oss-health db check-url` command.

All URLs here are synthetic; the password marker must never appear in any output.
"""

from __future__ import annotations

import pytest

from oss_health.cli import main
from oss_health.db import diagnose_database_url, normalize_database_url
from oss_health.errors import ConfigError

SECRET = "npg_FAKE_TEST_ONLY_pw0rd"  # gitleaks:allow (synthetic test value, not a credential)
NEON_DIRECT = (
    f"postgresql://neondb_owner:{SECRET}@ep-misty-rain-a1b2c3d4.ap-southeast-1.aws.neon.tech"
    "/neondb?sslmode=require&channel_binding=require"
)
NEON_POOLED = NEON_DIRECT.replace(".ap-southeast-1", "-pooler.ap-southeast-1")

VALID = {
    "neon direct": NEON_DIRECT,
    "neon pooled": NEON_POOLED,
    "postgres scheme": NEON_DIRECT.replace("postgresql://", "postgres://", 1),
    "trailing newline (from stdin)": NEON_DIRECT + "\n",
    "local without tls": "postgresql://postgres@localhost:5432/osp_health_dev",
}
INVALID = {
    "single quoted": f"'{NEON_DIRECT}'",
    "double quoted": f'"{NEON_DIRECT}"',
    "KEY=VALUE line": f"MIGRATION_DATABASE_URL={NEON_DIRECT}",
    "KEY = VALUE line": f"MIGRATION_DATABASE_URL = {NEON_DIRECT}",
    "psql command": f"psql '{NEON_DIRECT}'",
    "internal space": NEON_DIRECT.replace("/neondb", "/ neondb"),
    "internal newline": NEON_DIRECT.replace("?", "\n?"),
    "mysql scheme": NEON_DIRECT.replace("postgresql://", "mysql://", 1),
    "http scheme": NEON_DIRECT.replace("postgresql://", "https://", 1),
    "no scheme": NEON_DIRECT.split("://", 1)[1],
    "sslmode disable on remote host": NEON_DIRECT.replace("sslmode=require", "sslmode=disable"),
    "empty": "",
    "whitespace only": "   \n",
}


@pytest.mark.parametrize("value", VALID.values(), ids=VALID.keys())
def test_parser_accepts_valid_urls(value):
    url = normalize_database_url(value)
    assert url.drivername == "postgresql+psycopg"
    report = diagnose_database_url(value)
    assert report["present"] and report["expected_scheme"] and report["parser_accepts"]
    assert not report["surrounding_quotes"] and not report["key_prefix"]
    assert not report["internal_whitespace"]


@pytest.mark.parametrize("value", INVALID.values(), ids=INVALID.keys())
def test_parser_rejects_malformed_urls_without_echoing_them(value):
    with pytest.raises(ConfigError) as info:
        normalize_database_url(value)
    assert SECRET not in str(info.value)
    assert diagnose_database_url(value)["parser_accepts"] is False


def test_diagnosis_flags_each_mistake():
    assert diagnose_database_url(f"'{NEON_DIRECT}'")["surrounding_quotes"]
    assert diagnose_database_url(f"KEY={NEON_DIRECT}")["key_prefix"]
    assert diagnose_database_url(f"psql '{NEON_DIRECT}'")["internal_whitespace"]
    assert diagnose_database_url(f"  {NEON_DIRECT}  ")["surrounding_whitespace"]
    assert not diagnose_database_url("mysql://u:p@h/db")["expected_scheme"]
    assert diagnose_database_url(None) == {
        "present": False, "surrounding_whitespace": False, "internal_whitespace": False,
        "surrounding_quotes": False, "key_prefix": False, "expected_scheme": False,
        "parser_accepts": False,
    }  # fmt: skip
    # Booleans only: the report itself can never carry the secret.
    assert all(isinstance(v, bool) for v in diagnose_database_url(NEON_DIRECT).values())


@pytest.mark.parametrize("value", VALID.values(), ids=VALID.keys())
def test_check_url_ok(monkeypatch, capsys, value):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", value)
    assert main(["db", "check-url", "--env", "MIGRATION_DATABASE_URL"]) == 0
    out = capsys.readouterr()
    assert "MIGRATION_DATABASE_URL: OK" in out.out
    assert SECRET not in out.out + out.err and "neon.tech" not in out.out + out.err


@pytest.mark.parametrize(
    ("value", "hint"),
    [
        (f"'{NEON_DIRECT}'", "remove the surrounding quotes"),
        (f"MIGRATION_DATABASE_URL={NEON_DIRECT}", "without a NAME= prefix"),
        (f"psql '{NEON_DIRECT}'", "remove spaces/newlines"),
        (NEON_DIRECT.replace("postgresql://", "mysql://", 1), "must start with postgresql://"),
        ("", "empty or not set"),
    ],
)
def test_check_url_invalid_is_explained_without_leaking(monkeypatch, capsys, value, hint):
    monkeypatch.setenv("MIGRATION_DATABASE_URL", value)
    assert main(["db", "check-url", "--env", "MIGRATION_DATABASE_URL"]) == 3
    out = capsys.readouterr()
    assert "INVALID" in out.out and hint in out.out
    combined = out.out + out.err
    assert SECRET not in combined and "neon.tech" not in combined and "neondb_owner" not in combined


def test_check_url_default_follows_db_upgrade_precedence(monkeypatch, capsys):
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", NEON_DIRECT)
    assert main(["db", "check-url"]) == 0
    assert "Checking DATABASE_URL_UNPOOLED" in capsys.readouterr().out
    monkeypatch.setenv("MIGRATION_DATABASE_URL", f"'{NEON_DIRECT}'")
    assert main(["db", "check-url"]) == 3  # MIGRATION_DATABASE_URL takes precedence
    assert "Checking MIGRATION_DATABASE_URL" in capsys.readouterr().out


def test_db_upgrade_rejects_quoted_secret_before_connecting(monkeypatch, capsys):
    calls = []
    monkeypatch.setattr("oss_health.migrate.command.upgrade", lambda cfg, rev: calls.append(rev))
    monkeypatch.setenv("MIGRATION_DATABASE_URL", f"'{NEON_DIRECT}'")
    assert main(["db", "upgrade"]) == 3
    assert calls == []  # Alembic never ran; no connection was attempted
    out = capsys.readouterr()
    assert SECRET not in out.out + out.err
