import { useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { ActivityWeek, IssueMetrics } from "../api/types";
import { COLORS, HorizontalBarChart, WeeklyActivityChart } from "../components/charts";
import { RepositoryFilter, WINDOW_OPTIONS, WindowFilter } from "../components/RepositoryFilter";
import { Async, Card, Kpi, PageHeader } from "../components/ui";
import { formatDays, formatDaysFromHours, formatNumber } from "../format";

export function useFilters() {
  const [params, setParams] = useSearchParams();
  const repoParam = params.get("repo");
  const repositoryId = repoParam && /^\d{1,15}$/.test(repoParam) ? Number(repoParam) : null;
  const daysParam = Number(params.get("days"));
  const days = (WINDOW_OPTIONS as readonly number[]).includes(daysParam) ? daysParam : 90;
  const set = (key: string, value: string | null) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  return {
    repositoryId,
    days,
    setRepository: (id: number | null) => set("repo", id ? String(id) : null),
    setDays: (d: number) => set("days", String(d)),
  };
}

export default function Issues() {
  const { repositoryId, days, setRepository, setDays } = useFilters();
  const metrics = useApi<IssueMetrics>("/api/v1/analytics/issues", { repository_id: repositoryId, days });
  const activity = useApi<{ items: ActivityWeek[] }>("/api/v1/analytics/activity", {
    repository_id: repositoryId,
    weeks: Math.ceil(days / 7),
  });

  return (
    <>
      <PageHeader title="Issues & Backlog" subtitle="Open versus closed issues, backlog age and closure trends. Pull requests are excluded." />
      <div className="filters">
        <RepositoryFilter value={repositoryId} onChange={setRepository} />
        <WindowFilter value={days} onChange={setDays} />
      </div>
      <Async state={metrics}>
        {(m) => (
          <div className="kpi-grid">
            <Kpi label="Open issues" value={formatNumber(m.totals.open_issues)} hint={`${formatNumber(m.totals.open_issues_by_bots)} opened by bots`} />
            <Kpi label={`Opened (last ${days} days)`} value={formatNumber(m.totals.opened_in_window)} />
            <Kpi label={`Closed (last ${days} days)`} value={formatNumber(m.totals.closed_in_window)} />
            <Kpi label="Median time to close" value={formatDaysFromHours(m.totals.median_hours_to_close)} hint={`Issues closed in the last ${days} days`} />
            <Kpi label="Median open-issue age" value={formatDays(m.totals.median_open_age_days)} />
          </div>
        )}
      </Async>
      <div className="grid-2">
        <Card title="Weekly issues opened vs closed" subtitle="Complete weeks within each repository's coverage window.">
          <Async state={activity}>
            {(a) => (
              <WeeklyActivityChart
                weeks={a.items}
                series={[
                  { key: "issues_opened", label: "Opened", color: COLORS.amber },
                  { key: "issues_closed", label: "Closed", color: COLORS.teal },
                ]}
              />
            )}
          </Async>
        </Card>
        <Card title="Open backlog by age" subtitle="Age = days since the issue was opened (UTC).">
          <Async state={metrics} empty={(m) => (m.backlog_age.length === 0 ? "No open issues." : null)}>
            {(m) => <HorizontalBarChart data={m.backlog_age} labelKey="bucket" valueKey="open_issues" valueLabel="Open issues" color={COLORS.amber} />}
          </Async>
        </Card>
      </div>
      <Card title="Oldest open issues">
        <Async state={metrics} empty={(m) => (m.oldest_open.length === 0 ? "No open issues." : null)}>
          {(m) => (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Issue</th>
                    <th scope="col">Repository</th>
                    <th scope="col" className="num">Age</th>
                    <th scope="col" className="num">Comments</th>
                  </tr>
                </thead>
                <tbody>
                  {m.oldest_open.map((o) => (
                    <tr key={`${o.full_name}#${o.issue_number}`}>
                      <th scope="row">
                        <a href={o.html_url} target="_blank" rel="noopener noreferrer">
                          #{o.issue_number} {o.title}
                        </a>
                      </th>
                      <td>{o.full_name}</td>
                      <td className="num">{formatDays(o.open_age_days)}</td>
                      <td className="num">{formatNumber(o.comments_count)}</td>
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
