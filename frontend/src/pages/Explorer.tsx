import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api/useApi";
import type { RepositoryList } from "../api/types";
import { Async, Card, PageHeader } from "../components/ui";
import { formatDate, formatNumber } from "../format";

const SORTS = [
  { value: "stars", label: "Stars" },
  { value: "forks", label: "Forks" },
  { value: "open_issues", label: "Open issues" },
  { value: "open_prs", label: "Open PRs" },
  { value: "name", label: "Name" },
] as const;

export default function Explorer() {
  const [params, setParams] = useSearchParams();
  const sort = SORTS.some((s) => s.value === params.get("sort")) ? (params.get("sort") as string) : "stars";
  const language = params.get("language") ?? "";
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [debounced, setDebounced] = useState(search);

  useEffect(() => {
    const t = setTimeout(() => setDebounced(search.trim()), 250);
    return () => clearTimeout(t);
  }, [search]);

  const all = useApi<RepositoryList>("/api/v1/repositories", { sort: "name", order: "asc", limit: 100 });
  const languages = useMemo(
    () => [...new Set((all.data?.items ?? []).map((r) => r.primary_language).filter((l): l is string => Boolean(l)))].sort(),
    [all.data],
  );
  const repos = useApi<RepositoryList>("/api/v1/repositories", {
    search: debounced.replace(/[^\w .\-/]/g, "").slice(0, 100) || undefined,
    language: language || undefined,
    sort,
    order: sort === "name" ? "asc" : "desc",
    limit: 100,
  });

  function update(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  }

  return (
    <>
      <PageHeader title="Repository Explorer" subtitle="Search and compare the tracked repositories." />
      <div className="filters" role="search">
        <label className="field grow">
          <span>Search</span>
          <input
            type="search"
            value={search}
            maxLength={100}
            placeholder="Name or description"
            onChange={(e) => {
              setSearch(e.target.value);
              update("q", e.target.value);
            }}
          />
        </label>
        <label className="field">
          <span>Language</span>
          <select value={language} onChange={(e) => update("language", e.target.value)}>
            <option value="">All languages</option>
            {languages.map((l) => (
              <option key={l} value={l}>
                {l}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Sort by</span>
          <select value={sort} onChange={(e) => update("sort", e.target.value)}>
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </select>
        </label>
      </div>
      <Async state={repos} empty={(r) => (r.items.length === 0 ? "No repositories match these filters." : null)}>
        {(r) => (
          <>
            <p className="muted small" aria-live="polite">
              {r.total} repositor{r.total === 1 ? "y" : "ies"}
            </p>
            <div className="repo-grid">
              {r.items.map((x) => (
                <Card key={x.repository_id} className="repo-card">
                  <h2 className="repo-title">
                    <Link to={`/repositories/${x.repository_id}`}>{x.full_name}</Link>
                  </h2>
                  <p className="muted small clamp">{x.description ?? "No description."}</p>
                  <dl className="stat-list">
                    <div><dt>Stars</dt><dd>{formatNumber(x.stars_latest)}</dd></div>
                    <div><dt>Forks</dt><dd>{formatNumber(x.forks_latest)}</dd></div>
                    <div><dt>Open issues</dt><dd>{formatNumber(x.open_issues)}</dd></div>
                    <div><dt>Open PRs</dt><dd>{formatNumber(x.open_prs)}</dd></div>
                  </dl>
                  <p className="small muted">
                    {x.primary_language ?? "Unknown language"} · coverage since {formatDate(x.coverage_start_date)}
                    {x.is_archived ? " · archived" : ""}
                  </p>
                </Card>
              ))}
            </div>
          </>
        )}
      </Async>
    </>
  );
}
