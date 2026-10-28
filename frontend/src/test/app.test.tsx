import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AppRoutes } from "../App";
import { clearApiCache } from "../api/useApi";
import type { Freshness } from "../api/types";
import { freshFreshness, mockApi, okRun, summary } from "./fixtures";

function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AppRoutes />
    </MemoryRouter>,
  );
}

describe("pages", () => {
  it("renders the overview with KPIs from the API", async () => {
    mockApi();
    renderAt("/");
    expect(await screen.findByText("Data is up to date")).toBeInTheDocument();
    expect(screen.getByText("54.3K")).toBeInTheDocument();
    expect(screen.getByText("75.0%")).toBeInTheDocument();
    expect(screen.getByText("2.0 days")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "acme/alpha" })).toHaveAttribute("href", "/repositories/101");
  });

  it.each([
    ["/repositories", "Repository Explorer"],
    ["/repositories/101", "acme/alpha"],
    ["/issues", "Issues & Backlog"],
    ["/pull-requests", "Pull Request Analytics"],
    ["/quality", "Data Quality & Pipeline Runs"],
    ["/about", "About & Sources"],
    ["/no-such-page", "Page not found"],
  ])("route %s renders %s", async (path, heading) => {
    mockApi();
    renderAt(path);
    expect(await screen.findByRole("heading", { level: 1, name: heading })).toBeInTheDocument();
  });

  it("only ever issues GET requests to the configured API", async () => {
    const { calls, fetchMock } = mockApi();
    renderAt("/");
    await screen.findByText("Data is up to date");
    expect(calls.length).toBeGreaterThan(0);
    for (const call of calls) expect(call.pathname.startsWith("/api/v1/")).toBe(true);
    for (const [, init] of fetchMock.mock.calls) expect(init?.method).toBe("GET");
  });

  it("shows an error state with retry when the API fails", async () => {
    mockApi({ "/api/v1/summary": new Response(JSON.stringify({ error: "internal_error" }), { status: 500 }) });
    renderAt("/");
    const alerts = await screen.findAllByRole("alert");
    expect(alerts.some((a) => /temporarily unavailable/.test(a.textContent ?? ""))).toBe(true);
    expect(screen.getAllByRole("button", { name: "Try again" }).length).toBeGreaterThan(0);
  });

  it("shows empty states instead of zeros when nothing is ingested", async () => {
    const noData: Freshness = { ...freshFreshness, status: "no_successful_run", latest_run: null, last_successful_run: null, hours_since_last_success: null };
    mockApi({
      "/api/v1/summary": {
        ...summary,
        freshness: noData,
        repositories: { ...summary.repositories, repository_count: 0, stars_total: null, forks_total: null, open_issues_total: null, open_prs_total: null },
        pull_requests: { ...summary.pull_requests, merge_rate: null, median_hours_to_merge: null },
        runs: { ...summary.runs, success_rate: null },
      },
      "/api/v1/repositories": { items: [], total: 0, limit: 100, offset: 0 },
      "/api/v1/analytics/activity": { weeks: 13, items: [] },
    });
    renderAt("/");
    expect(await screen.findByText(/No successful ingestion run yet/)).toBeInTheDocument();
    const stars = screen.getByText("Total stars").closest(".kpi") as HTMLElement;
    expect(within(stars).getByText("—")).toBeInTheDocument();
    const mergeRate = screen.getByText("PR merge rate (90 days)").closest(".kpi") as HTMLElement;
    expect(within(mergeRate).getByText("—")).toBeInTheDocument();
    expect(screen.getAllByText("No repositories have been ingested yet.").length).toBeGreaterThan(0);
  });

  it("never shows a healthy banner when the latest run failed", async () => {
    const failed = { ...okRun, run_id: "f", status: "failed" as const, repositories_failed: 2, repositories_succeeded: 0 };
    mockApi({ "/api/v1/summary": { ...summary, freshness: { ...freshFreshness, status: "degraded", latest_run: failed } } });
    renderAt("/");
    expect(await screen.findByText(/latest ingestion run failed/)).toBeInTheDocument();
    expect(screen.queryByText("Data is up to date")).not.toBeInTheDocument();
    expect(screen.getByText("Failed")).toHaveClass("badge-failed");
  });

  it("labels partial and stale data", async () => {
    const partial = { ...okRun, status: "partial_success" as const, repositories_failed: 1 };
    mockApi({ "/api/v1/summary": { ...summary, freshness: { ...freshFreshness, status: "degraded", latest_run: partial } } });
    const first = renderAt("/");
    expect(await screen.findByText(/only partially successful/)).toBeInTheDocument();
    first.unmount();
    clearApiCache();
    mockApi({ "/api/v1/summary": { ...summary, freshness: { ...freshFreshness, status: "stale", hours_since_last_success: 80 } } });
    renderAt("/");
    expect(await screen.findByText(/Data is stale/)).toBeInTheDocument();
  });

  it("shows a single-snapshot message instead of a fake trend", async () => {
    mockApi();
    renderAt("/repositories/101");
    expect(await screen.findByText(/Only one snapshot so far/)).toBeInTheDocument();
  });

  it("handles unknown repositories with a not-found message", async () => {
    mockApi();
    renderAt("/repositories/999");
    expect(await screen.findByRole("heading", { name: "Repository not found" })).toBeInTheDocument();
  });

  it("passes the selected repository filter to the API", async () => {
    const { calls } = mockApi();
    renderAt("/issues?repo=101&days=30");
    await screen.findByText(/Ancient bug/);
    const issueCall = calls.find((c) => c.pathname === "/api/v1/analytics/issues");
    expect(issueCall?.searchParams.get("repository_id")).toBe("101");
    expect(issueCall?.searchParams.get("days")).toBe("30");
  });

  it("shows failed quality checks and run history", async () => {
    mockApi({
      "/api/v1/quality/latest": {
        run: { ...okRun, status: "partial_success" },
        freshness: freshFreshness,
        checks: [{
          check_name: "extraction_completeness", check_stage: "extract", entity: "pull_request", repository_full_name: "acme/alpha",
          severity: "error", status: "failed", records_evaluated: 10, records_failed: null, observed_value: 300, threshold_value: null,
          details: {}, checked_at_utc: "2026-10-28T02:24:00Z",
        }],
      },
    });
    renderAt("/quality");
    expect(await screen.findByText("extraction completeness")).toBeInTheDocument();
    expect(screen.getByText("error failed")).toHaveClass("badge-failed");
    expect(await screen.findByText("scheduled")).toBeInTheDocument();
  });
});

describe("navigation", () => {
  it("toggles the mobile menu and exposes its state", async () => {
    mockApi();
    renderAt("/about");
    const button = await screen.findByRole("button", { name: /open menu/i });
    expect(button).toHaveAttribute("aria-expanded", "false");
    await userEvent.click(button);
    expect(screen.getByRole("button", { name: /close menu/i })).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("primary-nav")).toHaveClass("open");
  });

  it("navigates between pages through the nav links", async () => {
    mockApi();
    renderAt("/about");
    await userEvent.click(screen.getByRole("link", { name: "Pull Requests" }));
    expect(await screen.findByRole("heading", { level: 1, name: "Pull Request Analytics" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Pull Requests" })).toHaveClass("active");
  });
});
