import { useApi } from "../api/useApi";
import type { ActivityWeek, PullRequestMetrics } from "../api/types";
import { COLORS, WeeklyActivityChart } from "../components/charts";
import { RepositoryFilter, WindowFilter } from "../components/RepositoryFilter";
import { Async, Card, Kpi, PageHeader } from "../components/ui";
import { formatDaysFromHours, formatNumber, formatPercent } from "../format";
import { useFilters } from "./Issues";

export default function PullRequests() {
  const { repositoryId, days, setRepository, setDays } = useFilters();
  const metrics = useApi<PullRequestMetrics>("/api/v1/analytics/pull-requests", { repository_id: repositoryId, days });
  const activity = useApi<{ items: ActivityWeek[] }>("/api/v1/analytics/activity", {
    repository_id: repositoryId,
    weeks: Math.ceil(days / 7),
  });

  return (
    <>
      <PageHeader
        title="Pull Request Analytics"
        subtitle="Merged is not the same as closed: merge rate = merged ÷ (merged + closed without merging), by close date."
      />
      <div className="filters">
        <RepositoryFilter value={repositoryId} onChange={setRepository} />
        <WindowFilter value={days} onChange={setDays} />
      </div>
      <Async state={metrics}>
        {(m) => (
          <div className="kpi-grid">
            <Kpi
              label={`Merge rate (last ${days} days)`}
              value={formatPercent(m.merge_rate)}
              hint={m.merge_rate === null ? "No pull requests were closed in this window" : undefined}
            />
            <Kpi label="Median time to merge" value={formatDaysFromHours(m.median_hours_to_merge)} hint={`PRs merged in the last ${days} days`} />
            <Kpi label="Merged" value={formatNumber(m.merged_in_window)} />
            <Kpi label="Closed without merge" value={formatNumber(m.closed_unmerged_in_window)} />
            <Kpi label={`Opened (last ${days} days)`} value={formatNumber(m.opened_in_window)} />
            <Kpi label="Open now" value={formatNumber(m.open_prs)} hint={`${formatNumber(m.open_draft_prs)} drafts`} />
          </div>
        )}
      </Async>
      <Card title="Weekly pull-request flow" subtitle="Opened, merged, and closed without merging per week.">
        <Async state={activity}>
          {(a) => (
            <WeeklyActivityChart
              height={320}
              weeks={a.items}
              series={[
                { key: "prs_opened", label: "Opened", color: COLORS.blue },
                { key: "prs_merged", label: "Merged", color: COLORS.violet },
                { key: "prs_closed_unmerged", label: "Closed unmerged", color: COLORS.slate },
              ]}
            />
          )}
        </Async>
      </Card>
    </>
  );
}
