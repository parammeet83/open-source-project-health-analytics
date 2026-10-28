# Architectural Decision Records

Format: context → decision → alternatives → consequences. Superseded decisions are marked, not deleted.

| ID | Title | Status |
|----|-------|--------|
| ADR-001 | Single orchestration function `run_pipeline()` | Accepted |
| ADR-002 | GitHub REST API (not GraphQL) | Accepted |
| ADR-003 | Separate endpoints for issues and pull requests | Accepted |
| ADR-004 | GitHub numeric IDs as primary keys | Accepted |
| ADR-005 | Current-state tables plus daily snapshots | Accepted |
| ADR-006 | Incremental extraction with watermarks, overlap, and open-item backfill | Accepted (amended) |
| ADR-007 | Idempotent guarded upserts; per-repository transactions | Accepted (amended) |
| ADR-008 | Row-level Pandera validation with quarantine | Accepted |
| ADR-009 | Alembic for tables, views and grants | Accepted |
| ADR-010 | Run tracking in PostgreSQL + JSON report; advisory lock | Accepted |
| ADR-011 | Alerts via logs, report status, exit codes and dashboard status | Accepted (amended) |
| ADR-012 | TOML configuration; secrets only in the environment | Accepted |
| ADR-013 | No Docker in v1 | **Superseded by ADR-021** |
| ADR-014 | Windows Task Scheduler for scheduling | **Superseded by ADR-020** |
| ADR-015 | SQLAlchemy Core with psycopg 3 | Accepted |
| ADR-016 | Power BI Import mode on the analytics schema | Accepted (now optional; see ADR-019) |
| ADR-017 | UTC everywhere | Accepted |
| ADR-018 | Tooling additions (python-dotenv, responses, ruff) | Accepted |
| ADR-019 | Hosted architecture: Vercel + Cloud Run + Neon + GitHub Actions | Accepted; API host amended by ADR-027 |
| ADR-020 | Cloud ingestion with GitHub Actions | Accepted |
| ADR-021 | Container only for the API on Cloud Run | **Superseded by ADR-027** |
| ADR-022 | Three database credentials with SQL-created runtime roles | Accepted |
| ADR-023 | GitHub OIDC → Workload Identity Federation; no service-account keys | **Superseded by ADR-027** (no Google Cloud) |
| ADR-024 | Singapore region pairing | Accepted (Vercel `sin1` + Neon `aws-ap-southeast-1`) |
| ADR-025 | Honest-status UI: missing ≠ zero; partial ≠ healthy | Accepted |
| ADR-026 | Local integration database via pgserver | Accepted |
| ADR-027 | Host the API on Vercel instead of Google Cloud Run | Accepted |

## ADR-001: Single orchestration function `run_pipeline()`

A daily batch over five repositories in a linear flow. `oss_health.pipeline.run_pipeline()` is the
only entry point. The CLI (`oss-health run`) wraps it, and GitHub Actions runs the CLI. It never
raises; the outcome comes back in a `RunResult`. Airflow and Prefect were rejected: they add
infrastructure for a single linear DAG.

## ADR-002: GitHub REST API

REST v3 pinned to `2022-11-28`. GraphQL would save calls only for data that is out of scope
(reviews, diffs).

## ADR-003: Separate endpoints for issues and pull requests

`/issues` returns PRs too. Items with `pull_request` are dropped from issues, and PRs come only from
`/pulls`, which has PR-only fields. A PR's issue ID differs from its PR ID, so the cross-check uses
`(repository_id, number)`. The post-load `issue_pr_number_overlap` check is error-severity.

## ADR-004: GitHub numeric IDs as primary keys

They are stable across renames. `(repository_id, number)` is unique. `full_name` is unique
case-insensitively but mutable.

## ADR-005: Current-state tables plus daily snapshots

Issues and PRs are SCD1. Stars/forks/watchers form a per-UTC-day snapshot table. Reopen history and
past star counts are out of scope and documented as limitations. Snapshots are never fabricated.

## ADR-006: Incremental extraction with watermarks, overlap, and open-item backfill

