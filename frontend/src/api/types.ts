/** Response contracts of the read-only API (backend/src/oss_health/api). Nullable = no data. */

export type RunStatus = "running" | "success" | "partial_success" | "failed";
export type FreshnessStatus = "fresh" | "degraded" | "stale" | "no_successful_run";

export interface RunRef {
  run_id: string;
  status: RunStatus;
  started_at_utc: string;
  finished_at_utc: string | null;
  repositories_requested: number;
  repositories_succeeded: number;
  repositories_failed: number;
  records_rejected: number;
  error_checks_failed: number;
  warning_checks_failed: number;
  error_type: string | null;
  error_message: string | null;
}

export interface Freshness {
  status: FreshnessStatus;
  hours_since_last_success: number | null;
  stale_after_hours: number;
  latest_run: RunRef | null;
  last_successful_run: RunRef | null;
}

export interface RunStatistics {
  runs_finished: number;
  runs_succeeded: number;
  runs_partial: number;
  runs_failed: number;
  runs_running: number;
  success_rate: number | null;
  window_days: number;
}

export interface PullRequestMetrics {
  open_prs: number;
  open_draft_prs: number;
  merged_in_window: number;
  closed_unmerged_in_window: number;
  opened_in_window: number;
  median_hours_to_merge: number | null;
  merge_rate: number | null;
  window_days: number;
}

export interface Summary {
  repositories: {
    repository_count: number;
    repositories_with_snapshot: number;
    stars_total: number | null;
    forks_total: number | null;
    repositories_with_issue_data: number;
    open_issues_total: number | null;
    repositories_with_pr_data: number;
    open_prs_total: number | null;
    earliest_coverage_start: string | null;
    latest_snapshot_date: string | null;
    first_snapshot_date: string | null;
  };
  pull_requests: PullRequestMetrics;
  issues: {
    issues_closed: number;
    issues_opened: number;
    median_open_issue_age_days: number | null;
  };
  window_days: number;
  freshness: Freshness;
  runs: RunStatistics;
}

export interface Repository {
  repository_id: number;
  full_name: string;
  owner_login: string;
  repo_name: string;
  description: string | null;
  html_url: string;
  primary_language: string | null;
  license_spdx_id: string | null;
  topics: string[];
  is_fork: boolean;
  is_archived: boolean;
  created_at_utc: string;
  pushed_at_utc: string | null;
  metadata_refreshed_at_utc: string;
  coverage_start_date: string | null;
  issues_watermark_utc: string | null;
  prs_watermark_utc: string | null;
  first_snapshot_date: string | null;
  latest_snapshot_date: string | null;
  snapshot_count: number | null;
  stars_latest: number | null;
  forks_latest: number | null;
  watchers_latest: number | null;
  open_issues: number | null;
  closed_issues_in_coverage: number | null;
  open_prs: number | null;
  open_draft_prs: number | null;
  merged_prs_in_coverage: number | null;
  closed_unmerged_prs_in_coverage: number | null;
}

export interface RepositoryList {
  items: Repository[];
  total: number;
  limit: number;
  offset: number;
}

export interface SnapshotPoint {
  repository_id: number;
  full_name: string;
  snapshot_date: string;
  stargazers_count: number;
  forks_count: number;
  subscribers_count: number | null;
  stars_delta: number | null;
  forks_delta: number | null;
  previous_snapshot_date: string | null;
}

export interface ActivityWeek {
  week_start_date: string;
  week_end_date: string;
  issues_opened: number;
  issues_closed: number;
  prs_opened: number;
  prs_merged: number;
  prs_closed_unmerged: number;
  is_complete_week: boolean;
  repositories_included: number;
}

export interface IssueMetrics {
  totals: {
    open_issues: number;
    closed_issues_in_coverage: number;
    closed_in_window: number;
    opened_in_window: number;
    median_hours_to_close: number | null;
    median_open_age_days: number | null;
    open_issues_by_bots: number;
  };
  backlog_age: { bucket: string; bucket_order: number; open_issues: number }[];
  oldest_open: {
    issue_number: number;
    title: string;
    html_url: string;
    open_age_days: number;
    comments_count: number;
    full_name: string;
  }[];
  window_days: number;
}

export interface PipelineRun extends RunRef {
  trigger_type: string;
  is_full_refresh: boolean;
  run_date: string;
  duration_seconds: number | null;
  exit_code: number | null;
  records_extracted: number;
  records_inserted: number;
  records_updated: number;
  api_requests_made: number;
  rejection_rate: number | null;
  checks_evaluated: number;
  checks_passed: number;
}

export interface QualityCheck {
  check_name: string;
  check_stage: string;
  entity: string;
  repository_full_name: string | null;
  severity: "error" | "warning" | "info";
  status: "passed" | "failed" | "skipped";
  records_evaluated: number | null;
  records_failed: number | null;
  observed_value: number | null;
  threshold_value: number | null;
  details: Record<string, unknown>;
  checked_at_utc: string;
}

export interface QualityLatest {
  run: RunRef | null;
  checks: QualityCheck[];
  freshness: Freshness;
}
