# Implementation Plan and Status

**Updated:** 2026-10-28. Status values are evidence-based. "Done" means implemented **and** verified
as described. Details are in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

| Phase | Scope | Status | Evidence |
|-------|-------|--------|----------|
| 1 Inspect & reconcile | Read the design docs; adopt Vercel + Cloud Run + Neon + GitHub Actions | Done | ADR-019…025; all docs revised |
| 2 Foundation | `.venv` (3.12), `pyproject.toml` with extras, config, logging, CLI, scripts, `.gitignore`, `.env.example` | Done | `scripts\setup.ps1`, `check-config` |
| 3 Database | Alembic 0001–0003, constraints, indexes, roles and grants, test-DB guard | Done | Migration round trip; constraint and permission tests |
| 4 Extraction | Client, pagination, retries, rate limits, windows, open-item backfill, completeness | Done | 24 client + 12 extract/transform tests; live run on psf/requests |
| 5 Transform & validate | pandas, Pandera, quarantine, dedup, checks | Done | Unit + integration tests |
| 6 Load & orchestrate | Guarded upserts, transactions, lock, stale sweep, watermarks, report, exit codes | Done | End-to-end integration tests; live run + idempotent rerun |
| 7 Analytics | 8 views, coverage handling, null-not-zero | Done | View tests with hand-computed expectations |
| 8 API | FastAPI read-only, validation, CORS, limits, Dockerfile | Done (local) | 36 API unit tests; API-only install served live data as the reader |
| 9 Frontend | 7 pages, responsive, states, tests, `vercel.json` | Done (local) | 25 Vitest tests; 390/820/1440 px screenshots, no overflow |
| 10 CI & automation | `ci.yml`, `ingest.yml`, `deploy-cloud-run.yml`, WIF guide | Written; **not yet executed on GitHub** | Repository not published yet |
| 11 Local verification | Full suites, live ingestion, idempotency, audits, secret scan | Done | 148 backend + 25 frontend tests; pip-audit/npm audit clean; gitleaks clean |
| 12 Publication | Dedicated public GitHub repository | **Blocked: GitHub authentication** | `gh` not installed or authenticated on this machine |
| 13 Cloud deployment | Neon, Cloud Run, Vercel, secrets | **Blocked: owner accounts and billing approval** | [DEPLOYMENT.md → Owner actions](DEPLOYMENT.md#owner-actions-required) |
| 14 Live verification | Public URL checks, cloud ingestion, devices | Not started (depends on 12–13) | Checklist in [RUN_AND_VERIFY.md](RUN_AND_VERIFY.md#5-live-verification-checklist) |
| 15 Release docs | README, guides | Done for the current state; URLs to be added after verification | — |

## Remaining work after the owner actions

1. Push the reviewed repository and confirm CI is green (expect PostgreSQL 17 + Docker build jobs to
   run for the first time).
2. Neon: `db upgrade`, `db bootstrap-roles`, and set the GitHub environment secrets.
3. GCP: run the project / Artifact Registry / Secret Manager / WIF setup, then the **Migrate and deploy API** workflow.
4. Ingestion: first the four smaller repositories, then all five including `microsoft/vscode`. Check
   the completeness checks and the page counts for vscode.
5. Vercel import with `VITE_API_BASE_URL`; restrict CORS; redeploy the API.
6. Live verification checklist, then record the results and the URLs in the README and DEVELOPMENT_LOG.
