import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import type { ActivityWeek } from "../api/types";
import { formatCompact, formatNumber, formatShortDate } from "../format";
import { Empty } from "./ui";

export const COLORS = {
  blue: "#2563eb",
  teal: "#0d9488",
  amber: "#d97706",
  rose: "#e11d48",
  violet: "#7c3aed",
  slate: "#64748b",
};

export interface Series {
  key: string;
  label: string;
  color: string;
}

const tooltipStyle = { borderRadius: 8, border: "1px solid var(--border)", background: "var(--surface)", color: "var(--text)" };

/** Weekly activity as grouped bars; partial (current) weeks are labelled. */
export function WeeklyActivityChart({ weeks, series, height = 280 }: {
  weeks: ActivityWeek[];
  series: Series[];
  height?: number;
}) {
  if (weeks.length === 0) {
    return <Empty>No weeks within the complete coverage window yet.</Empty>;
  }
  const data = weeks.map((w) => ({
    ...w,
    label: `${formatShortDate(w.week_start_date)}${w.is_complete_week ? "" : "*"}`,
  }));
  const partial = weeks.some((w) => !w.is_complete_week);
  return (
    <figure className="chart" aria-label={`Weekly ${series.map((s) => s.label).join(", ")}`}>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} margin={{ top: 8, right: 8, left: -8, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" vertical={false} />
          <XAxis dataKey="label" tick={{ fontSize: 12 }} interval="preserveStartEnd" minTickGap={12} />
          <YAxis tick={{ fontSize: 12 }} allowDecimals={false} tickFormatter={formatCompact} width={44} />
          <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => formatNumber(v)} />
          <Legend wrapperStyle={{ fontSize: 13 }} />
          {series.map((s) => (
            <Bar key={s.key} dataKey={s.key} name={s.label} fill={s.color} radius={[3, 3, 0, 0]} />
          ))}
        </BarChart>
      </ResponsiveContainer>
      <figcaption className="small muted">
        Weeks start Monday (UTC).{partial ? " * = current week, still in progress." : ""}
      </figcaption>
    </figure>
  );
}

export function TrendLineChart({ data, xKey, series, height = 280, xFormatter = formatShortDate }: {
  data: Record<string, unknown>[];
  xKey: string;
  series: Series[];
  height?: number;
  xFormatter?: (value: string) => string;
}) {
  return (
    <figure className="chart" aria-label={series.map((s) => s.label).join(", ")}>
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={data} margin={{ top: 8, right: 12, left: -4, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" vertical={false} />
          <XAxis dataKey={xKey} tick={{ fontSize: 12 }} tickFormatter={xFormatter} minTickGap={16} />
          <YAxis tick={{ fontSize: 12 }} tickFormatter={formatCompact} width={52} domain={["auto", "auto"]} />
          <Tooltip contentStyle={tooltipStyle} labelFormatter={(v) => xFormatter(String(v))}
            formatter={(v: number) => formatNumber(v)} />
          <Legend wrapperStyle={{ fontSize: 13 }} />
          {series.map((s) => (
            <Line key={s.key} type="monotone" dataKey={s.key} name={s.label} stroke={s.color}
              strokeWidth={2} dot={data.length < 20} connectNulls={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </figure>
  );
}

export function HorizontalBarChart({ data, labelKey, valueKey, valueLabel, color = COLORS.blue }: {
  data: Record<string, unknown>[];
  labelKey: string;
  valueKey: string;
  valueLabel: string;
  color?: string;
}) {
  const height = Math.max(160, data.length * 40 + 40);
  return (
    <figure className="chart" aria-label={valueLabel}>
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 16, left: 8, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="var(--grid)" horizontal={false} />
          <XAxis type="number" tick={{ fontSize: 12 }} tickFormatter={formatCompact} allowDecimals={false} />
          <YAxis type="category" dataKey={labelKey} tick={{ fontSize: 12 }} width={130} />
          <Tooltip contentStyle={tooltipStyle} formatter={(v: number) => formatNumber(v)} />
          <Bar dataKey={valueKey} name={valueLabel} fill={color} radius={[0, 3, 3, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </figure>
  );
}
