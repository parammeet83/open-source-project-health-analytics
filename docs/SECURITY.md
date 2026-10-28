# Security Model

## Principles

1. **Public means read-only.** Anonymous visitors can only read curated analytics. Ingestion,
   migrations and administration happen only in authenticated GitHub Actions jobs or on the owner's
   machine.
2. **Least privilege at every boundary.** Three database credentials, each used by exactly one
   component: migrations, ingestion, or the public API.
3. **No secrets in code, bundles, logs, reports or chat.**

## Database roles

| Role | Used by | Can | Cannot |
|------|---------|-----|--------|
| Neon project owner (migration credential) | `oss-health db upgrade`, `db bootstrap-roles`, `migrate.yml` workflow | Own schemas, run Alembic, create the runtime roles, grant privileges | It is never used by the pipeline or the API |
| `oss_health_app` | Ingestion (`PIPELINE_DATABASE_URL`) | `SELECT/INSERT/UPDATE` on `oss_health` tables; `DELETE` on `rejected_records` only (retention); use sequences; advisory locks | Read `analytics`, delete or truncate data tables, run DDL; connection limit 5 |
| `oss_health_reader` | API, optional Power BI (`API_DATABASE_URL`) | `SELECT` on the eight `analytics` views | Read `oss_health` tables, write anything, run DDL; sessions default to read-only; 10 s statement timeout; connection limit 10 |

The runtime roles are created with SQL (`NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION
NOBYPASSRLS`). Unlike roles created in the Neon console, they are not members of `neon_superuser`.
`oss-health db verify-roles` checks role attributes, memberships and six concrete privilege
expectations. Integration tests (`tests/integration/test_schema_views_api.py::test_role_permissions`)
prove the denials against a real PostgreSQL.

Every remote connection uses TLS: `sslmode=require` is added automatically, and weaker modes are
rejected. The API additionally sets `default_transaction_read_only=on` and a 5-second statement
timeout on its sessions.

## Public API

- **Only GET/HEAD/OPTIONS.** Any other method returns 405 before routing. No route runs the
  pipeline, executes SQL, changes watermarks or runs migrations (asserted by unit tests).
- **Validation and bounds:**
  - `limit` ≤ 100 and `offset` ≤ 10,000
  - `weeks` ≤ 104, `days` 7–365, date ranges ≤ 730 days
  - search/language text matched against strict patterns
  - the sort column comes from an allow-list
  - query strings over 1 KB are rejected (414)
  - every SQL statement is parameterized
- **Errors:** generic JSON (`{"error": "internal_error"}`, `database_unavailable`, `not_found`, …).
  Stack traces, SQL text and configuration are never returned. Logs are redacted.
- **CORS:** exact origins from `CORS_ALLOWED_ORIGINS`, plus an optional anchored, https-only
  `CORS_ALLOW_ORIGIN_REGEX` for Vercel previews. Credentials are disabled and only GET is allowed.
- **Headers:** `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`,
  `Referrer-Policy: no-referrer`, HSTS, and short public caching for API responses. The dashboard
  is served with a CSP whose `connect-src` allows only `https://oss-health-api.vercel.app`.
- **Abuse protection (honest limits):**
  - A 120-requests/minute fixed-window limiter per client IP, taken from Vercel's
    `x-vercel-forwarded-for`, which clients cannot set.
  - It lives **in the memory of each Vercel Function instance**, so it is *not* a reliable global
    limit across instances or cold starts.
  - Other ceilings: a 30 s function duration, one pooled DB connection (+1 overflow) per instance,
    the reader role's 10-connection limit, 5–10 s statement timeouts, and Vercel's platform DDoS
    mitigation.
  - On the Hobby plan, exhausting included usage pauses the project instead of billing.
  - Vercel WAF custom rules (3 on Hobby) could add stricter rate limiting; they are not configured.

## Secrets handling

| Secret | Where it lives | Never in |
|--------|----------------|----------|
| `MIGRATION_DATABASE_URL`, `PIPELINE_DATABASE_URL` | Local `.env.local` / `.env.roles` (git-ignored); GitHub repository secrets | Repository, logs, reports, Vercel, `VITE_*` |
| `API_DATABASE_URL` | Vercel `oss-health-api` Production env (encrypted, *sensitive*: not readable back) | Repository, frontend project, GitHub |
| `GH_API_TOKEN` (optional) | GitHub repository secret | Anywhere else; the built-in job token is the default |
| `VERCEL_OIDC_TOKEN` (short-lived) | `backend/.env.local`, `frontend/.env.local`, written by `vercel link` | Git (ignored) and uploads (`.vercelignore`); never bundled (not a `VITE_` variable) |

Secret values are only ever piped on **stdin** (`gh secret set`, `vercel env add`) after passing
`oss-health db check-url`-style validation. They are never passed as command arguments.

- **Redaction.** `oss_health.redaction` masks GitHub tokens (`ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_`,
  `github_pat_`), `Bearer`/`token` headers, URL passwords and `password=` pairs, plus every
  registered secret value. It applies to log records (message and arguments), persisted error
  messages, rejected-record payloads and the whole JSON report. Tests feed fake secrets through
  each path.
- **Frontend.** `VITE_API_BASE_URL` is the only build variable and is public. `npm run check:dist`
  fails the build output if it contains database URLs, GitHub tokens, Neon hosts, private keys,
  service-account JSON, server-side variable names or a localhost API URL. CI runs it on every push.
- **Repository.** `.gitignore` excludes `.env*` (except examples), keys, credentials JSON, local
  database data, reports, logs and build output. Gitleaks scans the full history in CI and in
  `scripts/verify.ps1`.

## GitHub Actions hardening

- Every workflow uses `permissions: contents: read`. No cloud credentials exist in GitHub;
  deployments are done by Vercel.
- Database secrets are used only by `ingest.yml` (writer) and `migrate.yml` (owner). Both jobs
  run in the `production` environment and validate the secret before use.
- Untrusted inputs (`workflow_dispatch` repository names) go through environment variables and are
  validated against a strict pattern before use.
- `concurrency` groups prevent overlapping ingestions and migrations. The pipeline also takes a
  PostgreSQL advisory lock that is released automatically if the process dies.

## Dependency hygiene

- `pip-audit` runs in CI (backend). `npm audit --omit=dev --audit-level=moderate` runs in CI (frontend).
- At the time of writing both reported no known vulnerabilities. Upgrades made to get there:
  react-router-dom 7.18.5, vitest 4.1.12 and pytest ≥ 9.0.3.

## Reporting

Please report security issues privately to the repository owner rather than in public issues.
