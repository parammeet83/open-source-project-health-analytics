# Product Requirements Document (PRD)

**Project:** Open-Source Project Health Analytics — Automated GitHub Data Pipeline
**Document status:** Approved baseline, revised 2026-10-28 for the hosted architecture.
**Implementation status:** Implemented and tested locally. Hosting not yet deployed (see [DEPLOYMENT.md](DEPLOYMENT.md)).
**Related:** [TRD](TRD.md) · [DB Schema](Backend_DB_Schema.md) · [Data Flow](Data_Flow.md) · [Power BI](PowerBI_Dashboard_Spec.md) · [Plan](implementation_plan.md) · [Decisions](DECISIONS.md)

## Revision note

The original design was a local pipeline, with Windows Task Scheduler and Power BI as the main
outputs. The approved final architecture supersedes those assumptions:

- a **public website** (React/Vite on Vercel),
- a **read-only API** (FastAPI, hosted as a Vercel Function; originally planned for Google Cloud
  Run, replaced to avoid any billing requirement, see ADR-027),
- **Neon PostgreSQL** as the hosted database,
- **GitHub Actions** for CI and daily/manual ingestion,
- **Power BI** as an optional supplement.

Nothing may depend on the owner's laptop. See [ADR-019](DECISIONS.md#adr-019-hosted-architecture-vercel--cloud-run--neon--github-actions).

## 1. Problem statement

People who depend on open-source software want to know whether a project is healthy. Is it growing?
Are issues handled? Are contributions merged in a reasonable time? GitHub shows current counts, but
not trends, backlog age or merge throughput. Naive collection is easily misleading: rate limits,
pagination, pull requests inside the issues endpoint, and silent partial failures all distort the
numbers. This project builds an automated, auditable pipeline and a public dashboard that labels its
own data quality honestly.

## 2. Goals and non-goals

| ID | Goal |
|----|------|
| G1 | Collect repository metadata, daily star/fork snapshots, issues and pull requests for configurable public repositories |
| G2 | Correct metrics: no issue/PR double counting, no duplicates, documented definitions |
| G3 | Unattended daily cloud runs; reruns always safe |
| G4 | Visible failures: run records, quality checks, JSON reports, nonzero exit codes, honest dashboard status |
| G5 | One public HTTPS link usable on phones, tablets and desktops without an account |
| G6 | Production-style engineering for a portfolio |

**Non-goals (v1):**
- real-time ingestion
- private repositories
- comments, reviews, commits and PR diff statistics
- reconstructing past star counts
- public write actions of any kind
- custom domains
- paid services without explicit owner approval
- external alerting (email/Slack)

## 3. Users

| Persona | Needs | Touchpoint |
|---------|-------|------------|
| Visitor / reviewer | Understand project health and the engineering quickly, on any device | Public dashboard, README |
| Analyst | Trustworthy trends and definitions | Dashboard, optional Power BI |
| Operator (owner) | Know whether ingestion worked and why not | GitHub Actions run summary, Data Quality page, quality report, exit codes |

## 4. Scope

| Area | In scope |
|------|----------|
| Repositories | Configurable; initially `pandas-dev/pandas`, `scikit-learn/scikit-learn`, `fastapi/fastapi`, `psf/requests`, `microsoft/vscode` (vscode after the bounded strategy was verified on the others) |
| History | Items updated in the last 90 days + **all currently open** issues and PRs; incremental afterwards with a 24 h overlap. Not a complete historical record. |
| Snapshots | One genuine stars/forks/watchers snapshot per repository per UTC day, starting at the first run |
| Website pages | Overview · Repository Explorer · Repository Detail · Issues & Backlog · Pull Requests · Data Quality & Runs · About & Sources |
| API | Versioned read-only endpoints (`/api/v1/...`) and health checks |
| Automation | CI, scheduled + manual ingestion, migrate-then-deploy |

## 5. Functional requirements

| ID | Requirement | Implemented in |
|----|-------------|----------------|
| FR-01 | Repositories, window, overlap, page cap, timeouts, retries, rate-limit waits and thresholds are configurable without code changes | `backend/config/pipeline.toml`, `config.py` |
| FR-02 | Secrets only from environment / git-ignored env files / GitHub secrets / Vercel encrypted env vars; never logged, reported or bundled | `redaction.py`, `check-dist.mjs`, workflows |
| FR-03 | Invalid configuration stops before extraction with exit 3 | `config.py`, `pipeline.py` |
| FR-10 | REST API with Link pagination, timeouts, bounded retries + jitter, primary/secondary rate limits, `Retry-After` | `github/client.py` |
| FR-11 | Extract metadata (and snapshot), issues and PRs | `github/extract.py` |
| FR-12 | Exclude `pull_request` items from issues; PRs only from `/pulls`; post-load overlap check on `(repository_id, number)` | `transform.py`, `pipeline.py` |
| FR-13 | Incremental watermarks with overlap; `/pulls` uses supported sort + early stop (it has no `since` parameter) | `extract.py`, `load.py` |
| FR-14 | Backfill = 90-day window + all open issues and PRs | `extract.py` |
| FR-15 | API failures are never treated as empty data; failed repositories keep their watermarks | `client.py`, `pipeline.py` |
| FR-16 | Page cap produces an error-severity completeness check | `client.py`, `pipeline.py` |
| FR-20–23 | pandas normalization, Pandera row validation with quarantine and reasons, dedup, dataset checks | `transform.py`, `validation.py` |
| FR-24 | Bots flagged (`is_bot_author`), not removed | `transform.py` |
| FR-30–34 | Idempotent guarded upserts, per-repository transaction, one snapshot per UTC day, watermarks advance only after a committed, validated load | `load.py`, `pipeline.py` |
| FR-40–45 | Run claimed (`running`) and finalized; checks/rejections persisted even on rollback; JSON report on every run including failures; exit codes 0/1/2/3; advisory lock + stale-run sweep | `tracking.py`, `report.py`, `pipeline.py` |
| FR-50–52 | Eight `analytics` views; reader-only API; coverage start exposed and honoured | `sql/analytics_views.sql`, `api/` |
| FR-60 | Public dashboard with the seven pages; loading/empty/error/stale states; missing shown as "—" | `frontend/` |
| FR-61 | No anonymous write endpoint | `api/main.py` (405 guard; tests) |
| FR-62 | Daily + manual cloud ingestion with minimal permissions and concurrency control | `.github/workflows/ingest.yml` |

