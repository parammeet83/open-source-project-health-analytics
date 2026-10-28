"""The single orchestration function, ``run_pipeline()`` (docs/Data_Flow.md).

Lifecycle: claim the run (advisory lock + ``running`` row) → per repository: extract, transform,
validate, quarantine rejections, load in one transaction, reconcile, advance watermarks →
post-load checks → finalize status → JSON report. Exit codes: 0 success, 2 partial_success,
1 failed, 3 not started.
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError

from oss_health import __version__
from oss_health.config import Settings
from oss_health.db import create_db_engine
from oss_health.errors import (
    ConfigError,
    GitHubAuthError,
    GitHubResponseError,
    LoadError,
    OssHealthError,
    PreconditionError,
    RateLimitExceededError,
)
from oss_health.github.client import GitHubClient
from oss_health.github.extract import (
    ISSUES,
    PULL_REQUESTS,
    ResourceExtract,
    extract_issues,
    extract_pull_requests,
    extract_repository_metadata,
    iso_z,
    plan_window,
)
from oss_health.load import (
    UpsertStats,
    advance_watermark,
    count_present,
    get_watermarks,
    set_tracked_repositories,
    upsert_issues,
    upsert_pull_requests,
    upsert_repository,
    upsert_snapshot,
)
from oss_health.logging_setup import repository_var, run_id_var
from oss_health.models import github_issues, pull_requests, repository_snapshots
from oss_health.redaction import safe_error_message
from oss_health.report import write_report
from oss_health.tracking import (
    CheckResult,
    RunLock,
    close_stale_runs,
    finalize_run,
    last_successful_run_started_at,
    purge_rejected_records,
    record_checks,
    record_rejections,
    start_run,
)
from oss_health.transform import (
    transform_issues,
    transform_pull_requests,
    transform_repository,
)
from oss_health.validation import (
    ValidationOutcome,
    duplicate_keys,
    issue_schema,
    pull_request_schema,
    repository_schema,
    snapshot_schema,
    unknown_state_reasons,
    validate_frame,
)

logger = logging.getLogger(__name__)

EXIT_CODES = {"success": 0, "failed": 1, "partial_success": 2, "not_started": 3}
REJECTION_SAMPLE_LIMIT = 25


@dataclass
class RepoOutcome:
    full_name: str
    status: str = "pending"
    repository_id: int | None = None
    modes: dict[str, str] = field(default_factory=dict)
    entities: dict[str, dict[str, int]] = field(default_factory=dict)
    watermarks: dict[str, str] = field(default_factory=dict)
    error_type: str | None = None
    error_message: str | None = None

    def fail(self, exc: BaseException | str, error_type: str | None = None) -> None:
        self.status = "failed"
        self.error_type = error_type or (
            type(exc).__name__ if isinstance(exc, BaseException) else "Error"
        )
        self.error_message = safe_error_message(exc) if isinstance(exc, BaseException) else str(exc)

    def as_report(self) -> dict[str, Any]:
        data: dict[str, Any] = {"full_name": self.full_name, "status": self.status}
        if self.repository_id is not None:
            data["repository_id"] = self.repository_id
        if self.modes:
            data["modes"] = self.modes
        if self.entities:
            data["entities"] = self.entities
        if self.watermarks:
            data["watermarks"] = self.watermarks
        if self.error_type:
            data["error_type"] = self.error_type
            data["error_message"] = self.error_message
        return data


@dataclass
class RunResult:
    run_id: uuid.UUID | None
    status: str
    exit_code: int
    started_at: datetime
    finished_at: datetime
    trigger: str
    dry_run: bool
    full_refresh: bool
    repositories: list[RepoOutcome]
    checks: list[CheckResult]
    rejections_sample: list[dict[str, Any]]
    errors: list[str]
    totals: dict[str, int]
    api: dict[str, Any]
    config: dict[str, Any]
    report_path: Path | None = None

    def as_report(self) -> dict[str, Any]:
        failed_checks = [c.as_report() for c in self.checks if c.status == "failed"]
        return {
            "run_id": str(self.run_id) if self.run_id else None,
            "status": self.status,
            "exit_code": self.exit_code,
            "dry_run": self.dry_run,
            "full_refresh": self.full_refresh,
            "trigger": self.trigger,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat(),
            "duration_seconds": round((self.finished_at - self.started_at).total_seconds(), 1),
            "pipeline_version": __version__,
            "config": self.config,
            "api": self.api,
            "totals": self.totals,
            "repositories": [r.as_report() for r in self.repositories],
            "checks_summary": {
                "evaluated": sum(1 for c in self.checks if c.status != "skipped"),
                "passed": sum(1 for c in self.checks if c.status == "passed"),
                "failed_error": sum(1 for c in self.checks if c.is_error_failure),
                "failed_warning": sum(
                    1 for c in self.checks if c.status == "failed" and c.severity == "warning"
                ),
            },
            "failed_checks": failed_checks,
            "completeness_warnings": [
                c.as_report()
                for c in self.checks
                if c.check_name in ("extraction_completeness", "watermark_advancement")
                and c.status == "failed"
            ],
            "checks": [c.as_report() for c in self.checks],
            "rejections_sample": self.rejections_sample,
            "errors": self.errors,
        }


def compute_status(outcomes: list[RepoOutcome], checks: list[CheckResult], fatal: bool) -> str:
    succeeded = sum(1 for o in outcomes if o.status == "succeeded")
    failed = sum(1 for o in outcomes if o.status != "succeeded")
    if fatal or succeeded == 0:
        return "failed"
    if failed or any(c.is_error_failure for c in checks):
        return "partial_success"
    return "success"


def decide_watermark(
    extract: ResourceExtract, *, rejection_ok: bool
) -> tuple[datetime | None, str]:
    """Return the new high watermark (or None to hold it) and the reason."""
    if not rejection_ok:
        return None, "held: rejection rate above threshold"
    if extract.window.include_open and not extract.open_complete:
        return None, "held: open-item series incomplete"
    if extract.resource == PULL_REQUESTS and not extract.window_complete:
        # Descending series: a capped series may be missing older items.
        return None, "held: pull-request series incomplete"
    newest = extract.window_max_updated_at
    if newest is None:
        if not extract.window_complete:
            return None, "held: series incomplete"
        return extract.started_at, "advanced (no items in window)"
    if not extract.window_complete:
        # Ascending issue series: safe forward progress up to the newest loaded item.
        return newest, "advanced (partial, ascending series)"
    return newest, "advanced"


class _Context:
    def __init__(
        self,
        settings: Settings,
        client: GitHubClient,
        engine: Engine | None,
        run_id: uuid.UUID,
        full_refresh: bool,
        now: Callable[[], datetime],
    ) -> None:
        self.settings = settings
        self.client = client
        self.engine = engine
        self.run_id = run_id
        self.full_refresh = full_refresh
        self.now = now
        self.checks: list[CheckResult] = []
        self.rejections_sample: list[dict[str, Any]] = []
        self.totals = {"extracted": 0, "rejected": 0, "inserted": 0, "updated": 0, "unchanged": 0}


def _validation_checks(
    v: ValidationOutcome, repo: str, threshold: float, stage_entity: str
) -> list[CheckResult]:
    checks = [
        CheckResult(
            "required_fields_and_rules",
            "validate",
            stage_entity,
            "warning",
            "passed" if v.rejected_rows == 0 else "failed",
            repository_full_name=repo,
            records_evaluated=v.evaluated,
            records_failed=v.rejected_rows,
            details={"failures_by_check": v.failures_by_check},
        ),
        CheckResult(
            "rejection_rate",
            "validate",
            stage_entity,
            "error",
            "passed" if v.rejection_rate <= threshold else "failed",
            repository_full_name=repo,
            records_evaluated=v.evaluated,
            records_failed=v.rejected_rows,
            observed_value=round(v.rejection_rate, 6),
            threshold_value=threshold,
        ),
    ]
    return checks


def _completeness_check(extract: ResourceExtract, repo: str, entity: str) -> CheckResult:
    return CheckResult(
        "extraction_completeness",
        "extract",
        entity,
        "error",
        "passed" if extract.complete else "failed",
        repository_full_name=repo,
        records_evaluated=len(extract.items),
        observed_value=extract.pages,
        details={
            "mode": extract.window.mode,
            "since": iso_z(extract.window.since),
            "window_complete": extract.window_complete,
            "open_items_fetched": extract.window.include_open,
            "open_items_complete": extract.open_complete,
            "pages": extract.pages,
        },
    )


def _process_repository(ctx: _Context, full_name: str) -> RepoOutcome:
    outcome = RepoOutcome(full_name)
    repo_checks: list[CheckResult] = []
    loaded = False
    settings, threshold = ctx.settings, ctx.settings.validation.max_rejection_rate
    try:
        # ---------------------------------------------------------------- extract
        repo_json = extract_repository_metadata(ctx.client, full_name)
        captured_at = ctx.now()
        repository_id = repo_json.get("id")
        if not isinstance(repository_id, int) or isinstance(repository_id, bool):
            raise GitHubResponseError("Repository payload has no integer 'id'")
        outcome.repository_id = repository_id
        canonical = repo_json.get("full_name")
        target = canonical if isinstance(canonical, str) and "/" in canonical else full_name
        if target.lower() != full_name.lower():
            repo_checks.append(
                CheckResult(
                    "repository_renamed", "extract", "repository", "warning", "failed",
                    repository_full_name=full_name, details={"resolves_to": target},
                )
            )  # fmt: skip

        watermarks: dict[str, dict[str, datetime]] = {}
        if ctx.engine is not None:
            with ctx.engine.connect() as conn:
                watermarks = get_watermarks(conn, repository_id)
        windows = {}
        for resource in (ISSUES, PULL_REQUESTS):
            stored = watermarks.get(resource, {})
            windows[resource] = plan_window(
                watermark=stored.get("high_watermark"),
                coverage_start=stored.get("coverage_start_at"),
                now=captured_at,
                history_days=settings.extraction.history_days,
                overlap_hours=settings.extraction.incremental_overlap_hours,
                backfill_open_items=settings.extraction.backfill_open_items,
                full_refresh=ctx.full_refresh,
            )
            outcome.modes[resource] = windows[resource].mode

        issues_extract = extract_issues(ctx.client, target, windows[ISSUES])
        pulls_extract = extract_pull_requests(ctx.client, target, windows[PULL_REQUESTS])
        repo_checks.append(_completeness_check(issues_extract, full_name, "issue"))
        repo_checks.append(_completeness_check(pulls_extract, full_name, "pull_request"))

        # -------------------------------------------------------------- transform
        repo_tr, snap_tr = transform_repository(repo_json, captured_at)
        issue_tr = transform_issues(issues_extract.items, repository_id)
        pr_tr = transform_pull_requests(pulls_extract.items, repository_id)
        repo_checks.append(
            CheckResult(
                "issues_endpoint_prs_excluded", "transform", "issue", "info", "passed",
                repository_full_name=full_name, observed_value=issue_tr.excluded_pull_requests,
                details={"note": "items with a pull_request key are loaded only from /pulls"},
            )
        )  # fmt: skip
        for entity, tr in (("issue", issue_tr), ("pull_request", pr_tr)):
            repo_checks.append(
                CheckResult(
                    "in_batch_duplicates_dropped", "transform", entity, "info", "passed",
                    repository_full_name=full_name, observed_value=tr.duplicates_dropped,
                )
            )  # fmt: skip
            repo_checks.append(
                CheckResult(
                    "null_optional_fields", "transform", entity, "info", "passed",
                    repository_full_name=full_name, records_evaluated=len(tr.frame),
                    details={"null_counts": tr.null_optional_counts or {}},
                )
            )  # fmt: skip

        # --------------------------------------------------------------- validate
        repo_v = validate_frame(
            repo_tr.frame, repository_schema(), entity="repository",
            repository_full_name=full_name, id_column="repository_id",
        )  # fmt: skip
        snap_v = validate_frame(
            snap_tr.frame, snapshot_schema(), entity="repository_snapshot",
            repository_full_name=full_name, id_column="repository_id",
        )  # fmt: skip
        issue_v = validate_frame(
            issue_tr.frame, issue_schema(repository_id), entity="issue",
            repository_full_name=full_name, id_column="issue_id", number_column="issue_number",
        )  # fmt: skip
        pr_v = validate_frame(
            pr_tr.frame, pull_request_schema(repository_id), entity="pull_request",
            repository_full_name=full_name, id_column="pull_request_id",
            number_column="pr_number",
        )  # fmt: skip
        for v, entity in (
            (repo_v, "repository"),
            (snap_v, "repository_snapshot"),
            (issue_v, "issue"),
            (pr_v, "pull_request"),
        ):
            repo_checks.extend(_validation_checks(v, full_name, threshold, entity))
        unknown = unknown_state_reasons(issue_v.valid)
        repo_checks.append(
            CheckResult(
                "unknown_state_reason", "validate", "issue", "warning",
                "failed" if unknown else "passed", repository_full_name=full_name,
                records_failed=sum(unknown.values()), details={"values": unknown},
            )
        )  # fmt: skip
        dupes = duplicate_keys(issue_v.valid, "issue_id") + duplicate_keys(
            pr_v.valid, "pull_request_id"
        )
        repo_checks.append(
            CheckResult(
                "primary_key_uniqueness", "validate", "repository", "error",
                "passed" if dupes == 0 else "failed", repository_full_name=full_name,
                records_failed=dupes,
            )
        )  # fmt: skip

        all_rejections = (
            repo_v.rejections + snap_v.rejections + issue_v.rejections + pr_v.rejections
        )
        rejected_rows = (
            repo_v.rejected_rows + snap_v.rejected_rows + issue_v.rejected_rows + pr_v.rejected_rows
        )
        extracted = len(repo_tr.frame) + len(snap_tr.frame) + len(issue_tr.frame) + len(pr_tr.frame)
        ctx.totals["extracted"] += extracted
        ctx.totals["rejected"] += rejected_rows
        room = REJECTION_SAMPLE_LIMIT - len(ctx.rejections_sample)
        if room > 0:
            ctx.rejections_sample.extend(
                {k: r[k] for k in ("entity", "repository_full_name", "source_number",
                                   "check_name", "failure_reason")}
                for r in all_rejections[:room]
            )  # fmt: skip
        if ctx.engine is not None and all_rejections:
            record_rejections(ctx.engine, ctx.run_id, all_rejections)

        if repo_v.rejected_rows:
            raise LoadError("Repository metadata failed validation; nothing loaded")
        if dupes:
            raise LoadError("Duplicate primary keys remained after deduplication")

        counts = {
            "repository": (len(repo_tr.frame), repo_v.rejected_rows),
            "repository_snapshot": (len(snap_tr.frame), snap_v.rejected_rows),
            "issue": (len(issue_tr.frame), issue_v.rejected_rows),
            "pull_request": (len(pr_tr.frame), pr_v.rejected_rows),
        }
        if ctx.engine is None:
            outcome.entities = {e: {"extracted": n, "rejected": r} for e, (n, r) in counts.items()}
            outcome.status = "succeeded"
            return outcome

        # ------------------------------------------------------------------- load
        rejection_ok = {
            ISSUES: issue_v.rejection_rate <= threshold,
            PULL_REQUESTS: pr_v.rejection_rate <= threshold,
        }
        batch = settings.database.batch_size
        stats: dict[str, UpsertStats] = {}
        watermark_results: dict[str, tuple[datetime | None, str]] = {}
        with ctx.engine.begin() as conn:
            stats["repository"] = upsert_repository(conn, repo_v.valid, ctx.run_id, batch)
            stats["repository_snapshot"] = upsert_snapshot(conn, snap_v.valid, ctx.run_id, batch)
            stats["issue"] = upsert_issues(
                conn, issue_v.valid, ctx.run_id, batch, full_refresh=ctx.full_refresh
            )
            stats["pull_request"] = upsert_pull_requests(
                conn, pr_v.valid, ctx.run_id, batch, full_refresh=ctx.full_refresh
            )

            # Reconciliation: every valid row must now exist in its table.
            issue_ids = [int(i) for i in issue_v.valid["issue_id"].tolist()]
            pr_ids = [int(i) for i in pr_v.valid["pull_request_id"].tolist()]
            present = {
                "issue": (
                    len(issue_ids),
                    count_present(conn, github_issues, "issue_id", issue_ids),
                ),
                "pull_request": (
                    len(pr_ids),
                    count_present(conn, pull_requests, "pull_request_id", pr_ids),
                ),
            }
            snapshot_rows = conn.execute(
                repository_snapshots.select().where(
                    repository_snapshots.c.repository_id == repository_id,
                    repository_snapshots.c.snapshot_date == captured_at.date(),
                )
            ).first()
            for entity, (expected, found) in present.items():
                st = stats[entity]
                ok = expected == found and st.inserted + st.updated <= st.attempted
                repo_checks.append(
                    CheckResult(
                        "load_reconciliation", "load", entity, "error",
                        "passed" if ok else "failed", repository_full_name=full_name,
                        records_evaluated=expected, records_failed=expected - found,
                        details=st.as_dict(),
                    )
                )  # fmt: skip
                if not ok:
                    raise LoadError(f"{entity} reconciliation failed: {found}/{expected} present")
            if len(snap_v.valid) and snapshot_rows is None:
                raise LoadError("Snapshot row missing after upsert")

            for resource, extract in ((ISSUES, issues_extract), (PULL_REQUESTS, pulls_extract)):
                value, reason = decide_watermark(extract, rejection_ok=rejection_ok[resource])
                if value is not None:
                    advance_watermark(
                        conn,
                        repository_id=repository_id,
                        resource=resource,
                        high_watermark=value,
                        coverage_start_at=extract.window.coverage_start,
                        run_id=ctx.run_id,
                    )
                watermark_results[resource] = (value, reason)

        loaded = True
        for resource, (value, reason) in watermark_results.items():
            outcome.watermarks[resource] = f"{reason}: {iso_z(value)}" if value else reason
            repo_checks.append(
                CheckResult(
                    "watermark_advancement", "load",
                    "issue" if resource == ISSUES else "pull_request",
                    "warning", "passed" if value is not None else "failed",
                    repository_full_name=full_name,
                    details={"reason": reason, "value": iso_z(value) if value else None},
                )
            )  # fmt: skip
        for entity, (n, r) in counts.items():
            st = stats[entity]
            outcome.entities[entity] = {"extracted": n, "rejected": r, **st.as_dict()}
            ctx.totals["inserted"] += st.inserted
            ctx.totals["updated"] += st.updated
            ctx.totals["unchanged"] += st.unchanged
        outcome.status = "succeeded"
        logger.info(
            "Loaded %s (%s): %s",
            full_name,
            "/".join(f"{k}={v}" for k, v in outcome.modes.items()),
            "; ".join(
                f"{e} {d['extracted']} extracted, {d['rejected']} rejected, "
                f"{d['inserted']} inserted, {d['updated']} updated"
                for e, d in outcome.entities.items()
            ),
        )
        return outcome
    except (GitHubAuthError, RateLimitExceededError) as exc:
        outcome.fail(exc)
        raise
    except (OssHealthError, SQLAlchemyError) as exc:
        logger.error("Repository failed: %s: %s", type(exc).__name__, safe_error_message(exc))
        outcome.fail(exc)
        return outcome
    finally:
        if outcome.status == "failed":
            repo_checks.append(
                CheckResult(
                    "repository_processing", "load", "repository", "error", "failed",
                    repository_full_name=full_name,
                    details={"error_type": outcome.error_type, "error": outcome.error_message},
                )
            )  # fmt: skip
        for check in repo_checks:
            check.repository_id = outcome.repository_id if loaded else None
        ctx.checks.extend(repo_checks)
        if ctx.engine is not None:
            try:
                record_checks(ctx.engine, ctx.run_id, repo_checks)
            except SQLAlchemyError as exc:
                logger.error("Could not persist quality checks: %s", safe_error_message(exc))


def _post_load_checks(ctx: _Context, outcomes: list[RepoOutcome]) -> list[CheckResult]:
    assert ctx.engine is not None
    checks: list[CheckResult] = []
    settings = ctx.settings
    with ctx.engine.connect() as conn:
        overlap = conn.execute(
            text(
                """
                SELECT r.full_name, count(*) AS n
                FROM oss_health.github_issues i
                JOIN oss_health.pull_requests p
                  ON p.repository_id = i.repository_id AND p.pr_number = i.issue_number
                JOIN oss_health.repositories r ON r.repository_id = i.repository_id
                GROUP BY r.full_name
                """
            )
        ).all()
        overlap_total = sum(r.n for r in overlap)
        checks.append(
            CheckResult(
                "issue_pr_number_overlap", "post_load", "run", "error",
                "passed" if overlap_total == 0 else "failed", records_failed=overlap_total,
                details={"by_repository": {r.full_name: r.n for r in overlap}},
            )
        )  # fmt: skip

        orphans = conn.execute(
            text(
                """
                SELECT
                  (SELECT count(*) FROM oss_health.github_issues i
                    WHERE NOT EXISTS (SELECT 1 FROM oss_health.repositories r
                                      WHERE r.repository_id = i.repository_id)) +
                  (SELECT count(*) FROM oss_health.pull_requests p
                    WHERE NOT EXISTS (SELECT 1 FROM oss_health.repositories r
                                      WHERE r.repository_id = p.repository_id)) +
                  (SELECT count(*) FROM oss_health.repository_snapshots s
                    WHERE NOT EXISTS (SELECT 1 FROM oss_health.repositories r
                                      WHERE r.repository_id = s.repository_id))
                """
            )
        ).scalar_one()
        checks.append(
            CheckResult(
                "foreign_key_consistency", "post_load", "run", "error",
                "passed" if orphans == 0 else "failed", records_failed=int(orphans),
            )
        )  # fmt: skip

        today = ctx.now().date()
        freshness_cutoff = ctx.now() - timedelta(hours=settings.validation.freshness_warning_hours)
        for outcome in outcomes:
            if outcome.status != "succeeded" or outcome.repository_id is None:
                continue
            has_snapshot = conn.execute(
                text(
                    "SELECT EXISTS (SELECT 1 FROM oss_health.repository_snapshots "
                    "WHERE repository_id = :rid AND snapshot_date = :d)"
                ),
                {"rid": outcome.repository_id, "d": today},
            ).scalar_one()
            checks.append(
                CheckResult(
                    "snapshot_coverage", "post_load", "repository_snapshot", "error",
                    "passed" if has_snapshot else "failed",
                    repository_full_name=outcome.full_name, repository_id=outcome.repository_id,
                    details={"snapshot_date": today.isoformat()},
                )
            )  # fmt: skip
            newest = conn.execute(
                text(
                    "SELECT GREATEST((SELECT max(updated_at) FROM oss_health.github_issues "
                    "WHERE repository_id = :rid), (SELECT max(updated_at) FROM "
                    "oss_health.pull_requests WHERE repository_id = :rid))"
                ),
                {"rid": outcome.repository_id},
            ).scalar_one()
            fresh = newest is not None and newest >= freshness_cutoff
            checks.append(
                CheckResult(
                    "data_freshness", "post_load", "repository", "warning",
                    "passed" if fresh else "failed", repository_full_name=outcome.full_name,
                    repository_id=outcome.repository_id,
                    threshold_value=settings.validation.freshness_warning_hours,
                    details={"newest_update": newest.isoformat() if newest else None},
                )
            )  # fmt: skip

    previous = last_successful_run_started_at(ctx.engine, ctx.run_id)
    if previous is None:
        checks.append(
            CheckResult(
                "last_successful_run_age", "post_load", "run", "warning", "skipped",
                details={"note": "no previous successful run"},
            )
        )  # fmt: skip
    else:
        age_hours = (ctx.now() - previous).total_seconds() / 3600
        checks.append(
            CheckResult(
                "last_successful_run_age", "post_load", "run", "warning",
                "passed" if age_hours <= settings.validation.last_success_warning_hours
                else "failed",
                observed_value=round(age_hours, 2),
                threshold_value=settings.validation.last_success_warning_hours,
            )
        )  # fmt: skip
    return checks


def run_pipeline(
    settings: Settings,
    *,
    trigger: str = "manual",
    repositories: list[str] | None = None,
    full_refresh: bool = False,
    dry_run: bool = False,
    client: GitHubClient | None = None,
    engine: Engine | None = None,
    now: Callable[[], datetime] | None = None,
) -> RunResult:
    """Run the pipeline once. Never raises; the outcome is in the returned ``RunResult``."""
    clock = now or (lambda: datetime.now(UTC))
    started_at = clock()
    run_id: uuid.UUID | None = None
    outcomes: list[RepoOutcome] = []
    errors: list[str] = []
    status = "not_started"
    fatal = False
    lock: RunLock | None = None
    owns_engine = False
    ctx: _Context | None = None
    targets = list(repositories) if repositories else list(settings.repositories)

    try:
        if trigger not in ("manual", "scheduled", "ci"):
            raise ConfigError(f"Unknown trigger {trigger!r}")
        if repositories:
            settings = settings.with_repositories(repositories)
        if not settings.github_token:
            logger.warning("No GITHUB_TOKEN configured: unauthenticated limit is 60 requests/hour")
        if not dry_run:
            if engine is None:
                if not settings.database_url:
                    raise PreconditionError("PIPELINE_DATABASE_URL is not set (use --dry-run?)")
                engine = create_db_engine(
                    settings.database_url,
                    application_name="oss-health-pipeline",
                    statement_timeout_ms=settings.database.statement_timeout_ms,
                )
                owns_engine = True
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
            except SQLAlchemyError as exc:
                raise PreconditionError(f"Database unreachable: {type(exc).__name__}") from exc
            lock = RunLock(engine)
            lock.acquire()
            close_stale_runs(engine, settings.database.stale_run_after_hours)
            purge_rejected_records(engine, settings.database.rejected_records_retention_days)

        run_id = uuid.uuid4()
        run_id_var.set(str(run_id))
        client = client or GitHubClient(settings.github, settings.github_token)
        ctx = _Context(settings, client, None if dry_run else engine, run_id, full_refresh, clock)
        if ctx.engine is not None:
            start_run(
                ctx.engine,
                run_id=run_id,
                trigger=trigger,
                full_refresh=full_refresh,
                pipeline_version=__version__,
                git_commit_sha=os.environ.get("GITHUB_SHA"),
                config_snapshot=settings.public_dict(),
                repositories_requested=len(targets),
                started_at=started_at,
            )
        status = "running"
        logger.info(
            "Run %s started (%s, %s repositories%s%s)", run_id, trigger, len(targets),
            ", full refresh" if full_refresh else "", ", dry run" if dry_run else "",
        )  # fmt: skip

        client.rate_limit_snapshot()
        for index, full_name in enumerate(targets):
            token = repository_var.set(full_name)
            try:
                outcomes.append(_process_repository(ctx, full_name))
            except GitHubAuthError as exc:
                fatal = True
                errors.append(f"{full_name}: {type(exc).__name__}: {safe_error_message(exc)}")
                outcomes.append(_failed(full_name, exc))
                outcomes.extend(_failed(n, "not attempted: run stopped", "NotAttempted")
                                for n in targets[index + 1:])  # fmt: skip
                break
            except RateLimitExceededError as exc:
                errors.append(f"{full_name}: {type(exc).__name__}: {safe_error_message(exc)}")
                outcomes.append(_failed(full_name, exc))
                outcomes.extend(_failed(n, "rate_limit_wait_exceeded", "RateLimitExceededError")
                                for n in targets[index + 1:])  # fmt: skip
                break
            finally:
                repository_var.reset(token)

        for o in outcomes:
            if (
                o.status == "failed"
                and o.error_message
                and not any(e.startswith(f"{o.full_name}:") for e in errors)
            ):
                errors.append(f"{o.full_name}: {o.error_type}: {o.error_message}")

        if ctx.engine is not None and not fatal and any(o.status == "succeeded" for o in outcomes):
            post = _post_load_checks(ctx, outcomes)
            ctx.checks.extend(post)
            record_checks(ctx.engine, run_id, post)
            if all(o.status == "succeeded" for o in outcomes) and not repositories:
                with ctx.engine.begin() as conn:
                    untracked = set_tracked_repositories(
                        conn, [o.repository_id for o in outcomes if o.repository_id]
                    )
                if untracked:
                    logger.info("Marked %s repositories as untracked", untracked)
        status = compute_status(outcomes, ctx.checks, fatal)
    except (ConfigError, PreconditionError) as exc:
        status = "not_started" if run_id is None or ctx is None else "failed"
        errors.append(f"{type(exc).__name__}: {safe_error_message(exc)}")
        logger.error("%s", safe_error_message(exc))
    except Exception as exc:
        status = "failed"
        fatal = True
        errors.append(f"{type(exc).__name__}: {safe_error_message(exc)}")
        logger.exception("Run failed with an unexpected error")
    finally:
        if ctx is not None and status == "running":
            status = "failed"
        exit_code = EXIT_CODES[status]
        finished_at = clock()
        client_obj = ctx.client if ctx else client
        api = {
            "requests_made": getattr(client_obj, "request_count", 0),
            "rate_limit_remaining_end": getattr(client_obj, "rate_limit_remaining", None),
            "rate_limit_waits": getattr(client_obj, "rate_limit_waits", 0),
        }
        totals = dict(ctx.totals) if ctx else {}
        result = RunResult(
            run_id=run_id,
            status=status,
            exit_code=exit_code,
            started_at=started_at,
            finished_at=finished_at,
            trigger=trigger,
            dry_run=dry_run,
            full_refresh=full_refresh,
            repositories=outcomes,
            checks=ctx.checks if ctx else [],
            rejections_sample=ctx.rejections_sample if ctx else [],
            errors=errors,
            totals=totals,
            api=api,
            config=settings.public_dict(),
        )
        if ctx is not None and ctx.engine is not None and run_id is not None:
            try:
                finalize_run(
                    ctx.engine,
                    run_id,
                    {
                        "status": status,
                        "exit_code": exit_code,
                        "repositories_succeeded": sum(
                            1 for o in outcomes if o.status == "succeeded"
                        ),
                        "repositories_failed": sum(1 for o in outcomes if o.status != "succeeded"),
                        "records_extracted": totals.get("extracted", 0),
                        "records_rejected": totals.get("rejected", 0),
                        "records_inserted": totals.get("inserted", 0),
                        "records_updated": totals.get("updated", 0),
                        "api_requests_made": api["requests_made"],
                        "rate_limit_remaining": api["rate_limit_remaining_end"],
                        "error_type": _first_error_type(outcomes, errors, status),
                        "error_message": "; ".join(errors)[:2000] or None,
                    },
                )
            except SQLAlchemyError as exc:
                logger.error(
                    "Could not finalize the run row (a later run will close it): %s",
                    safe_error_message(exc),
                )
                if result.exit_code == 0:
                    result.exit_code = 1
                    result.status = "failed"
        try:
            result.report_path = write_report(result.as_report(), settings.reports_path)
            logger.info("Quality report written to %s", result.report_path)
        except OSError as exc:
            logger.error("Could not write the quality report: %s", safe_error_message(exc))
            result.exit_code = max(result.exit_code, 1)
        if lock is not None:
            lock.release()
        if owns_engine and engine is not None:
            engine.dispose()
        logger.info("Run finished: status=%s exit_code=%s", result.status, result.exit_code)
        run_id_var.set("-")
    return result


def _failed(full_name: str, exc: BaseException | str, error_type: str | None = None) -> RepoOutcome:
    outcome = RepoOutcome(full_name)
    outcome.fail(exc, error_type)
    return outcome


def _first_error_type(outcomes: list[RepoOutcome], errors: list[str], status: str) -> str | None:
    if status == "success":
        return None
    for o in outcomes:
        if o.error_type:
            return o.error_type
    if errors:
        return errors[0].split(":", 1)[0]
    return "QualityCheckFailed" if status == "partial_success" else None