Per-resource watermarks live in `extraction_watermarks`, together with `coverage_start_at`. Backfill
reads 90 days plus all open items; incremental runs read `watermark − 24 h`.
**Amended in implementation:**
- `/pulls` has no `since`, so it is read in descending `updated` order with an early stop.
- Issue watermarks may advance on a page-capped ascending series. PR watermarks may not.
- Both hold when the open-item series is incomplete or the rejection rate exceeds the threshold.
- Coverage only moves earlier.

## ADR-007: Idempotent guarded upserts; per-repository transactions

`ON CONFLICT DO UPDATE … WHERE guard` with `RETURNING (xmax = 0)`.
**Amended:** a same-day snapshot is replaced only when a metric actually changed, so unchanged
reruns report zero updates. A post-upsert reconciliation (all valid IDs present) runs inside the
transaction. Checks and rejections are written in separate transactions, so they survive rollbacks.

## ADR-008: Row-level Pandera validation with quarantine

Pandera 0.35 (`pandera.pandas`) runs lazily. Frame-level check failures are reported per column, so
they are collapsed to one rejection per row per check, with stable check names. Structural failures
fail the repository. A rejection rate above 5% is an error check that holds the watermark.

## ADR-009: Alembic for tables, views and grants

Raw-SQL migrations give exact control over constraints, partial indexes, generated columns and
`NULLS NOT DISTINCT`. The view SQL and idempotent grants are package files executed by migration
0003 (grants also by `db bootstrap-roles`). CI runs the migrations twice on a fresh PostgreSQL 17.

## ADR-010: Run tracking in PostgreSQL + JSON report; advisory lock

A session-level `pg_try_advisory_lock` is released automatically if the process dies. A stale-run
sweep closes `running` rows older than 6 h. A JSON report is written even for not-started runs. In
the cloud, the database, not the ephemeral runner filesystem, is the record. The report is also
kept as a 30-day artifact.

## ADR-011: Alerts via logs, report status, exit codes and dashboard status

**Amended:**
- In GitHub Actions a nonzero exit fails the workflow run, which notifies the owner through GitHub's
  standard failed-workflow notifications.
- The public dashboard shows `degraded`/`stale` banners.
- Email or Slack integrations remain future work.

## ADR-012: TOML configuration; secrets only in the environment

`backend/config/pipeline.toml` is read with `tomllib`. Secrets come from `.env`, GitHub secrets or
Secret Manager. **Changed:** `require_token` defaults to `false`. A token is recommended, but it is
never required for tests or config checks.

## ADR-013: No Docker in v1 — *Superseded by ADR-021*

## ADR-014: Windows Task Scheduler — *Superseded by ADR-020*

The project must run with the owner's computer off. No local scheduled task is registered.

## ADR-015: SQLAlchemy Core with psycopg 3

Set-based upserts are written explicitly. The ORM was not used.

## ADR-016: Power BI Import mode on the analytics schema

Retained as an optional guide only. The public dashboard is primary. No `.pbix` is shipped.

## ADR-017: UTC everywhere

Sessions use `timezone=UTC`, snapshot dates are UTC dates, and weeks are ISO weeks starting Monday.
The UI labels every date as UTC.

## ADR-018: Tooling additions

python-dotenv, responses, ruff, plus pip-audit, Vitest/Testing Library and Gitleaks for quality gates.

## ADR-019: Hosted architecture: Vercel + Cloud Run + Neon + GitHub Actions

**Context.** The deliverable is one public HTTPS link usable on any device, independent of the
owner's laptop.

**Decision.**
- A static React/TypeScript/Vite SPA on Vercel.
- A read-only FastAPI service on Cloud Run.
- Neon PostgreSQL as the system of record.
- GitHub Actions for CI, ingestion and deploys.

**Alternatives.**
- Render was excluded by requirement.
- A Vercel serverless backend would put database credentials in a second runtime, so it was not used.
- A Power BI public embed requires licensing.
- Streamlit would mean a second app.

**Consequences.** Three providers to configure. Each piece stays small and replaceable, and the
public surface is read-only.

## ADR-020: Cloud ingestion with GitHub Actions

