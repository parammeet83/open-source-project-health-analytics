import { useApi } from "../api/useApi";
import type { RepositoryList } from "../api/types";

export function RepositoryFilter({ value, onChange }: { value: number | null; onChange: (id: number | null) => void }) {
  const repos = useApi<RepositoryList>("/api/v1/repositories", { sort: "name", order: "asc", limit: 100 });
  return (
    <label className="field">
      <span>Repository</span>
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        disabled={!repos.data}
      >
        <option value="">All repositories</option>
        {repos.data?.items.map((r) => (
          <option key={r.repository_id} value={r.repository_id}>
            {r.full_name}
          </option>
        ))}
      </select>
    </label>
  );
}

export const WINDOW_OPTIONS = [30, 90, 180, 365] as const;

export function WindowFilter({ value, onChange }: { value: number; onChange: (days: number) => void }) {
  return (
    <label className="field">
      <span>Window</span>
      <select value={value} onChange={(e) => onChange(Number(e.target.value))}>
        {WINDOW_OPTIONS.map((d) => (
          <option key={d} value={d}>
            Last {d} days
          </option>
        ))}
      </select>
    </label>
  );
}
