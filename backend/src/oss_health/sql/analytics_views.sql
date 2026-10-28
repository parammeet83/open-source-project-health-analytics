-- Analytics view contract (8 views). Consumed by the public API (oss_health_reader) and the
-- optional Power BI model. All timestamps are UTC; weeks start on Monday (ISO).
-- Statements are separated by a marker comment line (see oss_health.sql_files).

CREATE OR REPLACE VIEW analytics.dim_repository AS
WITH wm AS (
    SELECT repository_id,
           count(*)                                                    AS resources_covered,
           max(coverage_start_at)                                      AS coverage_start_at,
           max(high_watermark) FILTER (WHERE resource = 'issues')        AS issues_watermark,
           max(high_watermark) FILTER (WHERE resource = 'pull_requests') AS prs_watermark
    FROM oss_health.extraction_watermarks
    GROUP BY repository_id
),
latest AS (
    SELECT DISTINCT ON (repository_id)
           repository_id, snapshot_date, captured_at, stargazers_count, forks_count,
           subscribers_count
    FROM oss_health.repository_snapshots
    ORDER BY repository_id, snapshot_date DESC
),
snaps AS (
    SELECT repository_id, min(snapshot_date) AS first_snapshot_date, count(*) AS snapshot_count
    FROM oss_health.repository_snapshots
    GROUP BY repository_id
),
issues AS (
    SELECT repository_id,
           count(*) FILTER (WHERE state = 'open')   AS open_issues,
           count(*) FILTER (WHERE state = 'closed') AS closed_issues
    FROM oss_health.github_issues
    GROUP BY repository_id
),
prs AS (
    SELECT repository_id,
           count(*) FILTER (WHERE state = 'open')                          AS open_prs,
           count(*) FILTER (WHERE state = 'open' AND is_draft)             AS open_draft_prs,
           count(*) FILTER (WHERE merged_at IS NOT NULL)                   AS merged_prs,
           count(*) FILTER (WHERE state = 'closed' AND merged_at IS NULL)  AS closed_unmerged_prs
    FROM oss_health.pull_requests
    GROUP BY repository_id
)
SELECT
    r.repository_id,
    r.full_name,
    r.owner_login,
    r.repo_name,
    r.description,
    r.html_url,
    r.primary_language,
    r.license_spdx_id,
    r.topics,
    r.is_fork,
    r.is_archived,
    r.created_at AT TIME ZONE 'UTC'     AS created_at_utc,
    r.pushed_at AT TIME ZONE 'UTC'      AS pushed_at_utc,
    r.etl_updated_at AT TIME ZONE 'UTC' AS metadata_refreshed_at_utc,
    -- First full UTC day for which issue AND pull-request events are complete. NULL until both
    -- resources have completed a backfill (coverage unknown, so no trends are drawn).
    CASE WHEN wm.resources_covered = 2
         THEN date_trunc('day', (wm.coverage_start_at AT TIME ZONE 'UTC')
                                + interval '1 day' - interval '1 microsecond')::date
    END                                   AS coverage_start_date,
    wm.issues_watermark AT TIME ZONE 'UTC' AS issues_watermark_utc,
    wm.prs_watermark AT TIME ZONE 'UTC'    AS prs_watermark_utc,
    s.first_snapshot_date,
    l.snapshot_date                        AS latest_snapshot_date,
    s.snapshot_count,
    l.stargazers_count                     AS stars_latest,
    l.forks_count                          AS forks_latest,
    l.subscribers_count                    AS watchers_latest,
    -- Counts are only reported once the corresponding backfill (including all open items) has
    -- completed; otherwise they are NULL rather than a misleading zero.
    CASE WHEN wm.issues_watermark IS NOT NULL THEN coalesce(i.open_issues, 0) END   AS open_issues,
    CASE WHEN wm.issues_watermark IS NOT NULL THEN coalesce(i.closed_issues, 0) END AS closed_issues_in_coverage,
    CASE WHEN wm.prs_watermark IS NOT NULL THEN coalesce(p.open_prs, 0) END         AS open_prs,
    CASE WHEN wm.prs_watermark IS NOT NULL THEN coalesce(p.open_draft_prs, 0) END   AS open_draft_prs,
    CASE WHEN wm.prs_watermark IS NOT NULL THEN coalesce(p.merged_prs, 0) END       AS merged_prs_in_coverage,
    CASE WHEN wm.prs_watermark IS NOT NULL THEN coalesce(p.closed_unmerged_prs, 0) END
                                                                                    AS closed_unmerged_prs_in_coverage
