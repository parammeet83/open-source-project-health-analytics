# Run & Verify

## 1. One-time setup

```powershell
scripts\setup.ps1          # .venv (Python 3.12), editable backend [pipeline,api,dev], npm ci, .env if missing
scripts\local_postgres.ps1 # optional local PostgreSQL (pgserver in .venv; data in .pgdata)
```

Fill in `.env` (git-ignored; template in `.env.example`). For local work with `local_postgres.ps1`,
use the URL it prints:

| Variable | Local value |
|----------|-------------|
| `MIGRATION_DATABASE_URL` | `postgresql://postgres@127.0.0.1:<port>/osp_health_dev` |
| `PIPELINE_DATABASE_URL` | `postgresql://oss_health_app:<password>@127.0.0.1:<port>/osp_health_dev` |
| `API_DATABASE_URL` | `postgresql://oss_health_reader:<password>@127.0.0.1:<port>/osp_health_dev` |
| `TEST_DATABASE_URL` | `postgresql://postgres@127.0.0.1:<port>/osp_health_test` |
| `DEV_DATABASE_URL` | same as the development database (lets the test guard refuse it) |

Then:

```powershell
.venv\Scripts\oss-health.exe db upgrade
.venv\Scripts\oss-health.exe db bootstrap-roles   # needs OSS_HEALTH_APP_PASSWORD / OSS_HEALTH_READER_PASSWORD
```

## 2. Run the pipeline

```powershell
.venv\Scripts\oss-health.exe check-config                # no network, no database
scripts\run_pipeline.ps1 --dry-run --repo psf/requests     # GitHub only; no database writes
scripts\run_pipeline.ps1 --repo psf/requests               # load one repository
scripts\run_pipeline.ps1                                   # all configured repositories
scripts\run_pipeline.ps1 --full-refresh                    # re-backfill window + all open items
```

Without `GITHUB_TOKEN`, GitHub allows 60 requests/hour. One `psf/requests` backfill used 10.

**Outputs:**
- `backend/reports/latest_quality_report.json`, plus `quality_report_<run_id>.json`
- `backend/logs/pipeline_YYYYMMDD.log` (JSON lines)
- the database tables `oss_health.pipeline_runs`, `data_quality_checks` and `rejected_records`

Inspect history with SQL, e.g.
`SELECT status, started_at, records_extracted, records_rejected FROM analytics.fact_pipeline_run ORDER BY started_at_utc DESC;`

## 3. Run the API and dashboard locally

```powershell
cd backend
..\.venv\Scripts\uvicorn.exe oss_health.api.app:app --port 8000      # API_DATABASE_URL, CORS_ALLOWED_ORIGINS from env
cd ..\frontend
npm run dev                                                          # http://localhost:5173 → http://localhost:8000
```

Set `CORS_ALLOWED_ORIGINS=http://localhost:5173` for local use.

## 4. Tests and verification

```powershell
scripts\test.ps1          # everything (integration tests run only with a verified *_test DB)
scripts\verify.ps1        # + environment, deps, config, migration status, report structure, gitleaks
scripts\verify.ps1 -SkipTests
```

Individual suites:

```powershell
cd backend;  ..\.venv\Scripts\python.exe -m pytest tests/unit -q
cd backend;  ..\.venv\Scripts\python.exe -m pytest tests/integration -q   # TEST_DATABASE_URL required
cd frontend; npm run typecheck; npm test; npm run build; npm run check:dist
```

**Integration-test safety.** The suite refuses to run unless the database name ends in `_test`, the
host is local, the URL differs from every other configured database URL, and the server-reported
`current_database()` matches.

## 5. Live verification checklist

Run after each production deploy, then record the results in `docs/DEVELOPMENT_LOG.md`.

| # | Check | How |
|---|-------|-----|
| 1 | Dashboard loads without sign-in | Open the Vercel URL in a private window |
| 2 | Dashboard calls the Cloud Run API, not localhost | DevTools → Network: requests go to `https://…run.app/api/v1/…` |
| 3 | API is public and read-only | `curl -s $API/health/ready`; `curl -s -o /dev/null -w '%{http_code}' -X POST $API/api/v1/runs` → 405 |
| 4 | API reaches Neon as the reader | `/health/ready` → `{"status":"ok","database":"ok"}` |
| 5 | Invalid/oversized requests rejected | `$API/api/v1/repositories?limit=5000` → 422; long query string → 414 |
| 6 | Real metrics and freshness shown | Overview KPIs and the "Last successful ingestion" banner match `/api/v1/summary` |
| 7 | Manual ingestion updates Neon | Actions → Ingest → Run workflow; then a new row appears in `/api/v1/runs` |
| 8 | Schedule configured | `ingest.yml` cron `17 2 * * *`; Actions tab shows the next scheduled run |
| 9 | Unchanged rerun creates no duplicates | Run ingestion twice; the second run shows inserted = 0 for unchanged data and row counts are unchanged |
| 10 | Missing ≠ zero; failures not green | Repository detail shows "—" before data exists; a partial run shows an amber badge |
| 11 | Works with laptop off | Check from a phone on mobile data with the laptop shut down |
| 12 | No secrets public | `npm run check:dist` on the deployed assets; gitleaks on the repo; inspect API responses |
| 13 | SPA routes survive refresh | Load `/repositories/<id>` and `/quality` directly |
| 14 | Mobile and desktop layouts | 390×844, 820×1180 and 1440×900 viewports; then a physical phone (record which) |

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `invalid value for parameter "TimeZone": "UTC"` with the local pgserver | Re-run `scripts\local_postgres.ps1`; it installs the missing time-zone files into the pgserver package |
| `Database URL could not be parsed` / `contains whitespace` | Run `oss-health db check-url --env <VARIABLE>`. It prints only yes/no flags (quotes, `KEY=` prefix, whitespace, scheme). Store the bare `postgresql://…` URL. |
| Integration tests skipped | `TEST_DATABASE_URL` is not set; the tests intentionally skip |
| `UnsafeDatabaseTargetError` | The test URL is not a local `*_test` database, or equals another configured URL. This is intended. |
| `exit 3` lock message | A run is in progress, or a crashed session still holds the lock; it clears when that session ends |
| Scheduled ingestion stopped | GitHub disables schedules after 60 days without repository activity. Use Actions → workflow → **Enable workflow**, or `gh workflow enable ingest.yml`, and check `gh workflow list --all`. |
| CORS errors in the browser | `CORS_ALLOWED_ORIGINS` must exactly match the dashboard origin (scheme + host, no trailing slash) |