The `schedule` (02:17 UTC) and `workflow_dispatch` triggers run the same CLI. `concurrency` and the
DB lock prevent overlaps. Minimal permissions apply. Secrets live in the `production` environment.
The default GitHub token is the built-in job token, and an optional PAT can be added.
**Trade-off:** schedules in public repositories are disabled after 60 days without repository
activity (documented, with re-enable steps). The dashboard turns `stale` after 36 h.

## ADR-021: Container only for the API on Cloud Run

Cloud Run needs a container. `backend/Dockerfile` is a two-stage slim image with only the `[api]`
extra (no pandas or Pandera) and runs as a non-root user. Images are built on the GitHub runner and
pushed to Artifact Registry, which avoids Cloud Build. The pipeline itself does not run in a container.

## ADR-022: Three database credentials with SQL-created runtime roles

The migration credential is the Neon owner. `oss_health_app` and `oss_health_reader` are created
with SQL by `db bootstrap-roles` (NOSUPERUSER, NOINHERIT, connection limits, per-role statement
timeouts, reader read-only by default). Roles created in the Neon console are members of
`neon_superuser`, which is too broad for runtime use. Grants are explicit and idempotent, with
default privileges. `db verify-roles` checks the outcome.

## ADR-023: GitHub OIDC → Workload Identity Federation; no service-account keys

The deployer service account is impersonated only by tokens whose `repository` and `ref` claims match
this repository's `main` branch. The runtime service account can only read one secret.

## ADR-024: Singapore region pairing

Neon offers Singapore and Sydney in Asia-Pacific, but no India region. Cloud Run runs in
`asia-southeast1` beside Neon `aws-ap-southeast-1` to minimise API↔DB latency. Mumbai
(`asia-south1`) would be closer to Indian visitors, but each API request would then cross to
Singapore several times.

## ADR-025: Honest-status UI

Every nullable metric renders "—". The views return NULL until a backfill is complete. Weekly charts
start at the coverage start. The freshness banner is green only if the latest finished run succeeded
within 36 h. Partial and failed runs show amber or red badges. The overview's cross-repository
activity includes only weeks covered for every repository.

## ADR-026: Local integration database via pgserver

PostgreSQL was not installed on the development machine, and Docker was unavailable. `pgserver`
(a pip-packaged PostgreSQL 16.2) is installed only into the project `.venv`, with data in `.pgdata`.
Its Windows build lacks time-zone files; `scripts/local_postgres.ps1` copies them from the `tzdata`
package. CI uses a real PostgreSQL 17 service container, so version-specific behaviour is still
covered there.

## ADR-027: Host the API on Vercel instead of Google Cloud Run

**Context.** Google Cloud Run requires a billing-enabled project even within the Always Free tier.
The owner did not approve any charges or billing setup. Vercel's Python runtime now supports
FastAPI natively, and the Hobby plan pauses rather than bills when its limits are reached.

**Decision.**
- Deploy the read-only API as a separate Vercel Hobby project (`oss-health-api`, Root Directory
  `backend`). Its entrypoint is `backend/index.py`, which re-exports `oss_health.api.app:app`.
- FastAPI moves into the core `[project.dependencies]`, because Vercel installs only those.
- The function region is `sin1`, next to Neon Singapore.
- `API_DATABASE_URL` (reader role) is a sensitive Vercel environment variable.
- The dashboard stays a separate Vercel project (`oss-health-dashboard`, Root Directory `frontend`).
- Removed: the Cloud Run deploy workflow, the Dockerfile and the deploy script.
- Migrations now run in their own `migrate.yml` workflow, independent of the deployments.

**Alternatives.**
- Cloud Run: needs billing approval.
- Vercel Services in a single project: possible, but two projects keep the dashboard deployable
  independently and match the requested layout.
- Serving the SPA from FastAPI: couples the two releases.

**Consequences.**
- No Google Cloud resources and no billing exposure.
- Cold starts on the serverless function; rate limiting is per instance only (documented in SECURITY.md).
- Connection pools are kept to 1 + 1 per instance.
- The API's in-memory rate limiter uses Vercel's `x-vercel-forwarded-for`.