FROM oss_health.repositories r
LEFT JOIN wm     USING (repository_id)
LEFT JOIN latest l USING (repository_id)
LEFT JOIN snaps  s USING (repository_id)
LEFT JOIN issues i USING (repository_id)
LEFT JOIN prs    p USING (repository_id)
WHERE r.is_tracked;
-- @@
CREATE OR REPLACE VIEW analytics.dim_date AS
WITH bounds AS (
    SELECT coalesce(
               LEAST(
                   (SELECT min(created_at) FROM oss_health.github_issues),
                   (SELECT min(created_at) FROM oss_health.pull_requests),
                   (SELECT min(captured_at) FROM oss_health.repository_snapshots)
               ) AT TIME ZONE 'UTC',
               now() AT TIME ZONE 'UTC'
           )::date AS first_day,
           (now() AT TIME ZONE 'UTC')::date AS last_day
)
SELECT
    d::date                                   AS date,
    EXTRACT(year FROM d)::int                 AS year,
    EXTRACT(quarter FROM d)::int              AS quarter,
    EXTRACT(month FROM d)::int                AS month,
    to_char(d, 'Mon')                         AS month_name,
    EXTRACT(isoyear FROM d)::int              AS iso_year,
    EXTRACT(week FROM d)::int                 AS iso_week,
    date_trunc('week', d)::date               AS week_start_date,
    EXTRACT(isodow FROM d)::int               AS day_of_week
FROM bounds b,
     generate_series(b.first_day::timestamp, b.last_day::timestamp, interval '1 day') AS d;
-- @@
CREATE OR REPLACE VIEW analytics.fact_repository_snapshot AS
SELECT
    s.repository_id,
    s.snapshot_date,
    s.captured_at AT TIME ZONE 'UTC'                                  AS captured_at_utc,
    s.stargazers_count,
    s.forks_count,
    s.subscribers_count,
    s.open_issues_and_prs_count,
    lag(s.snapshot_date) OVER w                                       AS previous_snapshot_date,
    s.stargazers_count - lag(s.stargazers_count) OVER w               AS stars_delta,
    s.forks_count - lag(s.forks_count) OVER w                         AS forks_delta
FROM oss_health.repository_snapshots s
JOIN oss_health.repositories r USING (repository_id)
WHERE r.is_tracked
WINDOW w AS (PARTITION BY s.repository_id ORDER BY s.snapshot_date);
-- @@
CREATE OR REPLACE VIEW analytics.fact_issue AS
WITH today AS (SELECT (now() AT TIME ZONE 'UTC')::date AS d),
base AS (
    SELECT i.*, t.d - (i.created_at AT TIME ZONE 'UTC')::date AS age_days, t.d AS today
    FROM oss_health.github_issues i
    JOIN oss_health.repositories r USING (repository_id)
    CROSS JOIN today t
    WHERE r.is_tracked
)
SELECT
    issue_id,
    repository_id,
    issue_number,
    title,
    html_url,
    state,
    (state = 'open')                                  AS is_open,
    state_reason,
    is_bot_author,
    author_association,
    comments_count,
    created_at AT TIME ZONE 'UTC'                     AS created_at_utc,
    closed_at AT TIME ZONE 'UTC'                      AS closed_at_utc,
    (created_at AT TIME ZONE 'UTC')::date             AS created_date,
    (closed_at AT TIME ZONE 'UTC')::date              AS closed_date,
    CASE WHEN closed_at IS NOT NULL
         THEN round((EXTRACT(EPOCH FROM closed_at - created_at) / 3600.0)::numeric, 2) END
                                                      AS hours_to_close,
    CASE WHEN state = 'open' THEN age_days END        AS open_age_days,
    CASE WHEN state <> 'open' THEN NULL
         WHEN age_days <= 7   THEN '0-7 days'
         WHEN age_days <= 30  THEN '8-30 days'
         WHEN age_days <= 90  THEN '31-90 days'
         WHEN age_days <= 180 THEN '91-180 days'
         WHEN age_days <= 365 THEN '181-365 days'
         ELSE '>365 days' END                         AS backlog_age_bucket,
    CASE WHEN state <> 'open' THEN NULL
         WHEN age_days <= 7   THEN 1
         WHEN age_days <= 30  THEN 2
         WHEN age_days <= 90  THEN 3
         WHEN age_days <= 180 THEN 4
         WHEN age_days <= 365 THEN 5
         ELSE 6 END                                   AS backlog_age_bucket_order
