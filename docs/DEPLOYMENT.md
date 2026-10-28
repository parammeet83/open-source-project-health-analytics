# Deployment Guide

**Status (2026-10-28):** deployment files are complete and tested locally, but **nothing is deployed
yet**. Every remaining step needs an account login, a credential entry or a billing decision by the
owner. Those steps are collected in [Owner actions required](#owner-actions-required). This document
records only what was actually done. URLs will be added after they have been verified.

## Target topology

| Component | Provider | Region | Plan |
|-----------|----------|--------|------|
| Dashboard (static SPA) | Vercel | Global edge | Hobby |
| API (`oss-health-api`) | Google Cloud Run | `asia-southeast1` (Singapore) | Request-based billing, min 0 / max 3 instances |
| Container images | Google Artifact Registry | `asia-southeast1` | Docker repository `oss-health` |
| API database secret | Google Secret Manager | `asia-southeast1` (user-managed replication) | 1 secret |
| Database | Neon PostgreSQL 17 | `aws-ap-southeast-1` (Singapore) | Free |
| CI, ingestion, deploys | GitHub Actions | GitHub-hosted `ubuntu-latest` | Public repository |

**Region choice.** Neon's only Asia-Pacific regions are Singapore and Sydney; there is no Mumbai
region. The API issues several SQL queries per request, so API-to-database latency matters most.
Placing Cloud Run in Singapore next to Neon keeps that hop short. India-based visitors reach
Singapore with modest latency.

## Cost assumptions (checked against official pages on 2026-10-28)

| Service | What the official page says | Expected use |
|---------|-----------------------------|--------------|
| Cloud Run (Always Free) | 2 M requests, 360,000 GB-s memory, 180,000 vCPU-s per month; 1 GB outbound transfer **from North America**. **A billing account is required.** | Far below the compute limits. Responses are small JSON, but egress from Singapore is not covered by the North-America allowance, so a few cents of egress are possible. |
| Artifact Registry | 0.5 GB storage free | One ~150–200 MB image per deploy. Keep ≤ 2 images (cleanup policy below). |
| Secret Manager | 6 active secret versions, 10,000 access operations free | 1 secret, accessed at instance start-up |
| Cloud Build | 2,500 build-minutes (e2-standard-2) free | Not used by the workflow (images are built on the GitHub runner). Used only by the optional `scripts/deploy_cloud_run.ps1`. |
| Cloud Logging | 50 GiB per project free | Access logs are disabled in Uvicorn; small volume |
| Neon Free | 100 CU-hours of compute per project; 1 GB storage per project (20 GB total); scale to zero after 5 min (always on); no credit card | Daily ingestion of a few minutes plus API queries. Scale-to-zero means a cold start of a few hundred ms. |
| Vercel Hobby | $0; 100 GB Fast Data Transfer; for "personal, non-commercial use" | A personal portfolio site fits the Hobby terms |
| GitHub Actions | Standard runners free for public repositories | CI, daily ingestion, deploys |

**Important:**
- A free tier is not a spending cap.
- A Google Cloud budget alert only notifies; it does **not** stop charges.
- `--max-instances 3` and `--min-instances 0` bound how much Cloud Run can scale.
- None of the paid or billing actions below have been performed.

## One-time setup (owner + assistant)

The commands below avoid putting secrets on the command line. Values are read with prompts or from
standard input. Never paste secrets into chat or commit them.

### 1. GitHub repository

```powershell
winget install --id GitHub.cli        # if gh is not installed
gh auth login                          # owner action (browser login)
gh repo create open-source-project-health-analytics --public --source . --remote origin --push
gh api -X PUT repos/{owner}/open-source-project-health-analytics/environments/production
```

### 2. Neon

1. Create a Neon account and a **new** project named `open-source-project-health-analytics`. Use
   Postgres 17, region **AWS Asia Pacific (Singapore)**, and database `oss_health`. Do not reuse any
   other project.
2. Copy the **direct (non-pooled)** connection string of the project owner role into your local `.env`
   as `MIGRATION_DATABASE_URL`. Keep `sslmode=require`.
3. Put two strong random passwords into `.env` as `OSS_HEALTH_APP_PASSWORD` and
   `OSS_HEALTH_READER_PASSWORD`. For example, generate one with
   `.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"` and paste the
   output into `.env` yourself.
4. Create the schema and the least-privilege roles:

   ```powershell
   .venv\Scripts\oss-health.exe db upgrade
   .venv\Scripts\oss-health.exe db bootstrap-roles   # creates oss_health_app + oss_health_reader via SQL, applies grants, verifies
   ```

   The roles are created with SQL, so they are **not** members of `neon_superuser` (roles created in
   the Neon console are). `db verify-roles` checks for that.
5. Build the two runtime URLs from the same host. Use `oss_health_app` for `PIPELINE_DATABASE_URL`
   and `oss_health_reader` for `API_DATABASE_URL`, with `?sslmode=require`.

### 3. GitHub environment `production`

```powershell
# Each command reads the value from stdin, so it never appears in shell history.
Get-Content .env | Select-String '^PIPELINE_DATABASE_URL=' | ForEach-Object { $_.Line.Split('=',2)[1] } | gh secret set PIPELINE_DATABASE_URL --env production
Get-Content .env | Select-String '^MIGRATION_DATABASE_URL=' | ForEach-Object { $_.Line.Split('=',2)[1] } | gh secret set MIGRATION_DATABASE_URL --env production
# Optional: GH_API_TOKEN (fine-grained PAT, public read-only). Without it the job's built-in token is used.
```

Variables (not secrets): `GCP_PROJECT_ID`, `GCP_REGION=asia-southeast1`,
`GCP_WORKLOAD_IDENTITY_PROVIDER`, `GCP_DEPLOYER_SERVICE_ACCOUNT`, `GCP_RUNTIME_SERVICE_ACCOUNT`,
`CORS_ALLOWED_ORIGINS`, and optionally `CORS_ALLOW_ORIGIN_REGEX` (anchored, https-only), e.g.
`^https://open-source-project-health-analytics-[a-z0-9-]+-<team>\.vercel\.app$` for previews.

### 4. Google Cloud (requires billing approval)

```bash
PROJECT_ID=oss-health-analytics-<suffix>     # new, dedicated project
REGION=asia-southeast1
REPO_SLUG=<github-owner>/open-source-project-health-analytics

gcloud projects create "$PROJECT_ID"
gcloud billing projects link "$PROJECT_ID" --billing-account=<BILLING_ACCOUNT_ID>   # owner approval
gcloud config set project "$PROJECT_ID"
gcloud services enable run.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com iamcredentials.googleapis.com sts.googleapis.com

gcloud artifacts repositories create oss-health --repository-format=docker --location="$REGION"
# Keep at most two images so storage stays inside the 0.5 GB free allowance.
cat > policy.json <<'JSON'
[{"name":"keep-2","action":{"type":"Keep"},"mostRecentVersions":{"keepCount":2}},
 {"name":"delete-old","action":{"type":"Delete"},"condition":{"olderThan":"1d"}}]
JSON
gcloud artifacts repositories set-cleanup-policies oss-health --location="$REGION" --policy=policy.json

# Service accounts: runtime (no roles except reading one secret) and deployer (used via OIDC).
gcloud iam service-accounts create oss-health-api-runtime --display-name="OSS Health API runtime"
gcloud iam service-accounts create oss-health-deployer --display-name="OSS Health GitHub deployer"
RUNTIME=oss-health-api-runtime@$PROJECT_ID.iam.gserviceaccount.com
DEPLOYER=oss-health-deployer@$PROJECT_ID.iam.gserviceaccount.com

# API database secret: read from a prompt, never from argv.
read -rs API_DB_URL && printf '%s' "$API_DB_URL" | gcloud secrets create oss-health-api-database-url \
  --replication-policy=user-managed --locations="$REGION" --data-file=- && unset API_DB_URL
gcloud secrets add-iam-policy-binding oss-health-api-database-url \
  --member="serviceAccount:$RUNTIME" --role=roles/secretmanager.secretAccessor

# Deployer: deploy Cloud Run, push images, act as the runtime SA. Nothing else.
gcloud projects add-iam-policy-binding "$PROJECT_ID" --member="serviceAccount:$DEPLOYER" --role=roles/run.admin
gcloud artifacts repositories add-iam-policy-binding oss-health --location="$REGION" \
  --member="serviceAccount:$DEPLOYER" --role=roles/artifactregistry.writer
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME" \
  --member="serviceAccount:$DEPLOYER" --role=roles/iam.serviceAccountUser

# Workload Identity Federation: only this repository's main branch may impersonate the deployer.
gcloud iam workload-identity-pools create github --location=global --display-name="GitHub Actions"
gcloud iam workload-identity-pools providers create-oidc github-oidc --location=global \
  --workload-identity-pool=github --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" \
  --attribute-condition="assertion.repository=='$REPO_SLUG' && assertion.ref=='refs/heads/main'"
NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
gcloud iam service-accounts add-iam-policy-binding "$DEPLOYER" --role=roles/iam.workloadIdentityUser \
  --member="principalSet://iam.googleapis.com/projects/$NUMBER/locations/global/workloadIdentityPools/github/attribute.repository/$REPO_SLUG"
echo "GCP_WORKLOAD_IDENTITY_PROVIDER=projects/$NUMBER/locations/global/workloadIdentityPools/github/providers/github-oidc"
```

Optional: create a budget alert, e.g. USD 1 at 50/90/100 %, in Billing → Budgets & alerts. It only
notifies.

### 5. Vercel

1. Create or log in to a Vercel account and import the GitHub repository.
2. Set **Root Directory** = `frontend`. `vercel.json` sets the Vite framework, `npm ci`,
   `npm run build`, output `dist`, and the SPA fallback.
3. Set the environment variable `VITE_API_BASE_URL` = the Cloud Run URL (Production and Preview).
   It is public by design.
4. Deploy, then put the production origin (e.g. `https://<project>.vercel.app`) into the GitHub
   variable `CORS_ALLOWED_ORIGINS` and re-run **Migrate and deploy API**.

## Release order

1. CI green on `main`.
2. **Migrate and deploy API** workflow. It runs `db upgrade` + `db verify-roles` with the migration
   secret, then builds the image on the runner, pushes it to Artifact Registry, deploys to Cloud Run
   and smoke-tests `/health/live`, `/health/ready` and POST → 405.
3. **Ingest GitHub data** workflow, run manually. First run the four smaller repositories:
   `pandas-dev/pandas scikit-learn/scikit-learn fastapi/fastapi psf/requests`. Then run without
   inputs to include `microsoft/vscode`.
4. Vercel production deploy with the real API URL. Then restrict CORS (previous section) and redeploy the API.
5. Live verification (checklist in [RUN_AND_VERIFY.md](RUN_AND_VERIFY.md#live-verification-checklist)).

## Rollback

- **API:** `gcloud run services update-traffic oss-health-api --region asia-southeast1 --to-revisions <previous-revision>=100`.
  List revisions with `gcloud run revisions list --service oss-health-api --region asia-southeast1`.
- **Frontend:** Vercel → Deployments → previous deployment → *Promote to Production*.
- **Database:** migrations have working `downgrade()` functions. In production, prefer a
  forward-fixing migration. Neon branches (point-in-time restore within the plan's history window)
  can restore data.
- **Ingestion:** disable the workflow (Actions → Ingest GitHub data → ⋯ → Disable workflow). Data is
  upserted idempotently, so the next successful run self-heals.

## Owner actions required

These are the exact actions that need you. Everything else is ready.

1. **GitHub:** install the GitHub CLI and run `gh auth login`, or create the empty public repository
   `open-source-project-health-analytics` yourself and authorise `git push`. Confirm the GitHub
   username that should own the repository.
2. **Neon:** create the dedicated Neon project (Free plan, Singapore). Put the owner connection
   string and two generated role passwords into your local `.env` (steps 2.2–2.3). Do not share them in chat.
3. **Google Cloud — approval needed:** approve creating a **new dedicated project linked to a billing
   account**, then install the Google Cloud CLI and run `gcloud auth login`. Usage is expected to stay
   within the Always Free allowances, but billing is required, and small charges are possible
   (Singapore egress, image storage above 0.5 GB). Please explicitly approve:
   (a) linking billing to the new project,
   (b) enabling Cloud Run, Artifact Registry, Secret Manager, IAM Credentials and STS APIs,
   (c) one Secret Manager secret,
   (d) a Cloud Run service with max 3 instances in `asia-southeast1`.
4. **Vercel:** log in (Hobby) and import the repository with Root Directory `frontend`, or run
   `npx vercel login` so that I can link it.
