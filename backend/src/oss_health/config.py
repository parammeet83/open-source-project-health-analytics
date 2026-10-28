"""Central pipeline configuration: TOML defaults + environment secrets.

Precedence: CLI overrides (applied by the caller) > environment / .env > config/pipeline.toml.
Secrets are only ever read from the environment and are excluded from ``public_dict``.
"""

from __future__ import annotations

import os
import re
import tomllib
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

from oss_health.errors import ConfigError
from oss_health.redaction import register_secret

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG_PATH = BACKEND_DIR / "config" / "pipeline.toml"

REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")


@dataclass(frozen=True)
class GitHubSettings:
    api_base_url: str = "https://api.github.com"
    api_version: str = "2022-11-28"
    user_agent: str = "oss-health-analytics-pipeline"
    require_token: bool = False
    per_page: int = 100
    connect_timeout_seconds: float = 10.0
    read_timeout_seconds: float = 30.0
    max_retries: int = 3
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 60.0
    secondary_limit_min_wait_seconds: float = 60.0
    max_rate_limit_wait_seconds: float = 900.0
    rate_limit_buffer: int = 25
    max_pages_per_series: int = 300


@dataclass(frozen=True)
class ExtractionSettings:
    history_days: int = 90
    incremental_overlap_hours: int = 24
    backfill_open_items: bool = True


@dataclass(frozen=True)
class ValidationSettings:
    max_rejection_rate: float = 0.05
    freshness_warning_hours: int = 168
    last_success_warning_hours: int = 48


@dataclass(frozen=True)
class DatabaseSettings:
    batch_size: int = 500
    stale_run_after_hours: int = 6
    rejected_records_retention_days: int = 90
    statement_timeout_ms: int = 120_000


@dataclass(frozen=True)
class OutputSettings:
    reports_dir: str = "reports"
    logs_dir: str = "logs"
    log_level: str = "INFO"
    write_log_file: bool = True


@dataclass(frozen=True)
class Settings:
    repositories: tuple[str, ...]
    github: GitHubSettings = field(default_factory=GitHubSettings)
    extraction: ExtractionSettings = field(default_factory=ExtractionSettings)
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    database: DatabaseSettings = field(default_factory=DatabaseSettings)
    output: OutputSettings = field(default_factory=OutputSettings)
    github_token: str | None = field(default=None, repr=False)
    database_url: str | None = field(default=None, repr=False)

    def resolve_path(self, value: str) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_DIR / path

    @property
    def reports_path(self) -> Path:
        return self.resolve_path(self.output.reports_dir)

    @property
    def logs_path(self) -> Path:
        return self.resolve_path(self.output.logs_dir)

    def with_repositories(self, repositories: list[str] | tuple[str, ...]) -> Settings:
        return replace(self, repositories=_validate_repositories(list(repositories)))

    def public_dict(self) -> dict[str, Any]:
        """Non-secret settings, safe for logs, reports and ``pipeline_runs.config_snapshot``."""
        return {
            "repositories": list(self.repositories),
            "github": asdict(self.github),
            "extraction": asdict(self.extraction),
            "validation": asdict(self.validation),
            "database": asdict(self.database),
            "output": asdict(self.output),
            "github_token_configured": self.github_token is not None,
        }


def _validate_repositories(values: list[Any]) -> tuple[str, ...]:
    if not values:
        raise ConfigError("pipeline.repositories must list at least one 'owner/name'")
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not REPOSITORY_PATTERN.match(value):
            raise ConfigError(f"Invalid repository identifier {value!r}; expected 'owner/name'")
        key = value.lower()
        if key in seen:
            raise ConfigError(f"Repository {value!r} is listed more than once")
        seen.add(key)
        result.append(value)
    return tuple(result)


def _section[T](cls: type[T], data: Mapping[str, Any], name: str) -> T:
    if not isinstance(data, Mapping):
        raise ConfigError(f"[{name}] must be a table")
    defaults = cls()
    kwargs: dict[str, Any] = {}
    for key, value in data.items():
        if not hasattr(defaults, key):
            raise ConfigError(f"Unknown setting [{name}].{key}")
        expected = type(getattr(defaults, key))
        if expected is float and isinstance(value, int) and not isinstance(value, bool):
            value = float(value)
        if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
            raise ConfigError(
                f"[{name}].{key} must be {expected.__name__}, got {type(value).__name__}"
            )
        kwargs[key] = value
    return cls(**kwargs)


