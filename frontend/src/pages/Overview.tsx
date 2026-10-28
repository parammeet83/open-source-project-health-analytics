import { Link } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { ActivityWeek, RepositoryList, Summary } from "../api/types";
import { COLORS, HorizontalBarChart, WeeklyActivityChart } from "../components/charts";
import { FreshnessBanner } from "../components/FreshnessBanner";
import { Async, Card, Kpi, PageHeader } from "../components/ui";
import { formatCompact, formatDate, formatDaysFromHours, formatDays, formatNumber, formatPercent } from "../format";

export default function Overview() {
  const summary = useApi<Summary>("/api/v1/summary", { days: 90 });
  const repos = useApi<RepositoryList>("/api/v1/repositories", { sort: "stars", order: "desc", limit: 100 });
  const activity = useApi<{ items: ActivityWeek[] }>("/api/v1/analytics/activity", { weeks: 13 });

  return (
    <>
      <PageHeader
        title="Open-Source Project Health"
        subtitle="Growth, issue backlog and pull-request throughput for selected GitHub projects, refreshed daily by an automated pipeline."
      />
      <Async state={summary}>
        {(s) => (
          <>
            <FreshnessBanner freshness={s.freshness} />
            <div className="kpi-grid">
              <Kpi label="Repositories tracked" value={formatNumber(s.repositories.repository_count)} />
              <Kpi
                label="Total stars"
                value={formatCompact(s.repositories.stars_total)}
                hint={s.repositories.latest_snapshot_date ? `Snapshot ${formatDate(s.repositories.latest_snapshot_date)}` : "No snapshot yet"}
              />
              <Kpi label="Total forks" value={formatCompact(s.repositories.forks_total)} />
              <Kpi
                label="Open issues"
                value={formatNumber(s.repositories.open_issues_total)}
                hint={`${s.repositories.repositories_with_issue_data} of ${s.repositories.repository_count} repos with complete data`}
              />
              <Kpi label="Open pull requests" value={formatNumber(s.repositories.open_prs_total)} />
              <Kpi
                label="PR merge rate (90 days)"
                value={formatPercent(s.pull_requests.merge_rate)}
                hint={`${formatNumber(s.pull_requests.merged_in_window)} merged · ${formatNumber(s.pull_requests.closed_unmerged_in_window)} closed unmerged`}
              />
              <Kpi label="Median time to merge (90 days)" value={formatDaysFromHours(s.pull_requests.median_hours_to_merge)} />
              <Kpi label="Median open-issue age" value={formatDays(s.issues.median_open_issue_age_days)} />
              <Kpi
                label="Pipeline success rate (30 days)"
                value={formatPercent(s.runs.success_rate)}
                hint={`${s.runs.runs_succeeded} ok · ${s.runs.runs_partial} partial · ${s.runs.runs_failed} failed`}
                tone={s.runs.success_rate === null ? undefined : s.runs.success_rate >= 0.9 ? "good" : "warn"}
              />
            </div>
          </>
        )}
      </Async>

      <div className="grid-2">
        <Card title="Weekly activity, all repositories" subtitle="Issues opened/closed and PRs merged — only weeks covered for every repository.">
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
        <Card title="Stars by repository" subtitle="Latest real snapshot per repository.">
          <Async state={repos} empty={(r) => (r.items.length === 0 ? "No repositories have been ingested yet." : null)}>
            {(r) => {
              const data = r.items.filter((x) => x.stars_latest !== null).map((x) => ({ name: x.full_name, stars: x.stars_latest }));
              return data.length ? (
                <HorizontalBarChart data={data} labelKey="name" valueKey="stars" valueLabel="Stars" />
              ) : (
                <p className="muted">No snapshots yet.</p>
              );
            }}
          </Async>
        </Card>
      </div>

      <Card title="Repositories at a glance">
        <Async state={repos} empty={(r) => (r.items.length === 0 ? "No repositories have been ingested yet." : null)}>
          {(r) => (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Repository</th>
                    <th scope="col" className="num">Stars</th>
                    <th scope="col" className="num">Forks</th>
                    <th scope="col" className="num">Open issues</th>
                    <th scope="col" className="num">Open PRs</th>
                    <th scope="col">Coverage since</th>
                  </tr>
                </thead>
                <tbody>
                  {r.items.map((x) => (
                    <tr key={x.repository_id}>
                      <th scope="row">
                        <Link to={`/repositories/${x.repository_id}`}>{x.full_name}</Link>
                      </th>
                      <td className="num">{formatNumber(x.stars_latest)}</td>
                      <td className="num">{formatNumber(x.forks_latest)}</td>
                      <td className="num">{formatNumber(x.open_issues)}</td>
                      <td className="num">{formatNumber(x.open_prs)}</td>
                      <td>{formatDate(x.coverage_start_date)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Async>
      </Card>
    </>
  );
}
