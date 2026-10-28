"""Schemas and operational tables: pipeline_runs, data_quality_checks, rejected_records.

Revision ID: 0001_schemas_and_ops
Revises:
Create Date: 2026-10-28
"""

from alembic import op

revision = "0001_schemas_and_ops"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS oss_health")
    op.execute("CREATE SCHEMA IF NOT EXISTS analytics")

    op.execute(
        """
        CREATE TABLE oss_health.pipeline_runs (
            run_id                  UUID PRIMARY KEY,
            status                  TEXT        NOT NULL DEFAULT 'running',
            trigger_type            TEXT        NOT NULL,
            is_full_refresh         BOOLEAN     NOT NULL DEFAULT false,
            started_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
            finished_at             TIMESTAMPTZ,
            exit_code               SMALLINT,
            pipeline_version        TEXT        NOT NULL,
            git_commit_sha          TEXT,
            config_snapshot         JSONB       NOT NULL,
            repositories_requested  INTEGER     NOT NULL DEFAULT 0,
            repositories_succeeded  INTEGER     NOT NULL DEFAULT 0,
            repositories_failed     INTEGER     NOT NULL DEFAULT 0,
            records_extracted       INTEGER     NOT NULL DEFAULT 0,
            records_rejected        INTEGER     NOT NULL DEFAULT 0,
            records_inserted        INTEGER     NOT NULL DEFAULT 0,
            records_updated         INTEGER     NOT NULL DEFAULT 0,
            api_requests_made       INTEGER     NOT NULL DEFAULT 0,
            rate_limit_remaining    INTEGER,
            error_type              TEXT,
            error_message           TEXT,
            CONSTRAINT ck_runs_status   CHECK (status IN ('running','success','partial_success','failed')),
            CONSTRAINT ck_runs_trigger  CHECK (trigger_type IN ('manual','scheduled','ci')),
            CONSTRAINT ck_runs_finished CHECK ((status = 'running') = (finished_at IS NULL)),
            CONSTRAINT ck_runs_order    CHECK (finished_at IS NULL OR finished_at >= started_at),
            CONSTRAINT ck_runs_exit     CHECK (exit_code IS NULL OR exit_code IN (0,1,2)),
            CONSTRAINT ck_runs_counts   CHECK (
                repositories_succeeded >= 0 AND repositories_failed >= 0
                AND repositories_succeeded + repositories_failed <= repositories_requested
                AND records_extracted >= 0 AND records_rejected >= 0
                AND records_inserted >= 0 AND records_updated >= 0 AND api_requests_made >= 0)
        )
        """
    )
    op.execute("CREATE INDEX ix_runs_started_at ON oss_health.pipeline_runs (started_at DESC)")
    op.execute(
        "CREATE INDEX ix_runs_running ON oss_health.pipeline_runs (started_at) "
        "WHERE status = 'running'"
    )

    op.execute(
        """
        CREATE TABLE oss_health.data_quality_checks (
            check_result_id       BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id                UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id) ON DELETE CASCADE,
            check_name            TEXT        NOT NULL,
            check_stage           TEXT        NOT NULL,
            entity                TEXT        NOT NULL,
            repository_full_name  TEXT,
            repository_id         BIGINT,
            severity              TEXT        NOT NULL,
            status                TEXT        NOT NULL,
            records_evaluated     INTEGER,
            records_failed        INTEGER,
            observed_value        NUMERIC,
            threshold_value       NUMERIC,
            details               JSONB       NOT NULL DEFAULT '{}'::jsonb,
            checked_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_dq_stage    CHECK (check_stage IN ('extract','transform','validate','load','post_load')),
            CONSTRAINT ck_dq_entity   CHECK (entity IN ('repository','repository_snapshot','issue','pull_request','run')),
            CONSTRAINT ck_dq_severity CHECK (severity IN ('error','warning','info')),
            CONSTRAINT ck_dq_status   CHECK (status IN ('passed','failed','skipped')),
            CONSTRAINT ck_dq_counts   CHECK ((records_evaluated IS NULL OR records_evaluated >= 0)
                                         AND (records_failed IS NULL OR records_failed >= 0)),
            CONSTRAINT uq_dq_result UNIQUE NULLS NOT DISTINCT (run_id, check_name, entity, repository_full_name)
        )
        """
    )
    op.execute("CREATE INDEX ix_dq_run ON oss_health.data_quality_checks (run_id)")
    op.execute(
        "CREATE INDEX ix_dq_name_status ON oss_health.data_quality_checks (check_name, status)"
    )

    op.execute(
        """
        CREATE TABLE oss_health.rejected_records (
            rejected_record_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
            run_id                UUID        NOT NULL REFERENCES oss_health.pipeline_runs(run_id) ON DELETE CASCADE,
            entity                TEXT        NOT NULL,
            repository_full_name  TEXT        NOT NULL,
            source_id             BIGINT,
            source_number         INTEGER,
            check_name            TEXT        NOT NULL,
            failure_reason        TEXT        NOT NULL,
            failed_column         TEXT,
            failed_value          TEXT,
            record_payload        JSONB,
            rejected_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            CONSTRAINT ck_rejected_entity CHECK (entity IN ('repository','repository_snapshot','issue','pull_request'))
        )
        """
    )
    op.execute("CREATE INDEX ix_rejected_run ON oss_health.rejected_records (run_id)")
    op.execute(
        "CREATE INDEX ix_rejected_entity_check ON oss_health.rejected_records (entity, check_name)"
    )
    op.execute("CREATE INDEX ix_rejected_at ON oss_health.rejected_records (rejected_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS oss_health.rejected_records")
    op.execute("DROP TABLE IF EXISTS oss_health.data_quality_checks")
    op.execute("DROP TABLE IF EXISTS oss_health.pipeline_runs")
    op.execute("DROP SCHEMA IF EXISTS analytics")
    op.execute("DROP SCHEMA IF EXISTS oss_health")
