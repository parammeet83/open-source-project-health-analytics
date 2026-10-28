# Power BI Specification (optional, supplementary)

**Status:** Optional. The public React dashboard is the primary product, and visitors never need
Power BI. **No `.pbix` file has been created or verified.** This document is a build guide.
**Related:** [DB Schema §5](Backend_DB_Schema.md#5-analytics-view-contract-8-views) · [PRD §7](PRD.md#7-metric-definitions-authoritative)

## 1. Connecting (Power BI Desktop, Import mode)

1. **Get data → PostgreSQL database.** Server: the Neon host (`ep-…aws-ap-southeast-1…neon.tech`).
   Database: `oss_health`. Mode: **Import**.
2. **Credentials:** *Database* authentication with **`oss_health_reader`** only. It can read only
   the `analytics` views, and its sessions are read-only. Never use the owner or `oss_health_app`.
   Neon requires TLS; keep encryption enabled. If the bundled driver rejects the certificate, install
   the current Npgsql driver recommended by Microsoft.
3. Select only `analytics.*` views (table list below). Do not import `oss_health.*`. The reader
   cannot see those tables anyway.
4. **Refresh** manually after the daily ingestion (02:17 UTC). Scheduled refresh in Power BI Service
   would need a gateway or a cloud connection and is not part of this project.
5. Do not save credentials in shared files. A `.pbix` is git-ignored by this repository.

## 2. Model

| Model table | View | Type |
|-------------|------|------|
| Repository | `analytics.dim_repository` | dimension |
| Date | `analytics.dim_date` | dimension (mark as date table) |
| Snapshot | `analytics.fact_repository_snapshot` | periodic snapshot (semi-additive) |
| Issue | `analytics.fact_issue` | fact |
| PullRequest | `analytics.fact_pull_request` | fact |
| WeeklyActivity | `analytics.agg_weekly_activity` | aggregate fact |
| PipelineRun | `analytics.fact_pipeline_run` | fact |
| QualityCheck | `analytics.fact_data_quality_check` | fact |

**Relationships** (single direction, dimension → fact):
- `Repository[repository_id]` 1:* to Snapshot, Issue, PullRequest and WeeklyActivity.
- `Date[date]` 1:* to `Snapshot[snapshot_date]`, `Issue[created_date]` (active),
  `Issue[closed_date]` (inactive), `PullRequest[created_date]` (active),
  `PullRequest[merged_date]` and `[closed_date]` (inactive), `WeeklyActivity[week_start_date]`,
  and `PipelineRun[run_date]`.
- `PipelineRun[run_id]` 1:* `QualityCheck[run_id]`.

## 3. Measures (DAX)

```DAX
Stars (Latest) = SUMX ( VALUES ( Repository[repository_id] ),
    CALCULATE ( LASTNONBLANKVALUE ( 'Date'[date], SUM ( Snapshot[stargazers_count] ) ) ) )
Forks (Latest) = SUMX ( VALUES ( Repository[repository_id] ),
    CALCULATE ( LASTNONBLANKVALUE ( 'Date'[date], SUM ( Snapshot[forks_count] ) ) ) )
Open Issues = CALCULATE ( COUNTROWS ( Issue ), Issue[is_open] = TRUE (), REMOVEFILTERS ( 'Date' ) )
Issues Closed = CALCULATE ( COUNTROWS ( Issue ), USERELATIONSHIP ( Issue[closed_date], 'Date'[date] ),
    NOT ISBLANK ( Issue[closed_date] ) )
PRs Merged = CALCULATE ( COUNTROWS ( PullRequest ), USERELATIONSHIP ( PullRequest[merged_date], 'Date'[date] ),
    PullRequest[outcome] = "merged" )
PRs Closed Unmerged = CALCULATE ( COUNTROWS ( PullRequest ), USERELATIONSHIP ( PullRequest[closed_date], 'Date'[date] ),
    PullRequest[outcome] = "closed_unmerged" )
PR Merge Rate = DIVIDE ( [PRs Merged], [PRs Merged] + [PRs Closed Unmerged] )   -- BLANK when nothing closed
Median Hours to Merge = CALCULATE ( MEDIAN ( PullRequest[hours_to_merge] ),
    USERELATIONSHIP ( PullRequest[merged_date], 'Date'[date] ), PullRequest[outcome] = "merged" )
Weekly Issues Closed = SUM ( WeeklyActivity[issues_closed] )
Weekly PRs Merged = SUM ( WeeklyActivity[prs_merged] )
Pipeline Success Rate = DIVIDE ( CALCULATE ( COUNTROWS ( PipelineRun ), PipelineRun[status] = "success" ),
    CALCULATE ( COUNTROWS ( PipelineRun ), PipelineRun[status] <> "running" ) )
Error Checks Failed = CALCULATE ( COUNTROWS ( QualityCheck ), QualityCheck[status] = "failed",
    QualityCheck[severity] = "error" )
```

Definitions match [PRD §7](PRD.md#7-metric-definitions-authoritative) and the public API. Apply
`WeeklyActivity[is_within_coverage] = TRUE` to every trend visual. Hide incomplete weeks with
`is_complete_week`.

## 4. Suggested pages

1. **Overview:** KPI cards (stars, open issues, merge rate, median hours to merge, pipeline success
   rate), stars over time, and a repository table.
2. **Growth:** stars and forks lines from real snapshots, and daily deltas.
3. **Issues:** backlog age buckets (sorted by `backlog_age_bucket_order`), weekly opened vs closed,
   and the oldest open issues.
4. **Pull requests:** merge rate, median time to merge, weekly merged vs closed-unmerged, and
   outcomes by repository.
5. **Pipeline & quality:** runs by status (success green, partial amber, failed red), failed checks
   of the latest run, and rejection rate.

**Filters:** repository, date range, and an exclude-bots toggle (`is_bot_author`).

## 5. Reconciliation

Before relying on the report, compare a few values with the public API (`/api/v1/summary`,
`/api/v1/analytics/pull-requests?days=30`). Both read the same views, so the values should match exactly.
