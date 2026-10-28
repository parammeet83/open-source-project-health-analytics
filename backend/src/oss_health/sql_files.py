"""Access to versioned SQL files shipped inside the package."""

from __future__ import annotations

from importlib import resources

STATEMENT_SEPARATOR = "-- @@"


def read_sql(name: str) -> str:
    return resources.files("oss_health.sql").joinpath(name).read_text(encoding="utf-8")


def sql_statements(name: str) -> list[str]:
    """Split a SQL file on the ``-- @@`` marker into individual statements."""
    parts = read_sql(name).split(STATEMENT_SEPARATOR)
    return [p.strip() for p in parts if p.strip() and not _only_comments(p)]


def _only_comments(text: str) -> bool:
    return all(not line.strip() or line.strip().startswith("--") for line in text.splitlines())


ANALYTICS_VIEWS = [
    "dim_repository",
    "dim_date",
    "fact_repository_snapshot",
    "fact_issue",
    "fact_pull_request",
    "agg_weekly_activity",
    "fact_pipeline_run",
    "fact_data_quality_check",
]
