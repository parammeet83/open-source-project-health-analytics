"""Run lifecycle, concurrency lock, quality-check and rejection persistence.

Operational records are written in their own short transactions so the evidence of a failure
survives the rollback of a repository's data load.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import Connection, Engine, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from oss_health.db import PIPELINE_LOCK_KEY
from oss_health.errors import PreconditionError
from oss_health.models import data_quality_checks, pipeline_runs, rejected_records
from oss_health.redaction import redact

logger = logging.getLogger(__name__)


@dataclass
class CheckResult:
    """One data-quality check outcome (persisted to ``data_quality_checks``)."""

    check_name: str
    check_stage: str
    entity: str
    severity: str
    status: str
    repository_full_name: str | None = None
    repository_id: int | None = None
    records_evaluated: int | None = None
    records_failed: int | None = None
    observed_value: float | None = None
    threshold_value: float | None = None
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def is_error_failure(self) -> bool:
        return self.status == "failed" and self.severity == "error"

    def as_report(self) -> dict[str, Any]:
        data = asdict(self)
        data["details"] = json.loads(redact(json.dumps(self.details, default=str)))
        return data


class RunLock:
    """Session-level PostgreSQL advisory lock held on a dedicated connection.

    If the process dies the connection closes and PostgreSQL releases the lock automatically.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._conn: Connection | None = None

    def acquire(self) -> None:
        conn = self._engine.connect()
        acquired = conn.execute(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": PIPELINE_LOCK_KEY}
        ).scalar_one()
        conn.commit()
        if not acquired:
            conn.close()
            raise PreconditionError("Another pipeline run holds the lock; run already in progress")
        self._conn = conn

    def release(self) -> None:
        if self._conn is None:
            return
        try:
            self._conn.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": PIPELINE_LOCK_KEY})
            self._conn.commit()
        except Exception:  # pragma: no cover - lock is released with the connection anyway
            logger.warning("Advisory unlock failed; lock will be released when the session ends")
        finally:
            self._conn.close()
            self._conn = None


def close_stale_runs(engine: Engine, older_than_hours: int) -> int:
    """Close ``running`` rows left by crashed processes. Safe only while holding the lock."""
    with engine.begin() as conn:
        result = conn.execute(
            text(
                """
                UPDATE oss_health.pipeline_runs
                   SET status = 'failed', finished_at = now(), exit_code = 1,
                       error_type = 'AbandonedRun',
                       error_message = 'Run did not finish (process stopped); closed by a later run'
                 WHERE status = 'running'
                   AND started_at < now() - make_interval(hours => :hours)
                """
            ),
            {"hours": older_than_hours},
        )
    count = result.rowcount or 0
    if count:
        logger.warning("Closed %s abandoned run(s) as failed", count)
    return count


def purge_rejected_records(engine: Engine, retention_days: int) -> int:
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM oss_health.rejected_records "
                "WHERE rejected_at < now() - make_interval(days => :days)"
            ),
            {"days": retention_days},
        )
    return result.rowcount or 0


def start_run(
    engine: Engine,
    *,
    run_id: uuid.UUID,
    trigger: str,
    full_refresh: bool,
    pipeline_version: str,
    git_commit_sha: str | None,
    config_snapshot: dict[str, Any],
    repositories_requested: int,
    started_at: datetime,
) -> None:
    with engine.begin() as conn:
        conn.execute(
            pipeline_runs.insert().values(
                run_id=run_id,
                status="running",
                trigger_type=trigger,
                is_full_refresh=full_refresh,
                started_at=started_at,
                pipeline_version=pipeline_version,
                git_commit_sha=git_commit_sha,
                config_snapshot=config_snapshot,
                repositories_requested=repositories_requested,
                repositories_succeeded=0,
                repositories_failed=0,
                records_extracted=0,
                records_rejected=0,
                records_inserted=0,
                records_updated=0,
                api_requests_made=0,
            )
        )


def finalize_run(engine: Engine, run_id: uuid.UUID, values: dict[str, Any]) -> None:
    values = dict(values)
    if values.get("error_message"):
        values["error_message"] = redact(values["error_message"])[:2000]
    with engine.begin() as conn:
        conn.execute(
            pipeline_runs.update()
            .where(pipeline_runs.c.run_id == run_id)
            .values(finished_at=text("now()"), **values)
        )


def record_checks(engine: Engine, run_id: uuid.UUID, checks: list[CheckResult]) -> None:
    if not checks:
        return
    rows = []
    for check in checks:
        report = check.as_report()
        rows.append(
            {
                "run_id": run_id,
                "check_name": check.check_name,
                "check_stage": check.check_stage,
                "entity": check.entity,
                "repository_full_name": check.repository_full_name,
                "repository_id": check.repository_id,
                "severity": check.severity,
                "status": check.status,
                "records_evaluated": check.records_evaluated,
                "records_failed": check.records_failed,
                "observed_value": check.observed_value,
                "threshold_value": check.threshold_value,
                "details": report["details"],
            }
        )
    stmt = pg_insert(data_quality_checks).values(rows)
    ex = stmt.excluded
    stmt = stmt.on_conflict_do_update(
        constraint="uq_dq_result",
        set_={
            "check_stage": ex.check_stage,
            "repository_id": ex.repository_id,
            "severity": ex.severity,
            "status": ex.status,
            "records_evaluated": ex.records_evaluated,
            "records_failed": ex.records_failed,
            "observed_value": ex.observed_value,
            "threshold_value": ex.threshold_value,
            "details": ex.details,
            "checked_at": text("now()"),
        },
    )
    with engine.begin() as conn:
        conn.execute(stmt)


def record_rejections(
    engine: Engine, run_id: uuid.UUID, rejections: list[dict[str, Any]], batch_size: int = 500
) -> None:
    if not rejections:
        return
    rows = []
    for rejection in rejections:
        payload = json.loads(redact(json.dumps(rejection.get("record_payload"), default=str)))
        rows.append(
            {
                "run_id": run_id,
                "entity": rejection["entity"],
                "repository_full_name": rejection["repository_full_name"],
                "source_id": rejection.get("source_id"),
                "source_number": rejection.get("source_number"),
                "check_name": rejection["check_name"],
                "failure_reason": redact(rejection["failure_reason"]),
                "failed_column": rejection.get("failed_column"),
                "failed_value": redact(rejection["failed_value"])
                if rejection.get("failed_value") is not None
                else None,
                "record_payload": payload,
            }
        )
    with engine.begin() as conn:
        for start in range(0, len(rows), batch_size):
            conn.execute(rejected_records.insert(), rows[start : start + batch_size])


def last_successful_run_started_at(engine: Engine, exclude_run_id: uuid.UUID) -> datetime | None:
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT max(started_at) FROM oss_health.pipeline_runs "
                "WHERE status = 'success' AND run_id <> :run_id"
            ),
            {"run_id": exclude_run_id},
        ).scalar_one()
