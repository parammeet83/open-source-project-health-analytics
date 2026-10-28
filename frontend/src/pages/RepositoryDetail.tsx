import { Link, useParams } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { ActivityWeek, IssueMetrics, PullRequestMetrics, Repository, SnapshotPoint } from "../api/types";
import { COLORS, HorizontalBarChart, TrendLineChart, WeeklyActivityChart } from "../components/charts";
import { Async, Card, Empty, ErrorState, Kpi, Loading, PageHeader } from "../components/ui";
import {
  formatDate,
  formatDateTime,
  formatDays,
  formatDaysFromHours,
  formatNumber,
  formatPercent,
  formatSigned,
} from "../format";

export default function RepositoryDetail() {
  const { repositoryId } = useParams();
  const id = /^\d{1,15}$/.test(repositoryId ?? "") ? Number(repositoryId) : null;
  const repo = useApi<Repository>(id ? `/api/v1/repositories/${id}` : null);
  const snapshots = useApi<{ items: SnapshotPoint[] }>(id ? "/api/v1/analytics/stars-forks" : null, { repository_id: id });
  const activity = useApi<{ items: ActivityWeek[] }>(id ? "/api/v1/analytics/activity" : null, { repository_id: id, weeks: 26 });
  const issues = useApi<IssueMetrics>(id ? "/api/v1/analytics/issues" : null, { repository_id: id, days: 90 });
  const prs = useApi<PullRequestMetrics>(id ? "/api/v1/analytics/pull-requests" : null, { repository_id: id, days: 90 });

  if (id === null || repo.error?.status === 404) {
    return (
      <>
        <PageHeader title="Repository not found" />
        <Empty>
          This repository is not tracked. <Link to="/repositories">Browse tracked repositories</Link>.
        </Empty>
      </>
    );
  }
  if (repo.loading && !repo.data) return <Loading />;
  if (repo.error || !repo.data) return <ErrorState error={repo.error} onRetry={repo.reload} />;
  const r = repo.data;

  return (
    <>
      <PageHeader
        title={r.full_name}
        subtitle={r.description ?? undefined}
        actions={
          <a className="button button-ghost" href={r.html_url} target="_blank" rel="noopener noreferrer">
            View on GitHub ↗
          </a>
        }
      />
      <div className="kpi-grid">
        <Kpi label="Stars" value={formatNumber(r.stars_latest)} hint={`Snapshot ${formatDate(r.latest_snapshot_date)}`} />
        <Kpi label="Forks" value={formatNumber(r.forks_latest)} />
        <Kpi label="Watchers" value={formatNumber(r.watchers_latest)} />
        <Kpi label="Open issues" value={formatNumber(r.open_issues)} />
        <Kpi label="Open PRs" value={formatNumber(r.open_prs)} hint={`${formatNumber(r.open_draft_prs)} drafts`} />
        <Kpi
          label="PR merge rate (90 days)"
          value={formatPercent(prs.data?.merge_rate)}
          hint={`Median ${formatDaysFromHours(prs.data?.median_hours_to_merge)} to merge`}
        />
      </div>

      <Card title="Coverage" subtitle="What this dashboard knows about this repository.">
        <dl className="meta-grid">
          <div><dt>Complete issue/PR events since</dt><dd>{formatDate(r.coverage_start_date)}</dd></div>
          <div><dt>Star/fork history since</dt><dd>{formatDate(r.first_snapshot_date)} ({formatNumber(r.snapshot_count)} snapshots)</dd></div>
          <div><dt>Issues synced up to</dt><dd>{formatDateTime(r.issues_watermark_utc)}</dd></div>
          <div><dt>Pull requests synced up to</dt><dd>{formatDateTime(r.prs_watermark_utc)}</dd></div>
          <div><dt>Language · License</dt><dd>{r.primary_language ?? "—"} · {r.license_spdx_id ?? "—"}</dd></div>
          <div><dt>Last push</dt><dd>{formatDateTime(r.pushed_at_utc)}</dd></div>
        </dl>
        {r.coverage_start_date === null ? (
          <p className="banner banner-warn small">The first complete backfill has not finished; trends are hidden until it does.</p>
        ) : null}
      </Card>

      <div className="grid-2">
        <Card title="Stars and forks over time" subtitle="Real daily snapshots only — history starts with the first pipeline run.">
          <Async state={snapshots} empty={(s) => (s.items.length === 0 ? "No snapshots yet." : null)}>
            {(s) =>
              s.items.length < 2 ? (
                <div>
                  <p className="muted">
                    Only one snapshot so far ({formatDate(s.items[0]?.snapshot_date)}: {formatNumber(s.items[0]?.stargazers_count)} stars,{" "}
                    {formatNumber(s.items[0]?.forks_count)} forks). The trend line appears once more daily snapshots are collected.
                  </p>
                </div>
              ) : (
                <>
                  <TrendLineChart
                    data={s.items as unknown as Record<string, unknown>[]}
                    xKey="snapshot_date"
                    series={[{ key: "stargazers_count", label: "Stars", color: COLORS.blue }]}
                  />
                  <TrendLineChart
                    data={s.items as unknown as Record<string, unknown>[]}
                    xKey="snapshot_date"
                    height={200}
                    series={[{ key: "forks_count", label: "Forks", color: COLORS.teal }]}
                  />
                  <p className="small muted">
                    Change since previous snapshot: {formatSigned(s.items[s.items.length - 1]?.stars_delta)} stars
                  </p>
                </>
              )
            }
          </Async>
        </Card>
        <Card title="Weekly activity" subtitle="Within the complete coverage window.">
          <Async state={activity}>
            {(a) => (
              <WeeklyActivityChart
                weeks={a.items}
                series={[
                  { key: "issues_opened", label: "Issues opened", color: COLORS.amber },
                  { key: "issues_closed", label: "Issues closed", color: COLORS.teal },
                  { key: "prs_merged", label: "PRs merged", color: COLORS.violet },
                ]}
              />
            )}
          </Async>
        </Card>
      </div>

      <div className="grid-2">
        <Card title="Open issue backlog by age">
          <Async state={issues} empty={(i) => (i.backlog_age.length === 0 ? "No open issues." : null)}>
            {(i) => (
              <>
                <HorizontalBarChart data={i.backlog_age} labelKey="bucket" valueKey="open_issues" valueLabel="Open issues" color={COLORS.amber} />
                <p className="small muted">Median open-issue age: {formatDays(i.totals.median_open_age_days)}</p>
              </>
            )}
          </Async>
        </Card>
        <Card title="Oldest open issues">
          <Async state={issues} empty={(i) => (i.oldest_open.length === 0 ? "No open issues." : null)}>
            {(i) => (
              <ol className="issue-list">
                {i.oldest_open.map((o) => (
                  <li key={o.issue_number}>
                    <a href={o.html_url} target="_blank" rel="noopener noreferrer">
                      #{o.issue_number} {o.title}
                    </a>
                    <span className="small muted"> · {formatDays(o.open_age_days)} open · {o.comments_count} comments</span>
                  </li>
                ))}
              </ol>
            )}
          </Async>
        </Card>
      </div>
    </>
  );
}