FROM base;
-- @@
CREATE OR REPLACE VIEW analytics.fact_pull_request AS
WITH today AS (SELECT (now() AT TIME ZONE 'UTC')::date AS d)
SELECT
    p.pull_request_id,
    p.repository_id,
    p.pr_number,
    p.title,
    p.html_url,
    p.state,
    CASE WHEN p.merged_at IS NOT NULL THEN 'merged'
         WHEN p.state = 'closed'      THEN 'closed_unmerged'
         ELSE 'open' END                                  AS outcome,
    p.is_draft,
    p.is_bot_author,
    p.author_association,
    p.created_at AT TIME ZONE 'UTC'                       AS created_at_utc,
    p.closed_at AT TIME ZONE 'UTC'                        AS closed_at_utc,
    p.merged_at AT TIME ZONE 'UTC'                        AS merged_at_utc,
    (p.created_at AT TIME ZONE 'UTC')::date               AS created_date,
    (p.closed_at AT TIME ZONE 'UTC')::date                AS closed_date,
    (p.merged_at AT TIME ZONE 'UTC')::date                AS merged_date,
    CASE WHEN p.merged_at IS NOT NULL
         THEN round((EXTRACT(EPOCH FROM p.merged_at - p.created_at) / 3600.0)::numeric, 2) END
                                                          AS hours_to_merge,
    CASE WHEN p.closed_at IS NOT NULL
         THEN round((EXTRACT(EPOCH FROM p.closed_at - p.created_at) / 3600.0)::numeric, 2) END
                                                          AS hours_to_close,
    CASE WHEN p.state = 'open'
         THEN t.d - (p.created_at AT TIME ZONE 'UTC')::date END AS open_age_days
FROM oss_health.pull_requests p
JOIN oss_health.repositories r USING (repository_id)
CROSS JOIN today t
WHERE r.is_tracked;
-- @@
CREATE OR REPLACE VIEW analytics.agg_weekly_activity AS
WITH repos AS (
    SELECT repository_id, coverage_start_date
    FROM analytics.dim_repository
    WHERE coverage_start_date IS NOT NULL
),
weeks AS (
    SELECT r.repository_id, r.coverage_start_date, gs::date AS week_start_date
    FROM repos r
    CROSS JOIN LATERAL generate_series(
        date_trunc('week', r.coverage_start_date::timestamp),
        date_trunc('week', now() AT TIME ZONE 'UTC'),
        interval '7 days') AS gs
),
events AS (
    SELECT repository_id, created_at AS ts, 'issue_opened' AS ev FROM oss_health.github_issues
    UNION ALL
    SELECT repository_id, closed_at, 'issue_closed'
    FROM oss_health.github_issues WHERE closed_at IS NOT NULL
    UNION ALL
    SELECT repository_id, created_at, 'pr_opened' FROM oss_health.pull_requests
    UNION ALL
    SELECT repository_id, merged_at, 'pr_merged'
    FROM oss_health.pull_requests WHERE merged_at IS NOT NULL
    UNION ALL
    SELECT repository_id, closed_at, 'pr_closed_unmerged'
    FROM oss_health.pull_requests WHERE closed_at IS NOT NULL AND merged_at IS NULL
),
counts AS (
    SELECT repository_id,
           date_trunc('week', ts AT TIME ZONE 'UTC')::date       AS week_start_date,
           count(*) FILTER (WHERE ev = 'issue_opened')            AS issues_opened,
           count(*) FILTER (WHERE ev = 'issue_closed')            AS issues_closed,
           count(*) FILTER (WHERE ev = 'pr_opened')               AS prs_opened,
           count(*) FILTER (WHERE ev = 'pr_merged')               AS prs_merged,
           count(*) FILTER (WHERE ev = 'pr_closed_unmerged')      AS prs_closed_unmerged
    FROM events
    GROUP BY 1, 2
)
SELECT
    w.repository_id,
    w.week_start_date,
    w.week_start_date + 6                                         AS week_end_date,
    coalesce(c.issues_opened, 0)                                  AS issues_opened,
    coalesce(c.issues_closed, 0)                                  AS issues_closed,
    coalesce(c.prs_opened, 0)                                     AS prs_opened,
    coalesce(c.prs_merged, 0)                                     AS prs_merged,
    coalesce(c.prs_closed_unmerged, 0)                            AS prs_closed_unmerged,
    (w.week_start_date >= w.coverage_start_date)                  AS is_within_coverage,
    (w.week_start_date + 7 <= (now() AT TIME ZONE 'UTC')::date)   AS is_complete_week
