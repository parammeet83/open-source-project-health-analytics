import { vi } from "vitest";
import type { Freshness, QualityLatest, Repository, RunRef, Summary } from "../api/types";

export const okRun: RunRef = {
  run_id: "11111111-2222-3333-4444-555555555555",
  status: "success",
  started_at_utc: "2026-10-28T02:17:00Z",
  finished_at_utc: "2026-10-28T02:25:00Z",
  repositories_requested: 2,
  repositories_succeeded: 2,
  repositories_failed: 0,
  records_rejected: 0,
  error_checks_failed: 0,
  warning_checks_failed: 0,
  error_type: null,
  error_message: null,
};

export const freshFreshness: Freshness = {
  status: "fresh",
  hours_since_last_success: 3,
  stale_after_hours: 36,
  latest_run: okRun,
  last_successful_run: okRun,
};

export const repo: Repository = {
  repository_id: 101,
  full_name: "acme/alpha",
  owner_login: "acme",
  repo_name: "alpha",
  description: "Alpha project",
  html_url: "https://github.com/acme/alpha",
  primary_language: "Python",
  license_spdx_id: "MIT",
  topics: [],
  is_fork: false,
  is_archived: false,
  created_at_utc: "2015-01-01T00:00:00Z",
  pushed_at_utc: "2026-10-27T00:00:00Z",
  metadata_refreshed_at_utc: "2026-10-28T02:20:00Z",
  coverage_start_date: "2026-07-31",
  issues_watermark_utc: "2026-10-28T01:00:00Z",
  prs_watermark_utc: "2026-10-28T01:00:00Z",
  first_snapshot_date: "2026-10-28",
  latest_snapshot_date: "2026-10-28",
  snapshot_count: 1,
  stars_latest: 54321,
  forks_latest: 1200,
  watchers_latest: 300,
  open_issues: 157,
  closed_issues_in_coverage: 40,
  open_prs: 87,
  open_draft_prs: 5,
  merged_prs_in_coverage: 30,
  closed_unmerged_prs_in_coverage: 10,
};

export const summary: Summary = {
  repositories: {
    repository_count: 1,
    repositories_with_snapshot: 1,
    stars_total: 54321,
    forks_total: 1200,
    repositories_with_issue_data: 1,
    open_issues_total: 157,
    repositories_with_pr_data: 1,
    open_prs_total: 87,
    earliest_coverage_start: "2026-07-31",
    latest_snapshot_date: "2026-10-28",
    first_snapshot_date: "2026-10-28",
  },
  pull_requests: {
    open_prs: 87,
    open_draft_prs: 5,
    merged_in_window: 30,
    closed_unmerged_in_window: 10,
    opened_in_window: 50,
    median_hours_to_merge: 48,
    merge_rate: 0.75,
    window_days: 90,
  },
  issues: { issues_closed: 40, issues_opened: 60, median_open_issue_age_days: 120 },
  window_days: 90,
  freshness: freshFreshness,
  runs: { runs_finished: 4, runs_succeeded: 4, runs_partial: 0, runs_failed: 0, runs_running: 0, success_rate: 1, window_days: 30 },
};

export const quality: QualityLatest = {
  run: okRun,
  freshness: freshFreshness,
  checks: [
    {
      check_name: "issue_pr_number_overlap", check_stage: "post_load", entity: "run", repository_full_name: null,
      severity: "error", status: "passed", records_evaluated: null, records_failed: 0, observed_value: null,
      threshold_value: null, details: {}, checked_at_utc: "2026-10-28T02:24:00Z",
    },
  ],
};

export type Routes = Record<string, unknown | ((url: URL) => unknown)>;

export const defaultRoutes: Routes = {
  "/api/v1/summary": summary,
  "/api/v1/repositories": { items: [repo], total: 1, limit: 100, offset: 0 },
  "/api/v1/repositories/101": repo,
  "/api/v1/analytics/activity": {
    weeks: 13,
    items: [
      { week_start_date: "2026-10-19", week_end_date: "2026-10-25", issues_opened: 5, issues_closed: 3, prs_opened: 4, prs_merged: 2, prs_closed_unmerged: 1, is_complete_week: true, repositories_included: 1 },
      { week_start_date: "2026-10-26", week_end_date: "2026-11-01", issues_opened: 1, issues_closed: 0, prs_opened: 1, prs_merged: 0, prs_closed_unmerged: 0, is_complete_week: false, repositories_included: 1 },
    ],
  },
  "/api/v1/analytics/stars-forks": { start: "2025-10-28", end: "2026-10-28", items: [
    { repository_id: 101, full_name: "acme/alpha", snapshot_date: "2026-10-28", stargazers_count: 54321, forks_count: 1200, subscribers_count: 300, stars_delta: null, forks_delta: null, previous_snapshot_date: null },
  ] },
  "/api/v1/analytics/issues": {
    totals: { open_issues: 157, closed_issues_in_coverage: 40, closed_in_window: 40, opened_in_window: 60, median_hours_to_close: 72, median_open_age_days: 120, open_issues_by_bots: 2 },
    backlog_age: [{ bucket: "0-7 days", bucket_order: 1, open_issues: 10 }, { bucket: ">365 days", bucket_order: 6, open_issues: 50 }],
    oldest_open: [{ issue_number: 6, title: "Ancient bug", html_url: "https://github.com/acme/alpha/issues/6", open_age_days: 900, comments_count: 4, full_name: "acme/alpha" }],
    window_days: 90,
  },
  "/api/v1/analytics/pull-requests": summary.pull_requests,
  "/api/v1/runs": { items: [{ ...okRun, trigger_type: "scheduled", is_full_refresh: false, run_date: "2026-10-28", duration_seconds: 480, exit_code: 0, records_extracted: 346, records_inserted: 0, records_updated: 3, api_requests_made: 12, rejection_rate: 0, checks_evaluated: 25, checks_passed: 25 }], statistics: summary.runs },
  "/api/v1/quality/latest": quality,
};

/** Install a fetch mock that serves JSON by pathname. Use an Error instance to simulate a status. */
export function mockApi(overrides: Routes = {}) {
  const routes = { ...defaultRoutes, ...overrides };
  const calls: URL[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = new URL(String(input));
    calls.push(url);
    if (init?.method && init.method !== "GET") throw new Error("non-GET request issued");
    const entry = routes[url.pathname];
    const body = typeof entry === "function" ? (entry as (u: URL) => unknown)(url) : entry;
    if (body === undefined) return new Response(JSON.stringify({ error: "not_found" }), { status: 404 });
    if (body instanceof Response) return body;
    return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
  });
  vi.stubGlobal("fetch", fetchMock);
  return { fetchMock, calls };
}
