# Development Log

This log records what was actually done and observed. Commands ran on Windows 11 in the project
root, using only the project `.venv` (Python 3.12.10) and Node 24.19 / npm 11.17.

## 2026-10-28 — Design baseline

Design documents created (PRD, TRD, schema, data flow, Power BI spec, plan, decisions).

## 2026-10-29 — Neon provisioned and wired

- `npm i -g neon@latest` installed Neon CLI 8.13.1.
- Ran `neon init -y --org-id org-misty-rain-69863140 --project-id wandering-leaf-74668817 --branch production --services postgres`.
  It created `neon.ts` and a root `package.json`/`package-lock.json`, and added `.neon` to `.gitignore`.
- `neon deploy` completed browser sign-in, reported the `production` branch already matched, and
  pulled `DATABASE_URL`, `DATABASE_URL_UNPOOLED` and `NEON_BRANCH` into `.env.local`.
- Pre-checks on the project: Postgres 17.11, `aws-ap-southeast-1`; it contained only the `public`
  schema and no user tables, so nothing existing was touched.
- **Code changes:**
  - Env loading now reads `.env`, `.env.local` and `.env.roles`.
  - The migration credential falls back to Neon's `DATABASE_URL_UNPOOLED`.
  - Added `db bootstrap-roles --write-env`.
  - The API's local entry point loads the env files.
  - Integer sums now serialize as integers.
  - `run_pipeline.ps1` now tolerates native stderr.
  - The root `package.json` has `build`/`test` scripts.
  - Tests are isolated from the real env files (autouse fixture).
- **Results on Neon:**
  - `db upgrade` → "Migrations applied (head)".
  - `db bootstrap-roles --write-env` → both roles created, 6/6 privilege checks ok.
  - Pipeline as `oss_health_app` (`--repo psf/requests --repo fastapi/fastapi`, unauthenticated) →
    `success`, 814 rows inserted (psf/requests 188 issues + 158 PRs; fastapi 19 issues + 445 PRs).
  - Immediate rerun → `success`, incremental, 15 extracted, 0 inserted, 0 updated.
  - No duplicate snapshots; the issue/PR overlap check passed.
- **API against Neon** (Uvicorn, `oss_health_reader`):
  - `/health/ready` → `{"status":"ok","database":"ok"}`.
  - All data endpoints returned 200 with real data (2 repositories, 158,834 stars, merge rate 52.5%,
    freshness `fresh`).
  - POST → 405; `limit=5000` → 422; CORS allowed the dashboard origin.
- **Dashboard** (Vite dev → local API → Neon), with headless Chromium:
  - Overview KPIs rendered from Neon data; repository detail and quality pages loaded.
  - API calls went only to the API host, and there were 0 console errors.
- `npm run build` (root) exited 0. `scripts\test.ps1`: 151 backend tests passed (93% coverage),
  25 frontend tests passed, and the bundle scan was clean.
- **Not yet done:** GitHub repository, Cloud Run and Vercel deployment. `microsoft/vscode` and the
  remaining repositories await a token-backed cloud ingestion.

## 2026-10-28 — Implementation and local verification

### Reconciliation
- The final architecture (Vercel + Cloud Run + Neon + GitHub Actions; Power BI optional) superseded
  the local-only design. It is recorded as ADR-019…026, and every document was revised to match the code.

### Environment
- Created `.venv` with `py -3.12`. Installed `backend[pipeline,api,dev]` (editable).
- Key versions: pandas 2.3.3, NumPy 2.5.4, Pandera 0.35.0, SQLAlchemy 2.0.55, psycopg 3.3.6,
  Alembic 1.20.1, FastAPI 0.162.0, pytest 9.2.0.
- **No PostgreSQL or Docker was available locally.** `pgserver` 0.1.4 (PostgreSQL **16.2**) was
  installed into `.venv` only, with data in `.pgdata/`. Its Windows build lacked time-zone files
  (`invalid value for parameter "TimeZone": "UTC"`). Copying the `tzdata` package's zoneinfo into
  the pgserver install fixed this, and `scripts/local_postgres.ps1` now automates it. Created the
  dedicated databases `osp_health_dev` and `osp_health_test`.