## 6. Non-functional requirements

| ID | Requirement |
|----|-------------|
| NFR-01 | Reruns with unchanged data change nothing (no inserts or updates; `etl_updated_at` unchanged) |
| NFR-02 | One repository's failure does not block the others; the run is labelled `partial_success` |
| NFR-03 | Operates with the owner's computer off |
| NFR-04 | Stays within GitHub rate limits; respects every rate-limit signal |
| NFR-05 | Least privilege: separate migration, writer and reader credentials; TLS to the database |
| NFR-06 | ≥ 85% backend line coverage in CI; no network or token needed for unit tests |
| NFR-07 | Responsive and accessible: 44 px touch targets, keyboard navigation, skip link, no horizontal scroll at 390 px |
| NFR-08 | No unapproved paid resources |

## 7. Metric definitions (authoritative)

All dates are UTC. Weeks start on Monday.

| Metric | Definition |
|--------|------------|
| Stars / forks over time | Values from real daily snapshots; history starts at the first run |
| Open issues | Issues (never PRs) with state `open`. Reported as null until that repository's issue backfill (including all open items) has completed. |
| Backlog age | Days from `created_at` to today for open issues; buckets 0–7, 8–30, 31–90, 91–180, 181–365, >365 |
| Issues opened/closed per week | By `created_at` / final `closed_at`, only weeks from the repository's coverage start |
| PR merge rate | merged ÷ (merged + closed unmerged) for PRs closed in the window; null when the denominator is 0 |
| Median time to merge | Median `merged_at − created_at` for PRs merged in the window |
| Pipeline success rate | `success` ÷ finished runs in the last 30 days |
| Rejection rate | Rejected rows ÷ extracted rows per entity per repository (threshold 5%) |
| Freshness | `fresh` / `degraded` (latest finished run not `success`) / `stale` (> 36 h since last success) / `no_successful_run` |
| Coverage start date | First full UTC day from which issue **and** PR events are complete (both backfills done) |

## 8. Acceptance criteria

| ID | Criterion | Status (2026-10-29) |
|----|-----------|---------------------|
| AC-01 | Unchanged rerun: same row counts, no `etl_updated_at` change | **Verified** (integration test; live rerun of psf/requests: 0 inserted, 0 updated) |
| AC-02 | No `(repository_id, number)` in both issues and PRs; overlap check detects injected duplicates | **Verified** (integration tests) |
| AC-03 | ≤ 1 snapshot per repository per UTC day; same-day rerun keeps one row | **Verified** |
| AC-04 | 5xx for one repository → `partial_success`, exit 2, others loaded, its watermarks unchanged | **Verified** |
| AC-05 | `Retry-After` honoured; retries bounded | **Verified** (unit tests, fake clock) |
| AC-06 | 401 → `failed`, exit 1 | **Verified** |
| AC-07 | Database unreachable → exit 3 + `not_started` report | **Verified** |
| AC-08 | Invalid rows quarantined with check name and reason | **Verified** |
| AC-09 | Older versions never overwrite newer ones | **Verified** |
| AC-10 | Quality report on every run, including failures | **Verified** |
| AC-11 | No secrets in logs, reports, database or bundle | **Verified** (redaction tests, bundle scan, gitleaks) |
| AC-12 | Alembic upgrade → downgrade → upgrade | **Verified** (local PostgreSQL 16.2; CI targets PostgreSQL 17) |
| AC-13 | CI runs lint, audits, tests (≥ 85% coverage), API entrypoint smoke test, frontend checks, secret scan | **Verified** (GitHub Actions green on `main`) |
| AC-14 | Adding a repository needs only a config change | **Verified** (config-driven; `--repo` override tested) |
| AC-15 | Public site works on phone and desktop | **Verified** on the live URL in 390/820/1440 px browser viewports (emulated, not physical devices) |
| AC-16 | Cloud ingestion with the laptop off | **Verified** for manual GitHub Actions runs (data visible through the public API). The first scheduled run is pending. |
| AC-17 | Least-privilege roles enforced | **Verified** (integration tests + `db verify-roles`) |
| AC-18 | No anonymous write/admin endpoint | **Verified** (unit tests; local live probe POST → 405) |

## 9. Constraints and assumptions

- A token is optional. Unit tests and `check-config` need none. Cloud ingestion uses `GH_API_TOKEN`
  if set, otherwise the job's built-in token (about 1,000 requests/hour for read-only REST calls on
  public data).
- Neon Free, Vercel Hobby and GitHub Actions on a public repository cost nothing. Vercel Hobby
  pauses rather than bills when included usage is exceeded. No Google Cloud resources are used.

## 10. Risks

| Risk | Mitigation |
|------|-----------|
| `microsoft/vscode` volume | 300-page cap per series with a completeness check; added after the smaller repositories |
| Schedule disabled after 60 days without commits | Documented re-enable steps; the dashboard turns "stale" after 36 h |
| Neon scale-to-zero cold starts | `/health/ready` and the API tolerate them; pool pre-ping |
| In-memory rate limit is per instance | Documented; max 3 instances and connection limits bound the load |
