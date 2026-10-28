"""Parameterized, read-only queries against the ``analytics`` views only.

Every function takes bound parameters; no user input is ever interpolated into SQL text.
Metrics with no underlying data are returned as ``None`` (never as zero).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import Connection, text

REPOSITORY_SORTS = {
    "stars": "stars_latest",
    "forks": "forks_latest",
    "open_issues": "open_issues",
    "open_prs": "open_prs",
    "name": "lower(full_name)",
}

_REPOSITORY_COLUMNS = """
    repository_id, full_name, owner_login, repo_name, description, html_url, primary_language,
    license_spdx_id, topics, is_fork, is_archived, created_at_utc, pushed_at_utc,
    metadata_refreshed_at_utc, coverage_start_date, issues_watermark_utc, prs_watermark_utc,
    first_snapshot_date, latest_snapshot_date, snapshot_count, stars_latest, forks_latest,
    watchers_latest, open_issues, closed_issues_in_coverage, open_prs, open_draft_prs,
    merged_prs_in_coverage, closed_unmerged_prs_in_coverage
"""


def _clean(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


def _rows(result) -> list[dict[str, Any]]:
    return [{k: _clean(v) for k, v in row._mapping.items()} for row in result]


def _one(result) -> dict[str, Any] | None:
    row = result.first()
    return None if row is None else {k: _clean(v) for k, v in row._mapping.items()}


def list_repositories(
    conn: Connection,
    *,
    search: str | None,
    language: str | None,
    sort: str,
    order: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    order_sql = "DESC" if order == "desc" else "ASC"
    sort_sql = REPOSITORY_SORTS[sort]  # validated against an allow-list by the route
    params = {
        "search": f"%{search.lower()}%" if search else None,
        "language": language.lower() if language else None,
        "limit": limit,
        "offset": offset,
    }
    where = """
        WHERE (CAST(:search AS text) IS NULL OR lower(full_name) LIKE CAST(:search AS text)
               OR lower(coalesce(description, '')) LIKE CAST(:search AS text))
          AND (CAST(:language AS text) IS NULL OR lower(primary_language) = CAST(:language AS text))
    """
    total = conn.execute(
        text(f"SELECT count(*) FROM analytics.dim_repository {where}"), params
    ).scalar_one()
    rows = conn.execute(
        text(
            f"SELECT {_REPOSITORY_COLUMNS} FROM analytics.dim_repository {where} "
            f"ORDER BY {sort_sql} {order_sql} NULLS LAST, repository_id "
            "LIMIT :limit OFFSET :offset"
        ),
        params,
    )
    return _rows(rows), int(total)


def get_repository(conn: Connection, repository_id: int) -> dict[str, Any] | None:
    return _one(
        conn.execute(
            text(
                f"SELECT {_REPOSITORY_COLUMNS} FROM analytics.dim_repository "
                "WHERE repository_id = :rid"
            ),
            {"rid": repository_id},
        )
    )


def latest_runs(conn: Connection, limit: int) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            text(
                """
                SELECT run_id, status, trigger_type, is_full_refresh, started_at_utc,
                       finished_at_utc, run_date, duration_seconds, exit_code,
                       repositories_requested, repositories_succeeded, repositories_failed,
                       records_extracted, records_rejected, records_inserted, records_updated,
                       api_requests_made, rejection_rate, error_type, error_message,
                       checks_evaluated, checks_passed, error_checks_failed, warning_checks_failed
                FROM analytics.fact_pipeline_run
                ORDER BY started_at_utc DESC
                LIMIT :limit
                """
            ),
            {"limit": limit},
        )
    )


def run_statistics(conn: Connection, days: int) -> dict[str, Any]:
    row = (
        _one(
            conn.execute(
                text(
                    """
                SELECT count(*) FILTER (WHERE status <> 'running')       AS runs_finished,
                       count(*) FILTER (WHERE status = 'success')        AS runs_succeeded,
                       count(*) FILTER (WHERE status = 'partial_success') AS runs_partial,
                       count(*) FILTER (WHERE status = 'failed')         AS runs_failed,
                       count(*) FILTER (WHERE status = 'running')        AS runs_running
                FROM analytics.fact_pipeline_run
                WHERE started_at_utc >= (now() AT TIME ZONE 'UTC') - make_interval(days => :days)
                """
                ),
                {"days": days},
            )
        )
        or {}
    )
    finished = row.get("runs_finished") or 0
    row["success_rate"] = (row["runs_succeeded"] / finished) if finished else None
    row["window_days"] = days
    return row


def last_run(conn: Connection, *, status: str | None = None) -> dict[str, Any] | None:
    return _one(
        conn.execute(
            text(
                """
                SELECT run_id, status, started_at_utc, finished_at_utc, repositories_requested,
                       repositories_succeeded, repositories_failed, records_rejected,
                       error_checks_failed, warning_checks_failed, error_type, error_message
                FROM analytics.fact_pipeline_run
                WHERE (CAST(:status AS text) IS NULL AND status <> 'running')
                   OR status = CAST(:status AS text)
                ORDER BY started_at_utc DESC
                LIMIT 1
                """
            ),
            {"status": status},
        )
    )


def quality_checks_for_run(conn: Connection, run_id: str) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            text(
                """
                SELECT check_name, check_stage, entity, repository_full_name, severity, status,
                       records_evaluated, records_failed, observed_value, threshold_value,
                       details, checked_at_utc
                FROM analytics.fact_data_quality_check
                WHERE run_id = CAST(:run_id AS uuid)
                ORDER BY CASE status WHEN 'failed' THEN 0 ELSE 1 END,
                         CASE severity WHEN 'error' THEN 0 WHEN 'warning' THEN 1 ELSE 2 END,
                         check_name, repository_full_name NULLS FIRST, entity
                LIMIT 1000
                """
            ),
            {"run_id": run_id},
        )
    )


def summary_kpis(conn: Connection, days: int) -> dict[str, Any]:
    repos = (
        _one(
            conn.execute(
                text(
                    """
                SELECT count(*)                                  AS repository_count,
                       count(stars_latest)                       AS repositories_with_snapshot,
                       sum(stars_latest)                         AS stars_total,
                       sum(forks_latest)                         AS forks_total,
                       count(open_issues)                        AS repositories_with_issue_data,
                       sum(open_issues)                          AS open_issues_total,
                       count(open_prs)                           AS repositories_with_pr_data,
                       sum(open_prs)                             AS open_prs_total,
                       min(coverage_start_date)                  AS earliest_coverage_start,
                       max(latest_snapshot_date)                 AS latest_snapshot_date,
                       min(first_snapshot_date)                  AS first_snapshot_date
                FROM analytics.dim_repository
                """
                )
            )
        )
        or {}
    )
    prs = pull_request_metrics(conn, repository_id=None, days=days)
    issues = (
        _one(
            conn.execute(
                text(
                    """
                SELECT count(*) FILTER (WHERE closed_date >= CAST(:since AS date)) AS issues_closed,
                       count(*) FILTER (WHERE created_date >= CAST(:since AS date)) AS issues_opened,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY open_age_days)
                           FILTER (WHERE is_open)                                    AS median_open_issue_age_days
                FROM analytics.fact_issue
                """
                ),
                {"since": _since(days)},
            )
        )
        or {}
    )
    return {"repositories": repos, "pull_requests": prs, "issues": issues, "window_days": days}


def _since(days: int) -> date:
    from datetime import UTC, datetime, timedelta

    return (datetime.now(UTC) - timedelta(days=days)).date()


def stars_forks(
    conn: Connection, *, repository_id: int | None, start: date, end: date
) -> list[dict[str, Any]]:
    return _rows(
        conn.execute(
            text(
                """
                SELECT s.repository_id, d.full_name, s.snapshot_date, s.stargazers_count,
                       s.forks_count, s.subscribers_count, s.stars_delta, s.forks_delta,
                       s.previous_snapshot_date
                FROM analytics.fact_repository_snapshot s
                JOIN analytics.dim_repository d USING (repository_id)
                WHERE (CAST(:rid AS bigint) IS NULL OR s.repository_id = CAST(:rid AS bigint))
                  AND s.snapshot_date BETWEEN :start AND :end
                ORDER BY s.snapshot_date, d.full_name
                LIMIT 20000
                """
            ),
            {"rid": repository_id, "start": start, "end": end},
        )
    )


def weekly_activity(
    conn: Connection, *, repository_id: int | None, weeks: int
) -> list[dict[str, Any]]:
    """Weekly activity within each repository's coverage window (Monday-start UTC weeks).

    When no repository is given, weeks are summed across repositories and only weeks covered by
    every repository's coverage window are returned, so totals stay comparable.
    """
    if repository_id is not None:
        return _rows(
            conn.execute(
                text(
                    """
                    SELECT week_start_date, week_end_date, issues_opened, issues_closed,
                           prs_opened, prs_merged, prs_closed_unmerged, is_complete_week,
                           1 AS repositories_included
                    FROM analytics.agg_weekly_activity
                    WHERE repository_id = :rid AND is_within_coverage
                    ORDER BY week_start_date DESC
                    LIMIT :weeks
                    """
                ),
                {"rid": repository_id, "weeks": weeks},
            )
        )[::-1]
    return _rows(
        conn.execute(
            text(
                """
                WITH covered AS (
                    SELECT count(*) AS n FROM analytics.dim_repository
                    WHERE coverage_start_date IS NOT NULL
                )
                SELECT week_start_date, max(week_end_date) AS week_end_date,
                       sum(issues_opened)::int AS issues_opened,
                       sum(issues_closed)::int AS issues_closed,
                       sum(prs_opened)::int AS prs_opened,
                       sum(prs_merged)::int AS prs_merged,
                       sum(prs_closed_unmerged)::int AS prs_closed_unmerged,
                       bool_and(is_complete_week) AS is_complete_week,
                       count(*)::int AS repositories_included
                FROM analytics.agg_weekly_activity, covered
                WHERE is_within_coverage
                GROUP BY week_start_date, covered.n
                HAVING count(*) = covered.n
                ORDER BY week_start_date DESC
                LIMIT :weeks
                """
            ),
            {"weeks": weeks},
        )
    )[::-1]


def issue_metrics(conn: Connection, *, repository_id: int | None, days: int) -> dict[str, Any]:
    params = {"rid": repository_id, "since": _since(days)}
    flt = "(CAST(:rid AS bigint) IS NULL OR repository_id = CAST(:rid AS bigint))"
    totals = (
        _one(
            conn.execute(
                text(
                    f"""
                SELECT count(*) FILTER (WHERE is_open)                         AS open_issues,
                       count(*) FILTER (WHERE NOT is_open)                     AS closed_issues_in_coverage,
                       count(*) FILTER (WHERE closed_date >= :since)           AS closed_in_window,
                       count(*) FILTER (WHERE created_date >= :since)          AS opened_in_window,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_close)
                           FILTER (WHERE closed_date >= :since)                AS median_hours_to_close,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY open_age_days)
                           FILTER (WHERE is_open)                              AS median_open_age_days,
                       count(*) FILTER (WHERE is_open AND is_bot_author)       AS open_issues_by_bots
                FROM analytics.fact_issue
                WHERE {flt}
                """
                ),
                params,
            )
        )
        or {}
    )
    buckets = _rows(
        conn.execute(
            text(
                f"""
                SELECT backlog_age_bucket AS bucket, backlog_age_bucket_order AS bucket_order,
                       count(*)::int AS open_issues
                FROM analytics.fact_issue
                WHERE is_open AND {flt}
                GROUP BY 1, 2
                ORDER BY 2
                """
            ),
            params,
        )
    )
    oldest = _rows(
        conn.execute(
            text(
                """
                SELECT f.issue_number, f.title, f.html_url, f.open_age_days, f.comments_count,
                       d.full_name
                FROM analytics.fact_issue f JOIN analytics.dim_repository d USING (repository_id)
                WHERE f.is_open AND (CAST(:rid AS bigint) IS NULL
                                     OR f.repository_id = CAST(:rid AS bigint))
                ORDER BY f.open_age_days DESC NULLS LAST, f.issue_number
                LIMIT 10
                """
            ),
            params,
        )
    )
    return {"totals": totals, "backlog_age": buckets, "oldest_open": oldest, "window_days": days}


def pull_request_metrics(
    conn: Connection, *, repository_id: int | None, days: int
) -> dict[str, Any]:
    params = {"rid": repository_id, "since": _since(days)}
    row = (
        _one(
            conn.execute(
                text(
                    """
                SELECT count(*) FILTER (WHERE outcome = 'open')                   AS open_prs,
                       count(*) FILTER (WHERE outcome = 'open' AND is_draft)      AS open_draft_prs,
                       count(*) FILTER (WHERE outcome = 'merged'
                                        AND merged_date >= :since)                AS merged_in_window,
                       count(*) FILTER (WHERE outcome = 'closed_unmerged'
                                        AND closed_date >= :since)                AS closed_unmerged_in_window,
                       count(*) FILTER (WHERE created_date >= :since)             AS opened_in_window,
                       percentile_cont(0.5) WITHIN GROUP (ORDER BY hours_to_merge)
                           FILTER (WHERE outcome = 'merged' AND merged_date >= :since)
                                                                                  AS median_hours_to_merge
                FROM analytics.fact_pull_request
                WHERE (CAST(:rid AS bigint) IS NULL OR repository_id = CAST(:rid AS bigint))
                """
                ),
                params,
            )
        )
        or {}
    )
    merged = row.get("merged_in_window") or 0
    closed_unmerged = row.get("closed_unmerged_in_window") or 0
    decided = merged + closed_unmerged
    row["merge_rate"] = (merged / decided) if decided else None
    row["window_days"] = days
    return row


def ping(conn: Connection) -> bool:
    return conn.execute(text("SELECT count(*) >= 0 FROM analytics.dim_repository")).scalar_one()
