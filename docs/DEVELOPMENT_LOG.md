# Development Log

This log records what was actually done and observed. Commands ran on Windows 11 in the project
root, using only the project `.venv` (Python 3.12.10) and Node 24.19 / npm 11.17.

## 2026-10-28 — Design baseline

Design documents created (PRD, TRD, schema, data flow, Power BI spec, plan, decisions).

## 2026-10-29 — Public deployment on Vercel Hobby (no Google Cloud)

- **Decision:** the owner approved no charges, so the API moved from the planned Cloud Run to a
  Vercel Function (ADR-027). No Google Cloud resources were ever created. The Cloud Run workflow,
  the Dockerfile and the deploy script were removed, and a standalone `migrate.yml` was added.
- **Plan check (official docs):** Hobby is free with no billing cycle. Exceeding included usage
  pauses the feature. 1M invocations, 4 active-CPU h, 360 GB-h memory and 100 GB transfer per
  month; personal, non-commercial use. No paid action was needed or taken.
- **Backend:**
  - Added `backend/index.py`, which re-exports `oss_health.api.app:app`.
  - FastAPI moved to core dependencies, the only ones Vercel installs.
  - `backend/vercel.json`: framework `fastapi`, region `sin1`, `maxDuration` 30, `excludeFiles`.
  - Added `.vercelignore`.
  - The rate limiter now keys on `x-vercel-forwarded-for`.
  - Verified locally with only the core dependencies installed (no pandas, Pandera, Alembic or
    uvicorn): `/health/live` 200, `/health/ready` 503 without a DB, POST 405.
  - The first deploy failed ("pattern index.py doesn't match any Serverless Functions inside api");
    fixed by declaring `"framework": "fastapi"`.
- **Vercel projects:** `oss-health-api` and `oss-health-dashboard` were created via CLI under
  `parammeet-projects`. The existing `conservation-geography-quiz` project was not touched.
  - Root Directories were set to `backend` / `frontend` (and the Vite preset) via `vercel api`.
  - Env vars were piped on stdin:
    - `API_DATABASE_URL`: the reader role, validated first; **sensitive**.
    - `CORS_ALLOWED_ORIGINS`, `DB_POOL_SIZE=1`, `DB_MAX_OVERFLOW=1`, `RATE_LIMIT_PER_MINUTE`,
      `STALE_AFTER_HOURS`.
    - `VITE_API_BASE_URL` (frontend, public).
  - The writer and owner credentials are not in Vercel.
- **Live verification (2026-10-28 ~20:40–20:55 UTC, from an external network):**
  - **API (`https://oss-health-api.vercel.app`):**
    - `/health/live` 200; `/health/ready` `{"status":"ok","database":"ok"}`.
    - All data endpoints 200: 5 repositories, freshness `fresh`; about 0.7–1.9 s per request.
    - POST and DELETE → 405; `/api/v1/pipeline/run` → 404; `limit=5000` → 422; a > 730-day
      range → 422; a 2 KB query → 414; unknown path → `{"error":"not_found"}`.
    - No credential markers in responses.
  - **CORS:** a preflight from the dashboard origin returned 200 with `allow-origin` set to the
    dashboard; one from `https://evil.example.com` returned 400 with no allow-origin.
  - **Dashboard (`https://oss-health-dashboard-one.vercel.app`), headless Chromium:**
    - All 6 pages at 390×844, 820×1180 and 1440×900 (**emulated viewports, not physical devices**):
      HTTP 200, 0 px horizontal overflow, no console or page errors.
    - A deep link loaded directly worked (`/repositories/41881900` → microsoft/vscode), and an
      unknown route showed "Page not found".
    - The browser contacted only the dashboard and API origins.
    - KPIs rendered real data: 471.5K stars, 22,632 open issues, merge rate 75.9%.
  - **Deployed assets** (15 files fetched from the live site): no database URLs, tokens, Neon host
    or password, OIDC token, server variable names or localhost API URL. The API URL is embedded;
    CSP `connect-src` allows only the API origin; HSTS, X-Frame-Options and nosniff are present.
  - **Live data refresh:** a manual **Ingest GitHub data** run (`psf/requests`, run 40767015420)
    succeeded in GitHub Actions. The public API immediately reported run `454030ab…`, and
    freshness stayed `fresh`, with no redeploy.
