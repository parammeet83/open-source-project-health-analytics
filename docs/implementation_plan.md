# Implementation Plan and Status

**Updated:** 2026-10-29. Status values are evidence-based. "Done" means implemented **and** verified
as described. Details are in [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md).

| Phase | Scope | Status | Evidence |
|-------|-------|--------|----------|
| 1 Inspect & reconcile | Read the design docs; adopt hosted architecture | Done | ADR-019…027; docs revised |
| 2 Foundation | `.venv` (3.12), `pyproject.toml`, config, logging, CLI, scripts, `.gitignore`, `.env.example` | Done | `scripts\setup.ps1`, `check-config` |
| 3 Database | Alembic 0001–0003 (packaged), constraints, indexes, roles and grants, test-DB guard | Done | Migration round trip; constraint and permission tests |
| 4 Extraction | Client, pagination, retries, rate limits, windows, open-item backfill, completeness | Done | 24 client + 12 extract/transform tests; live ingestion of all 5 repositories |
| 5 Transform & validate | pandas, Pandera, quarantine, dedup, checks | Done | Unit + integration tests |
| 6 Load & orchestrate | Guarded upserts, transactions, lock, stale sweep, watermarks, report, exit codes | Done | Integration tests; idempotent reruns on Neon |
| 7 Analytics | 8 views, coverage handling, null-not-zero | Done | View tests with hand-computed expectations |
| 8 API | FastAPI read-only, validation, CORS, limits; Vercel entrypoint `backend/index.py` | Done (live) | Unit tests; live probes on https://oss-health-api.vercel.app |
| 9 Frontend | 7 pages, responsive, states, tests, `vercel.json` | Done (live) | 25 Vitest tests; live 390/820/1440 px checks on https://oss-health-dashboard-one.vercel.app |
| 10 CI & automation | `ci.yml`, `ingest.yml`, `migrate.yml` | Done | CI green; manual ingestion and migration runs succeeded |
| 11 Local verification | Full suites, audits, secret scan | Done | 195 backend + 25 frontend tests; pip-audit/npm audit clean; gitleaks clean |
| 12 Publication | Public GitHub repository `parammeet83/open-source-project-health-analytics` | Done | Pushed `main` |
| 13 Hosting | Neon Free, Vercel Hobby (API + dashboard), GitHub secrets | Done | [DEPLOYMENT.md](DEPLOYMENT.md) |
| 14 Live verification | Public URLs, read-only behaviour, Neon connectivity, viewports, data refresh | Done | [DEVELOPMENT_LOG.md](DEVELOPMENT_LOG.md) |
| 15 Release docs | README, guides | Done | URLs added after verification |

## Remaining / optional

1. Observe the first **scheduled** ingestion (02:17 UTC) after deployment.
2. Optionally connect the Vercel GitHub integration, so pushes to `main` redeploy both projects
   automatically (see [DEPLOYMENT.md](DEPLOYMENT.md#automatic-deployments-from-github)).
3. Test on a physical phone. Checks so far used emulated browser viewports.
4. Future enhancements are listed in [TRD §12](TRD.md#12-future-enhancements).
