import type { Freshness } from "../api/types";
import { formatDateTime, formatRelativeHours } from "../format";
import { StatusBadge } from "./ui";

/**
 * Data freshness and pipeline health. Never green unless the latest finished run succeeded
 * and the last success is within the staleness threshold.
 */
export function FreshnessBanner({ freshness }: { freshness: Freshness }) {
  const latest = freshness.latest_run;
  const lastOk = freshness.last_successful_run;
  let tone: "good" | "warn" | "bad";
  let headline: string;
  switch (freshness.status) {
    case "fresh":
      tone = "good";
      headline = "Data is up to date";
      break;
    case "degraded":
      tone = "warn";
      headline =
        latest?.status === "failed"
          ? "The latest ingestion run failed — showing data from the last successful run"
          : "The latest ingestion run was only partially successful — some data may be missing";
      break;
    case "stale":
      tone = "bad";
      headline = `Data is stale — no successful ingestion in over ${freshness.stale_after_hours} hours`;
      break;
    default:
      tone = "bad";
      headline = "No successful ingestion run yet — metrics are unavailable";
  }
  return (
    <div className={`banner banner-${tone}`} role={tone === "good" ? "status" : "alert"}>
      <div className="banner-main">
        <strong>{headline}</strong>
        <span className="small">
          Last successful ingestion:{" "}
          {lastOk ? (
            <>
              {formatDateTime(lastOk.finished_at_utc)} ({formatRelativeHours(freshness.hours_since_last_success)})
            </>
          ) : (
            "none"
          )}
        </span>
      </div>
      {latest ? (
        <div className="banner-side small">
          Latest run: <StatusBadge status={latest.status} />
          {latest.repositories_failed > 0 ? (
            <span> · {latest.repositories_failed} repositor{latest.repositories_failed === 1 ? "y" : "ies"} failed</span>
          ) : null}
          {latest.error_checks_failed > 0 ? <span> · {latest.error_checks_failed} failed checks</span> : null}
        </div>
      ) : null}
    </div>
  );
}
