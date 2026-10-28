import type { ReactNode } from "react";
import type { ApiError } from "../api/client";
import type { RunStatus } from "../api/types";
import { humanize } from "../format";

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <header className="page-header">
      <div>
        <h1>{title}</h1>
        {subtitle ? <p className="muted">{subtitle}</p> : null}
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function Card({ title, subtitle, children, className = "" }: {
  title?: string;
  subtitle?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`card ${className}`} aria-label={title}>
      {title ? (
        <div className="card-head">
          <h2>{title}</h2>
          {subtitle ? <p className="muted small">{subtitle}</p> : null}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Kpi({ label, value, hint, tone }: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  tone?: "good" | "warn" | "bad";
}) {
  return (
    <div className={`kpi ${tone ? `kpi-${tone}` : ""}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{value}</div>
      {hint ? <div className="kpi-hint">{hint}</div> : null}
    </div>
  );
}

const STATUS_LABEL: Record<RunStatus, string> = {
  success: "Success",
  partial_success: "Partial success",
  failed: "Failed",
  running: "Running",
};

export function StatusBadge({ status }: { status: RunStatus | string }) {
  const label = STATUS_LABEL[status as RunStatus] ?? humanize(status);
  return <span className={`badge badge-${status}`}>{label}</span>;
}

export function CheckBadge({ status, severity }: { status: string; severity: string }) {
  const tone = status === "passed" ? "success" : status === "skipped" ? "skipped" : severity === "error" ? "failed" : "partial_success";
  return <span className={`badge badge-${tone}`}>{status === "failed" ? `${severity} failed` : status}</span>;
}

export function Loading({ label = "Loading data…" }: { label?: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: ApiError | null; onRetry?: () => void }) {
  return (
    <div className="state state-error" role="alert">
      <strong>Data could not be loaded.</strong>
      <span>{error?.message ?? "Unknown error."}</span>
      {onRetry ? (
        <button type="button" className="button" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state state-empty">{children}</div>;
}

/** Renders loading / error / empty / content for one API resource. */
export function Async<T>({ state, empty, children }: {
  state: { data: T | null; error: ApiError | null; loading: boolean; reload: () => void };
  empty?: (data: T) => ReactNode | null;
  children: (data: T) => ReactNode;
}) {
  if (state.loading && !state.data) return <Loading />;
  if (state.error) return <ErrorState error={state.error} onRetry={state.reload} />;
  if (!state.data) return <Loading />;
  const emptyNode = empty?.(state.data);
  if (emptyNode) return <Empty>{emptyNode}</Empty>;
  return <>{children(state.data)}</>;
}