def _check_ranges(settings: Settings) -> None:
    gh, ex, va, db = settings.github, settings.extraction, settings.validation, settings.database
    checks = [
        (gh.api_base_url.startswith("https://"), "github.api_base_url must use https"),
        (1 <= gh.per_page <= 100, "github.per_page must be between 1 and 100"),
        (gh.connect_timeout_seconds > 0, "github.connect_timeout_seconds must be > 0"),
        (gh.read_timeout_seconds > 0, "github.read_timeout_seconds must be > 0"),
        (0 <= gh.max_retries <= 10, "github.max_retries must be between 0 and 10"),
        (gh.backoff_base_seconds > 0, "github.backoff_base_seconds must be > 0"),
        (gh.backoff_max_seconds >= gh.backoff_base_seconds, "backoff_max < backoff_base"),
        (gh.max_rate_limit_wait_seconds >= 0, "github.max_rate_limit_wait_seconds must be >= 0"),
        (gh.rate_limit_buffer >= 0, "github.rate_limit_buffer must be >= 0"),
        (1 <= gh.max_pages_per_series <= 2000, "github.max_pages_per_series must be 1..2000"),
        (1 <= ex.history_days <= 3650, "extraction.history_days must be 1..3650"),
        (0 <= ex.incremental_overlap_hours <= 720, "extraction.incremental_overlap_hours 0..720"),
        (0 <= va.max_rejection_rate <= 1, "validation.max_rejection_rate must be 0..1"),
        (va.freshness_warning_hours > 0, "validation.freshness_warning_hours must be > 0"),
        (1 <= db.batch_size <= 2000, "database.batch_size must be 1..2000"),
        (db.stale_run_after_hours >= 1, "database.stale_run_after_hours must be >= 1"),
        (
            settings.output.log_level.upper() in {"DEBUG", "INFO", "WARNING", "ERROR"},
            "output.log_level must be DEBUG, INFO, WARNING or ERROR",
        ),
    ]
    for ok, message in checks:
        if not ok:
            raise ConfigError(message)


def load_settings(
    config_path: Path | str | None = None,
    env: Mapping[str, str] | None = None,
    *,
    use_dotenv: bool = True,
) -> Settings:
    """Load and validate settings. Raises ConfigError on any problem."""
    if env is None:
        if use_dotenv:
            try:
                from dotenv import load_dotenv

                load_dotenv(BACKEND_DIR.parent / ".env", override=False)
            except ImportError:  # pragma: no cover - python-dotenv is a pipeline dependency
                pass
        env = os.environ

    path = Path(config_path or env.get("OSS_HEALTH_CONFIG") or DEFAULT_CONFIG_PATH)
    try:
        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {path}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Config file {path} is not valid TOML: {exc}") from exc

    known = {"pipeline", "github", "extraction", "validation", "database", "output"}
    unknown = set(raw) - known
    if unknown:
        raise ConfigError(f"Unknown config sections: {sorted(unknown)}")

    pipeline = raw.get("pipeline", {})
    repositories = _validate_repositories(list(pipeline.get("repositories", [])))

    token = (env.get("GITHUB_TOKEN") or "").strip() or None
    database_url = (env.get("PIPELINE_DATABASE_URL") or "").strip() or None
    register_secret(token)
    if database_url:
        from oss_health.db import password_from_url

        register_secret(password_from_url(database_url))

    settings = Settings(
        repositories=repositories,
        github=_section(GitHubSettings, raw.get("github", {}), "github"),
        extraction=_section(ExtractionSettings, raw.get("extraction", {}), "extraction"),
        validation=_section(ValidationSettings, raw.get("validation", {}), "validation"),
        database=_section(DatabaseSettings, raw.get("database", {}), "database"),
        output=_section(OutputSettings, raw.get("output", {}), "output"),
        github_token=token,
        database_url=database_url,
    )
    _check_ranges(settings)
    if settings.github.require_token and not token:
        raise ConfigError("GITHUB_TOKEN is required by configuration (github.require_token=true)")
    return settings
