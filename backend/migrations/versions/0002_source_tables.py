"""Source tables: repositories, repository_snapshots, github_issues, pull_requests, watermarks.

Revision ID: 0002_source_tables
Revises: 0001_schemas_and_ops
Create Date: 2026-10-28
"""

from alembic import op

revision = "0002_source_tables"
down_revision = "0001_schemas_and_ops"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE oss_health.repositories (
            repository_id     BIGINT PRIMARY KEY,
            node_id           TEXT        NOT NULL UNIQUE,
            full_name         TEXT        NOT NULL,
            owner_login       TEXT        NOT NULL,
            repo_name         TEXT        NOT NULL,
            description       TEXT,
            html_url          TEXT        NOT NULL,
            default_branch    TEXT,
            primary_language  TEXT,
            license_spdx_id   TEXT,
            topics            TEXT[]      NOT NULL DEFAULT '{}',
            is_fork           BOOLEAN     NOT NULL,
            is_archived       BOOLEAN     NOT NULL,
            created_at        TIMESTAMPTZ NOT NULL,
            updated_at        TIMESTAMPTZ NOT NULL,
            pushed_at         TIMESTAMPTZ,
            is_tracked        BOOLEAN     NOT NULL DEFAULT true,
            etl_run_id        UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id),
            etl_inserted_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
            etl_updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_repositories_repository_id CHECK (repository_id > 0)
        )
        """
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_repositories_full_name_ci ON oss_health.repositories (lower(full_name))"
    )

    op.execute(
        """
        CREATE TABLE oss_health.repository_snapshots (
            repository_id              BIGINT      NOT NULL REFERENCES oss_health.repositories(repository_id) ON DELETE RESTRICT,
            snapshot_date              DATE        NOT NULL,
            captured_at                TIMESTAMPTZ NOT NULL,
            stargazers_count           INTEGER     NOT NULL CHECK (stargazers_count >= 0),
            forks_count                INTEGER     NOT NULL CHECK (forks_count >= 0),
            subscribers_count          INTEGER              CHECK (subscribers_count >= 0),
            open_issues_and_prs_count  INTEGER     NOT NULL CHECK (open_issues_and_prs_count >= 0),
            size_kb                    INTEGER              CHECK (size_kb >= 0),
            etl_run_id                 UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id),
            PRIMARY KEY (repository_id, snapshot_date),
            CONSTRAINT ck_snapshot_date_matches CHECK (snapshot_date = (captured_at AT TIME ZONE 'UTC')::date)
        )
        """
    )
    op.execute("CREATE INDEX ix_snapshots_date ON oss_health.repository_snapshots (snapshot_date)")

    op.execute(
        """
        CREATE TABLE oss_health.github_issues (
            issue_id            BIGINT PRIMARY KEY,
            node_id             TEXT        NOT NULL UNIQUE,
            repository_id       BIGINT      NOT NULL REFERENCES oss_health.repositories(repository_id) ON DELETE RESTRICT,
            issue_number        INTEGER     NOT NULL CHECK (issue_number > 0),
            title               TEXT        NOT NULL,
            state               TEXT        NOT NULL CHECK (state IN ('open','closed')),
            state_reason        TEXT,
            author_login        TEXT,
            author_type         TEXT,
            author_association  TEXT,
            is_bot_author       BOOLEAN     NOT NULL DEFAULT false,
            label_names         TEXT[]      NOT NULL DEFAULT '{}',
            assignee_count      SMALLINT    NOT NULL DEFAULT 0 CHECK (assignee_count >= 0),
            comments_count      INTEGER     NOT NULL DEFAULT 0 CHECK (comments_count >= 0),
            is_locked           BOOLEAN     NOT NULL DEFAULT false,
            milestone_title     TEXT,
            html_url            TEXT        NOT NULL,
            created_at          TIMESTAMPTZ NOT NULL,
            updated_at          TIMESTAMPTZ NOT NULL,
            closed_at           TIMESTAMPTZ,
            etl_run_id          UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id),
            etl_inserted_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
            etl_updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_issues_repo_number   UNIQUE (repository_id, issue_number),
            CONSTRAINT ck_issues_id            CHECK (issue_id > 0),
            CONSTRAINT ck_issues_updated_order CHECK (updated_at >= created_at),
            CONSTRAINT ck_issues_closed_order  CHECK (closed_at IS NULL OR closed_at >= created_at)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_issues_repo_state ON oss_health.github_issues (repository_id, state)"
    )
    op.execute(
        "CREATE INDEX ix_issues_repo_created ON oss_health.github_issues (repository_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_issues_repo_closed ON oss_health.github_issues (repository_id, closed_at) "
        "WHERE closed_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_issues_open_created ON oss_health.github_issues (repository_id, created_at) "
        "WHERE state = 'open'"
    )

    op.execute(
        """
        CREATE TABLE oss_health.pull_requests (
            pull_request_id           BIGINT PRIMARY KEY,
            node_id                   TEXT        NOT NULL UNIQUE,
            repository_id             BIGINT      NOT NULL REFERENCES oss_health.repositories(repository_id) ON DELETE RESTRICT,
            pr_number                 INTEGER     NOT NULL CHECK (pr_number > 0),
            title                     TEXT        NOT NULL,
            state                     TEXT        NOT NULL CHECK (state IN ('open','closed')),
            is_draft                  BOOLEAN     NOT NULL DEFAULT false,
            author_login              TEXT,
            author_type               TEXT,
            author_association        TEXT,
            is_bot_author             BOOLEAN     NOT NULL DEFAULT false,
            label_names               TEXT[]      NOT NULL DEFAULT '{}',
            requested_reviewer_count  SMALLINT    NOT NULL DEFAULT 0 CHECK (requested_reviewer_count >= 0),
            base_ref                  TEXT,
            head_ref                  TEXT,
            head_repo_full_name       TEXT,
            html_url                  TEXT        NOT NULL,
            created_at                TIMESTAMPTZ NOT NULL,
            updated_at                TIMESTAMPTZ NOT NULL,
            closed_at                 TIMESTAMPTZ,
            merged_at                 TIMESTAMPTZ,
            merge_commit_sha          TEXT,
            is_merged                 BOOLEAN GENERATED ALWAYS AS (merged_at IS NOT NULL) STORED,
            etl_run_id                UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id),
            etl_inserted_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            etl_updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT uq_prs_repo_number   UNIQUE (repository_id, pr_number),
            CONSTRAINT ck_prs_id            CHECK (pull_request_id > 0),
            CONSTRAINT ck_prs_updated_order CHECK (updated_at >= created_at),
            CONSTRAINT ck_prs_closed_order  CHECK (closed_at IS NULL OR closed_at >= created_at),
            CONSTRAINT ck_prs_merged_valid  CHECK (merged_at IS NULL OR (state = 'closed' AND merged_at >= created_at))
        )
        """
    )
    op.execute("CREATE INDEX ix_prs_repo_state ON oss_health.pull_requests (repository_id, state)")
    op.execute(
        "CREATE INDEX ix_prs_repo_created ON oss_health.pull_requests (repository_id, created_at)"
    )
    op.execute(
        "CREATE INDEX ix_prs_repo_merged ON oss_health.pull_requests (repository_id, merged_at) "
        "WHERE merged_at IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX ix_prs_repo_closed ON oss_health.pull_requests (repository_id, closed_at) "
        "WHERE closed_at IS NOT NULL"
    )

    op.execute(
        """
        CREATE TABLE oss_health.extraction_watermarks (
            repository_id           BIGINT      NOT NULL REFERENCES oss_health.repositories(repository_id) ON DELETE RESTRICT,
            resource                TEXT        NOT NULL CHECK (resource IN ('issues','pull_requests')),
            high_watermark          TIMESTAMPTZ NOT NULL,
            coverage_start_at       TIMESTAMPTZ NOT NULL,
            last_successful_run_id  UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id),
            updated_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            PRIMARY KEY (repository_id, resource),
            CONSTRAINT ck_watermark_coverage CHECK (coverage_start_at <= high_watermark)
        )
        """
    )

    op.execute(
        """
        ALTER TABLE oss_health.data_quality_checks
            ADD CONSTRAINT fk_dq_repository FOREIGN KEY (repository_id)
            REFERENCES oss_health.repositories(repository_id) ON DELETE RESTRICT
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE oss_health.data_quality_checks DROP CONSTRAINT IF EXISTS fk_dq_repository"
    )
    for table in (
        "extraction_watermarks",
        "pull_requests",
        "github_issues",
        "repository_snapshots",
        "repositories",
    ):
        op.execute(f"DROP TABLE IF EXISTS oss_health.{table}")
