import { useState } from "react";
import { useApi } from "../api/useApi";
import type { PipelineRun, QualityLatest, RunStatistics } from "../api/types";
import { FreshnessBanner } from "../components/FreshnessBanner";
import { Async, Card, CheckBadge, Kpi, PageHeader, StatusBadge } from "../components/ui";
import { formatDateTime, formatDuration, formatNumber, formatPercent, humanize } from "../format";

export default function Quality() {
  const quality = useApi<QualityLatest>("/api/v1/quality/latest");
  const runs = useApi<{ items: PipelineRun[]; statistics: RunStatistics }>("/api/v1/runs", { limit: 30 });
  const [onlyProblems, setOnlyProblems] = useState(true);

  return (
    <>
      <PageHeader
        title="Data Quality & Pipeline Runs"
        subtitle="Every ingestion run is recorded with its status and data-quality checks. Partial or failed runs are never shown as healthy."
      />
      <Async state={quality}>{(q) => <FreshnessBanner freshness={q.freshness} />}</Async>
      <Async state={runs}>
        {(r) => (
          <div className="kpi-grid">
            <Kpi
              label="Success rate (30 days)"
              value={formatPercent(r.statistics.success_rate)}
              tone={r.statistics.success_rate === null ? undefined : r.statistics.success_rate >= 0.9 ? "good" : "warn"}
            />
            <Kpi label="Successful runs" value={formatNumber(r.statistics.runs_succeeded)} />
            <Kpi label="Partial runs" value={formatNumber(r.statistics.runs_partial)} tone={r.statistics.runs_partial ? "warn" : undefined} />
            <Kpi label="Failed runs" value={formatNumber(r.statistics.runs_failed)} tone={r.statistics.runs_failed ? "bad" : undefined} />
          </div>
        )}
      </Async>

      <Card title="Latest run: quality checks">
        <Async state={quality} empty={(q) => (q.run === null ? "No pipeline run has been recorded yet." : null)}>
          {(q) => {
            const problems = q.checks.filter((c) => c.status === "failed");
            const shown = onlyProblems ? problems : q.checks;
            return (
              <>
                <p className="small">
                  Run <code>{q.run?.run_id.slice(0, 8)}</code> · <StatusBadge status={q.run?.status ?? "failed"} /> · started{" "}
                  {formatDateTime(q.run?.started_at_utc)} · {q.checks.length} checks, {problems.length} failed
                </p>
                {q.run?.error_message ? <p className="banner banner-bad small">{q.run.error_message}</p> : null}
                <label className="toggle">
                  <input type="checkbox" checked={onlyProblems} onChange={(e) => setOnlyProblems(e.target.checked)} />
                  Show only failed checks
                </label>
                {shown.length === 0 ? (
                  <p className="muted">No failed checks in the latest run.</p>
                ) : (
                  <div className="table-wrap">
                    <table className="table">
                      <thead>
                        <tr>
                          <th scope="col">Check</th>
                          <th scope="col">Scope</th>
                          <th scope="col">Result</th>
                          <th scope="col" className="num">Failed / evaluated</th>
                          <th scope="col" className="num">Observed</th>
                        </tr>
                      </thead>
                      <tbody>
                        {shown.map((c) => (
                          <tr key={`${c.check_name}-${c.entity}-${c.repository_full_name ?? "run"}`}>
                            <th scope="row">{humanize(c.check_name)}</th>
                            <td>
                              {humanize(c.entity)}
                              {c.repository_full_name ? <span className="muted"> · {c.repository_full_name}</span> : null}
                            </td>
                            <td><CheckBadge status={c.status} severity={c.severity} /></td>
                            <td className="num">
                              {formatNumber(c.records_failed)} / {formatNumber(c.records_evaluated)}
                            </td>
                            <td className="num">
                              {c.observed_value === null ? "—" : formatNumber(c.observed_value)}
                              {c.threshold_value !== null ? <span className="muted"> (limit {c.threshold_value})</span> : null}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            );
          }}
        </Async>
      </Card>

      <Card title="Run history" subtitle="Most recent 30 runs.">
        <Async state={runs} empty={(r) => (r.items.length === 0 ? "No runs recorded yet." : null)}>
          {(r) => (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th scope="col">Started</th>
                    <th scope="col">Status</th>
                    <th scope="col">Trigger</th>
                    <th scope="col" className="num">Repos ok / failed</th>
                    <th scope="col" className="num">Extracted</th>
                    <th scope="col" className="num">Rejected</th>
                    <th scope="col" className="num">Failed checks</th>
                    <th scope="col" className="num">Duration</th>
                  </tr>
                </thead>
                <tbody>
                  {r.items.map((run) => (
                    <tr key={run.run_id}>
                      <th scope="row">{formatDateTime(run.started_at_utc)}</th>
                      <td><StatusBadge status={run.status} /></td>
                      <td>{run.trigger_type}{run.is_full_refresh ? " (full)" : ""}</td>
                      <td className="num">{run.repositories_succeeded} / {run.repositories_failed}</td>
                      <td className="num">{formatNumber(run.records_extracted)}</td>
                      <td className="num">{formatNumber(run.records_rejected)}</td>
                      <td className="num">{run.error_checks_failed} error · {run.warning_checks_failed} warn</td>
                      <td className="num">{formatDuration(run.duration_seconds)}</td>
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