- **Tests:** backend 196 passed (94% coverage); frontend 25 passed; type check, production build
  and bundle scan clean.

## 2026-10-29 — Fix: migration URL "could not be parsed" in GitHub Actions

- After `b6c19d1`, the migrate job reached URL parsing and failed with exit 3
  (`Database URL could not be parsed`), before any connection attempt.
- **Workflow mapping:** verified correct (`MIGRATION_DATABASE_URL: ${{ secrets.MIGRATION_DATABASE_URL }}`).
  The secret is repository-level; the `production` environment has no secrets of its own.
- **Local check:** `oss-health db check-url --env DATABASE_URL_UNPOOLED` on `.env.local` → all
  checks OK. The application parser rejects the quoted, `psql '...'` and `KEY=` forms with exactly
  the observed message, so the stored secret did not hold a bare URL.
- **Added:**
  - `oss-health db check-url`, which prints only the variable name and yes/no flags: present,
    whitespace, quotes, `KEY=` prefix, scheme, parser acceptance.
  - A validation step before migrations (deploy) and before ingestion (pipeline URL).
  - `normalize_database_url` now rejects internal whitespace (stricter).
  - `db upgrade` validates the URL before invoking Alembic.
  - 31 regression tests.
- **Secret:** `MIGRATION_DATABASE_URL` was re-set from `.env.local` `DATABASE_URL_UNPOOLED` via
  `gh secret set` with stdin (exit 0; value never displayed). No Neon data or passwords were changed.
- **Tests:** backend 192 passed (93.94% coverage). `git diff --check` and Gitleaks clean. One
  synthetic test password is annotated `gitleaks:allow`.

## 2026-10-29 — Fix: `db upgrade` failed in GitHub Actions (`No 'script_location' key found`)

- **Symptom:** the **Migrate and deploy API** run (commit `ced70c2`) failed at "Apply migrations"
  with `CommandError: No 'script_location' key found in configuration.`
- **Root cause, reproduced locally** with the same non-editable `pip install ".[pipeline]"` used by
  the workflow:
  - `BACKEND_DIR` was derived from the module path, so in `site-packages` it resolved to `…\Lib`.
    `Config(BACKEND_DIR / "alembic.ini")` therefore read a non-existent file and had no options.
  - The migrations (`backend/migrations`) were not part of the installed package at all.
  - The same path problem made `check-config` / `run` fail to find `config/pipeline.toml`, so
    ingestion would have failed the same way.
- **Fix:**
  - Moved the migrations into the package (`oss_health/migrations`, declared as package data).
  - Added `oss_health.migrate.alembic_config()`, which builds the config in code with an absolute
    `script_location`. The URL is passed via `Config.attributes`, never as an option.
  - `env.py` falls back to `MIGRATION_DATABASE_URL`, then `DATABASE_URL_UNPOOLED`, and registers
    the password for redaction.
  - `BACKEND_DIR` is now discovered from the source tree, the working directory/parents, or `OSS_HEALTH_HOME`.
  - Integration fixtures use the same config builder.
  - CI has a new installed-package smoke step.
- **Verification:**
  - In the non-editable venv: `db upgrade` exits 0; a fresh empty database (temporary, dropped
    afterwards) migrated to `0003_analytics_views` with 8 tables and 8 views; `check-config` exits
    0; a dry run wrote `backend/reports/`.
  - 10 new regression tests. Backend 161 passed (93% coverage); frontend 25 passed.
  - `scripts\verify.ps1` passed, and `git diff --check` was clean.
  - GitHub secrets were not changed: the failure happened before any database connection was attempted.

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
