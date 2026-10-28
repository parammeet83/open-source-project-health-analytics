"""Alembic configuration built in code, independent of any ``alembic.ini`` on disk.

The migration scripts ship inside the package (``oss_health/migrations``, declared as package data
in ``pyproject.toml``), so ``oss-health db upgrade`` works from a source checkout, an editable
install, and a regular ``pip install`` such as the one in GitHub Actions.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory

from oss_health.errors import ConfigError

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config(database_url: str | None = None) -> Config:
    """Return an Alembic ``Config`` whose ``script_location`` is the packaged migrations.

    The database URL travels in ``Config.attributes`` (read by ``migrations/env.py``), never in the
    config options, so it is not rendered into Alembic's logs or error messages.
    """
    if not (MIGRATIONS_DIR / "env.py").is_file() or not (MIGRATIONS_DIR / "versions").is_dir():
        raise ConfigError(
            f"Alembic migrations are missing from the installed package ({MIGRATIONS_DIR}); "
            "check [tool.setuptools.package-data] in pyproject.toml"
        )
    cfg = Config()
    # Alembic options go through configparser interpolation, so a literal '%' must be doubled.
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR).replace("%", "%%"))
    cfg.set_main_option("path_separator", "os")
    if database_url:
        cfg.attributes["database_url"] = database_url
    return cfg


def head_revisions() -> list[str]:
    """Head revision ids of the packaged migrations (no database access)."""
    return list(ScriptDirectory.from_config(alembic_config()).get_heads())


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)
