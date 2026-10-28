/** Display formatting. Missing values always render as an em dash, never as zero. */

export const MISSING = "—";

const integer = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const oneDecimal = new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, minimumFractionDigits: 1 });

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatNumber(value: number | null | undefined): string {
  return isNumber(value) ? integer.format(value) : MISSING;
}

export function formatCompact(value: number | null | undefined): string {
  return isNumber(value) ? compact.format(value) : MISSING;
}

export function formatPercent(value: number | null | undefined): string {
  return isNumber(value) ? `${oneDecimal.format(value * 100)}%` : MISSING;
}

/** Durations given in hours: shown in hours below 48 h (so 0.8 h is not "0.0 days"), else days. */
export function formatDaysFromHours(hours: number | null | undefined): string {
  if (!isNumber(hours)) return MISSING;
  if (hours < 48) return `${oneDecimal.format(hours)} hours`;
  return `${oneDecimal.format(hours / 24)} days`;
}

export function formatDays(days: number | null | undefined): string {
  if (!isNumber(days)) return MISSING;
  return `${integer.format(days)} ${Math.round(days) === 1 ? "day" : "days"}`;
}

export function formatSigned(value: number | null | undefined): string {
  if (!isNumber(value)) return MISSING;
  return `${value > 0 ? "+" : ""}${integer.format(value)}`;
}

/** API timestamps are UTC (ISO 8601 with Z); dates are YYYY-MM-DD in UTC. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return MISSING;
  const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : value);
  if (Number.isNaN(date.getTime())) return MISSING;
  return date.toLocaleDateString("en-US", { year: "numeric", month: "short", day: "numeric", timeZone: "UTC" });
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return MISSING;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return MISSING;
  return `${date.toLocaleString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    timeZone: "UTC",
    hour12: false,
  })} UTC`;
}

export function formatShortDate(value: string): string {
  const date = new Date(`${value}T00:00:00Z`);
  return date.toLocaleDateString("en-US", { month: "short", day: "numeric", timeZone: "UTC" });
}

export function formatRelativeHours(hours: number | null | undefined): string {
  if (!isNumber(hours)) return MISSING;
  if (hours < 1) return "less than an hour ago";
  if (hours < 48) return `${Math.round(hours)} hours ago`;
  return `${Math.round(hours / 24)} days ago`;
}

export function formatDuration(seconds: number | null | undefined): string {
  if (!isNumber(seconds)) return MISSING;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  return `${oneDecimal.format(seconds / 60)} min`;
}

export function humanize(value: string): string {
  return value.replace(/_/g, " ");
}