FROM weeks w
LEFT JOIN counts c USING (repository_id, week_start_date);
-- @@
CREATE OR REPLACE VIEW analytics.fact_pipeline_run AS
WITH q AS (
    SELECT run_id,
           count(*) FILTER (WHERE status <> 'skipped')                         AS checks_evaluated,
           count(*) FILTER (WHERE status = 'passed')                           AS checks_passed,
           count(*) FILTER (WHERE status = 'failed' AND severity = 'error')    AS error_checks_failed,
           count(*) FILTER (WHERE status = 'failed' AND severity = 'warning')  AS warning_checks_failed
    FROM oss_health.data_quality_checks
    GROUP BY run_id
)
SELECT
    r.run_id,
    r.status,
    r.trigger_type,
    r.is_full_refresh,
    r.started_at AT TIME ZONE 'UTC'                                     AS started_at_utc,
    r.finished_at AT TIME ZONE 'UTC'                                    AS finished_at_utc,
    (r.started_at AT TIME ZONE 'UTC')::date                             AS run_date,
    round(EXTRACT(EPOCH FROM r.finished_at - r.started_at)::numeric, 1) AS duration_seconds,
    r.exit_code,
    r.pipeline_version,
    r.repositories_requested,
    r.repositories_succeeded,
    r.repositories_failed,
    r.records_extracted,
    r.records_rejected,
    r.records_inserted,
    r.records_updated,
    r.api_requests_made,
    CASE WHEN r.records_extracted > 0
         THEN round(r.records_rejected::numeric / r.records_extracted, 6) END AS rejection_rate,
    r.error_type,
    r.error_message,
    coalesce(q.checks_evaluated, 0)                                     AS checks_evaluated,
    coalesce(q.checks_passed, 0)                                        AS checks_passed,
    coalesce(q.error_checks_failed, 0)                                  AS error_checks_failed,
    coalesce(q.warning_checks_failed, 0)                                AS warning_checks_failed
FROM oss_health.pipeline_runs r
LEFT JOIN q USING (run_id);
-- @@
CREATE OR REPLACE VIEW analytics.fact_data_quality_check AS
SELECT
    c.check_result_id,
    c.run_id,
    r.started_at AT TIME ZONE 'UTC'           AS run_started_at_utc,
    (r.started_at AT TIME ZONE 'UTC')::date   AS run_date,
    c.check_name,
    c.check_stage,
    c.entity,
    c.repository_full_name,
    c.severity,
    c.status,
    c.records_evaluated,
    c.records_failed,
    c.observed_value,
    c.threshold_value,
    c.details,
    c.checked_at AT TIME ZONE 'UTC'           AS checked_at_utc
FROM oss_health.data_quality_checks c
JOIN oss_health.pipeline_runs r USING (run_id);