### Defects found and fixed during testing
| Defect | Fix |
|--------|-----|
| Analytics SQL file was split inside its own header comment | Reworded the comment; statement marker documented |
| Pandera reports frame-level failures per column, labelled by error text | Collapse to one rejection per row per check; map to stable check names |
| After a rate-limit wait, a stale `remaining=0` caused a second, needless throttle | Reset the remaining budget after any rate-limit wait |
| **`from __future__ import annotations` in the API module made FastAPI treat the DB dependency as a query parameter** (every data endpoint would have returned 422) | Removed it from `api/main.py`, with an explanatory comment; caught by unit tests |
| Same-day snapshot rerun counted as an "update" even with identical values | The snapshot guard now also requires a changed metric |
| Unknown paths returned Starlette's `{"detail": ...}` | A unified error handler returns `{"error": "not_found"}` |
| Median time to merge for psf/requests displayed as "0.0 days" | Durations under 48 h now display in hours |
| API pool (3 + 2) × 3 instances exceeded the reader's 10-connection limit | Pool defaults lowered to 2 + 1 (9 at max scale) |
| Bundle scan flagged React Router's internal `"http://localhost"` base | The scan now targets localhost URLs with a port (the dev fallback is compiled out); a positive check that the API URL is embedded was added |
| npm audit: react-router 6.x (2 moderate); Vitest 3 chain (`tinypool` critical, dev-only) | Upgraded to react-router-dom 7.18.5 and vitest 4.1.12, then 0 vulnerabilities |
| pip-audit: pytest 8.4.2 (PYSEC-2026-1845) | Dev requirement raised to `pytest>=9.0.3`, then no known vulnerabilities |

### Test results (final local run, `scripts\test.ps1`)
- Ruff lint + format: clean.
- Backend: **148 passed** (118 unit + 30 integration against PostgreSQL 16.2 via pgserver). Line +
  branch coverage **94%**.
- Frontend: TypeScript check clean; **25 Vitest tests passed**; production build OK; `check:dist`
  clean, with the API URL confirmed embedded.
- `pip-audit`: no known vulnerabilities. `npm audit` (all dependencies): 0 vulnerabilities.

### Live GitHub ingestion (local, unauthenticated, into `osp_health_dev` as `oss_health_app`)
- `oss-health run --repo psf/requests` → `success`, exit 0, **10 API requests**.
  - Loaded 1 repository, 1 snapshot, 188 issues and 158 pull requests.
  - 25 checks evaluated, 25 passed. Mode: backfill (90 days + all open items).
  - Watermarks advanced.
- Immediate rerun → `success`, mode incremental, **4 requests**, totals inserted 0 / updated 0 /
  unchanged 6. Row counts unchanged, and still one snapshot for the day.
- `analytics.dim_repository` (psf/requests): 157 open issues, 87 open PRs, 54,589 stars, coverage
  start 2026-07-31.
- The four-repository and `microsoft/vscode` runs were **not** executed locally (60 requests/hour
  unauthenticated). They belong to the first cloud ingestion with a token.

### API and UI checks (local)
- An API-only install (`backend[api]`, **no pandas**) was served with Uvicorn as `oss_health_reader`
  against the live-ingested dev DB:
  - all 11 endpoints returned 200 with real data
  - `limit=5000` → 422; an unknown repository → 404; `POST` → 405
  - CORS allowed `http://localhost:5173` and refused another origin
  - freshness `fresh`
- Playwright Chromium (headless; installed in a scratch directory, not in the project) rendered
  every page at **390×844 (phone viewport), 820×1180 (tablet viewport) and 1440×900 (desktop)**:
  - 21 page loads, **0 px horizontal overflow, 0 console errors**
  - the screenshots were inspected
  - **These are browser viewport emulations, not tests on physical devices.**

### Not done (blocked by external accounts)
- The Docker image was not built locally (no Docker); the CI job `api-image` builds and smoke-tests it.
- CI workflows have not run yet: the GitHub repository does not exist.
- No Neon, Google Cloud or Vercel resources were created. See [DEPLOYMENT.md](DEPLOYMENT.md#owner-actions-required).

### Repository
- `git init -b main` locally, with `.gitattributes` for line endings.
- Before committing, I reviewed the 109 staged files: no `.env`, database data, reports, logs,
  `node_modules`, `.venv` or build output.
- **Gitleaks 8.30.1:** no leaks in the exported staged tree, or in the full history after the commit.
  `git diff --check` against the empty tree was clean.
- Initial commit `db9c10a`. **No remote has been added and nothing has been pushed.**
