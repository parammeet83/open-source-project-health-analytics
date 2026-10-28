"""SQLAlchemy Core table definitions used by the loaders.

The authoritative DDL (constraints, partial indexes, generated columns) lives in the Alembic
migrations under ``oss_health/migrations``; integration tests verify the two stay in sync.
"""

from __future__ import annotations

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    MetaData,
    Numeric,
    SmallInteger,
    Table,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

CORE_SCHEMA = "oss_health"
ANALYTICS_SCHEMA = "analytics"

metadata = MetaData(schema=CORE_SCHEMA)


def _tz(name: str, nullable: bool = True) -> Column:
    return Column(name, DateTime(timezone=True), nullable=nullable)


pipeline_runs = Table(
    "pipeline_runs",
    metadata,
    Column("run_id", UUID(as_uuid=True), primary_key=True),
    Column("status", Text, nullable=False),
    Column("trigger_type", Text, nullable=False),
    Column("is_full_refresh", Boolean, nullable=False),
    _tz("started_at", False),
    _tz("finished_at"),
    Column("exit_code", SmallInteger),
    Column("pipeline_version", Text, nullable=False),
    Column("git_commit_sha", Text),
    Column("config_snapshot", JSONB, nullable=False),
    Column("repositories_requested", Integer, nullable=False),
    Column("repositories_succeeded", Integer, nullable=False),
    Column("repositories_failed", Integer, nullable=False),
    Column("records_extracted", Integer, nullable=False),
    Column("records_rejected", Integer, nullable=False),
    Column("records_inserted", Integer, nullable=False),
    Column("records_updated", Integer, nullable=False),
    Column("api_requests_made", Integer, nullable=False),
    Column("rate_limit_remaining", Integer),
    Column("error_type", Text),
    Column("error_message", Text),
)

repositories = Table(
    "repositories",
    metadata,
    Column("repository_id", BigInteger, primary_key=True),
    Column("node_id", Text, nullable=False),
    Column("full_name", Text, nullable=False),
    Column("owner_login", Text, nullable=False),
    Column("repo_name", Text, nullable=False),
    Column("description", Text),
    Column("html_url", Text, nullable=False),
    Column("default_branch", Text),
    Column("primary_language", Text),
    Column("license_spdx_id", Text),
    Column("topics", ARRAY(Text), nullable=False),
    Column("is_fork", Boolean, nullable=False),
    Column("is_archived", Boolean, nullable=False),
    _tz("created_at", False),
    _tz("updated_at", False),
    _tz("pushed_at"),
    Column("is_tracked", Boolean, nullable=False),
    Column("etl_run_id", UUID(as_uuid=True), nullable=False),
    _tz("etl_inserted_at", False),
    _tz("etl_updated_at", False),
)

repository_snapshots = Table(
    "repository_snapshots",
    metadata,
    Column("repository_id", BigInteger, primary_key=True),
    Column("snapshot_date", Date, primary_key=True),
    _tz("captured_at", False),
    Column("stargazers_count", Integer, nullable=False),
    Column("forks_count", Integer, nullable=False),
    Column("subscribers_count", Integer),
    Column("open_issues_and_prs_count", Integer, nullable=False),
    Column("size_kb", Integer),
    Column("etl_run_id", UUID(as_uuid=True), nullable=False),
)

github_issues = Table(
    "github_issues",
    metadata,
    Column("issue_id", BigInteger, primary_key=True),
    Column("node_id", Text, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("issue_number", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("state_reason", Text),
    Column("author_login", Text),
    Column("author_type", Text),
    Column("author_association", Text),
    Column("is_bot_author", Boolean, nullable=False),
    Column("label_names", ARRAY(Text), nullable=False),
    Column("assignee_count", SmallInteger, nullable=False),
    Column("comments_count", Integer, nullable=False),
    Column("is_locked", Boolean, nullable=False),
    Column("milestone_title", Text),
    Column("html_url", Text, nullable=False),
    _tz("created_at", False),
    _tz("updated_at", False),
    _tz("closed_at"),
    Column("etl_run_id", UUID(as_uuid=True), nullable=False),
    _tz("etl_inserted_at", False),
    _tz("etl_updated_at", False),
)

pull_requests = Table(
    "pull_requests",
    metadata,
    Column("pull_request_id", BigInteger, primary_key=True),
    Column("node_id", Text, nullable=False),
    Column("repository_id", BigInteger, nullable=False),
    Column("pr_number", Integer, nullable=False),
    Column("title", Text, nullable=False),
    Column("state", Text, nullable=False),
    Column("is_draft", Boolean, nullable=False),
    Column("author_login", Text),
    Column("author_type", Text),
    Column("author_association", Text),
    Column("is_bot_author", Boolean, nullable=False),
    Column("label_names", ARRAY(Text), nullable=False),
    Column("requested_reviewer_count", SmallInteger, nullable=False),
    Column("base_ref", Text),
    Column("head_ref", Text),
    Column("head_repo_full_name", Text),
    Column("html_url", Text, nullable=False),
    _tz("created_at", False),
    _tz("updated_at", False),
    _tz("closed_at"),
    _tz("merged_at"),
    Column("merge_commit_sha", Text),
    Column("etl_run_id", UUID(as_uuid=True), nullable=False),
    _tz("etl_inserted_at", False),
    _tz("etl_updated_at", False),
)

data_quality_checks = Table(
    "data_quality_checks",
    metadata,
    Column("check_result_id", BigInteger, primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("check_name", Text, nullable=False),
    Column("check_stage", Text, nullable=False),
    Column("entity", Text, nullable=False),
    Column("repository_full_name", Text),
    Column("repository_id", BigInteger),
    Column("severity", Text, nullable=False),
    Column("status", Text, nullable=False),
    Column("records_evaluated", Integer),
    Column("records_failed", Integer),
    Column("observed_value", Numeric),
    Column("threshold_value", Numeric),
    Column("details", JSONB, nullable=False),
    _tz("checked_at", False),
)

rejected_records = Table(
    "rejected_records",
    metadata,
    Column("rejected_record_id", BigInteger, primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("entity", Text, nullable=False),
    Column("repository_full_name", Text, nullable=False),
    Column("source_id", BigInteger),
    Column("source_number", Integer),
    Column("check_name", Text, nullable=False),
    Column("failure_reason", Text, nullable=False),
    Column("failed_column", Text),
    Column("failed_value", Text),
    Column("record_payload", JSONB),
    _tz("rejected_at", False),
)

extraction_watermarks = Table(
    "extraction_watermarks",
    metadata,
    Column("repository_id", BigInteger, primary_key=True),
    Column("resource", Text, primary_key=True),
    _tz("high_watermark", False),
    _tz("coverage_start_at", False),
    Column("last_successful_run_id", UUID(as_uuid=True), nullable=False),
    _tz("updated_at", False),
)
