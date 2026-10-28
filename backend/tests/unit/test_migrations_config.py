"""Regression tests for the Alembic configuration used by `oss-health db upgrade`.

GitHub Actions installs the backend with a regular (non-editable) `pip install`, so nothing may
depend on `backend/alembic.ini` or on files outside the package. These tests need no database.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

import pytest
from alembic.script import ScriptDirectory

import oss_health
from oss_health import migrate
from oss_health.cli import main
from oss_health.config import discover_backend_dir

PACKAGE_DIR = Path(oss_health.__file__).resolve().parent
PYPROJECT = PACKAGE_DIR.parents[1] / "pyproject.toml"
EXPECTED_REVISIONS = ["0001_schemas_and_ops", "0002_source_tables", "0003_analytics_views"]


def test_config_has_script_location_pointing_at_packaged_migrations():
    cfg = migrate.alembic_config("postgresql://owner:pw@localhost/osp_health_test")
    location = cfg.get_main_option("script_location")
    assert location, "Alembic config must define script_location"
    path = Path(location)
    assert path.is_absolute() and path == PACKAGE_DIR / "migrations"
    assert (path / "env.py").is_file() and (path / "versions").is_dir()


def test_url_is_passed_as_attribute_not_as_an_option():
    url = "postgresql://owner:super-secret-pw@localhost/osp_health_test"
    cfg = migrate.alembic_config(url)
    assert cfg.attributes["database_url"] == url
    assert cfg.get_main_option("sqlalchemy.url") is None
    rendered = "".join(
        f"{k}={cfg.get_main_option(k)}" for k in ("script_location", "path_separator")
    )
    assert "super-secret-pw" not in rendered


def test_script_directory_loads_the_full_revision_chain():
    script = ScriptDirectory.from_config(migrate.alembic_config())
    assert migrate.head_revisions() == ["0003_analytics_views"]
    chain = [rev.revision for rev in script.walk_revisions("base", "heads")]
    assert list(reversed(chain)) == EXPECTED_REVISIONS


def test_config_does_not_depend_on_alembic_ini_or_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # far away from backend/alembic.ini
    cfg = migrate.alembic_config()
    assert ScriptDirectory.from_config(cfg).get_current_head() == "0003_analytics_views"


def test_missing_packaged_migrations_fail_with_clear_error(monkeypatch, tmp_path):
    from oss_health.errors import ConfigError

    monkeypatch.setattr(migrate, "MIGRATIONS_DIR", tmp_path / "missing")
    with pytest.raises(ConfigError, match="package-data"):
        migrate.alembic_config()


def test_package_data_ships_every_migration_and_sql_file():
    """Every file under migrations/ and sql/ must match a package-data glob in pyproject.toml."""
    globs = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["tool"]["setuptools"][
        "package-data"
    ]["oss_health"]
    shipped = [
        p.relative_to(PACKAGE_DIR).as_posix()
        for folder in ("migrations", "sql")
        for p in (PACKAGE_DIR / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and p.suffix in {".py", ".sql", ".mako"}
        and not (folder == "sql" and p.name == "__init__.py")
    ]
    assert shipped, "no migration files found"
    missing = [f for f in shipped if not any(fnmatch.fnmatch(f, g) for g in globs)]
    assert missing == []


def test_cli_upgrade_passes_a_complete_config_to_alembic(monkeypatch, capsys):
    """`oss-health db upgrade` must hand Alembic a config with script_location (CI regression)."""
    seen = {}

    def fake_upgrade(cfg, revision):
        seen["script_location"] = cfg.get_main_option("script_location")
        seen["revision"] = revision
        seen["url"] = cfg.attributes.get("database_url")
        ScriptDirectory.from_config(cfg)  # raises CommandError if script_location is missing

    url = "postgresql://owner:pw-not-printed@localhost/osp_health_test"
    monkeypatch.setenv("MIGRATION_DATABASE_URL", url)
    monkeypatch.setattr("oss_health.migrate.command.upgrade", fake_upgrade)
    assert main(["db", "upgrade"]) == 0
    assert seen == {
        "script_location": str(PACKAGE_DIR / "migrations"),
        "revision": "head",
        "url": url,
    }
    captured = capsys.readouterr()
    assert "pw-not-printed" not in captured.out + captured.err


def test_cli_upgrade_falls_back_to_neon_unpooled_url(monkeypatch):
    seen = {}
    monkeypatch.setenv("DATABASE_URL_UNPOOLED", "postgresql://neondb_owner:x@ep-1.neon.tech/neondb")
    monkeypatch.setattr(
        "oss_health.migrate.command.upgrade",
        lambda cfg, rev: seen.update(url=cfg.attributes["database_url"]),
    )
    assert main(["db", "upgrade"]) == 0
    assert seen["url"].startswith("postgresql://neondb_owner:")


def test_backend_dir_discovery_for_installed_package(tmp_path):
    # Simulate site-packages/oss_health/config.py with the project checked out elsewhere.
    installed = tmp_path / "venv" / "Lib" / "site-packages" / "oss_health" / "config.py"
    installed.parent.mkdir(parents=True)
    installed.touch()
    project = tmp_path / "checkout" / "backend"
    (project / "config").mkdir(parents=True)
    (project / "config" / "pipeline.toml").write_text("", encoding="utf-8")

    assert discover_backend_dir(installed, cwd=project) == project  # CI: working-directory backend
    assert discover_backend_dir(installed, cwd=project.parent) == project  # repository root
    assert discover_backend_dir(installed, cwd=project / "config") == project  # nested directory


def test_backend_dir_discovery_prefers_source_tree():
    source = PACKAGE_DIR / "config.py"
    assert discover_backend_dir(source, cwd=Path("/")) == PACKAGE_DIR.parents[1]
