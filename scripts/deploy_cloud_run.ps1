<#
.SYNOPSIS
  Manual alternative to .github/workflows/deploy-cloud-run.yml: deploy the API to Cloud Run from
  source with the gcloud CLI (uses Cloud Build and Artifact Registry in YOUR project).
.DESCRIPTION
  Prerequisites (see docs/DEPLOYMENT.md): gcloud CLI logged in, the dedicated project created and
  billing approved by you, the runtime service account and the Secret Manager secret
  'oss-health-api-database-url' created. Cloud Build/Artifact Registry usage can incur charges
  beyond the free tier, so -ConfirmCosts is required.
.EXAMPLE
  scripts\deploy_cloud_run.ps1 -ProjectId oss-health-analytics -Region asia-southeast1 `
    -RuntimeServiceAccount oss-health-api-runtime@oss-health-analytics.iam.gserviceaccount.com `
    -CorsOrigins https://open-source-project-health-analytics.vercel.app -ConfirmCosts
#>
param(
    [Parameter(Mandatory)] [string]$ProjectId,
    [Parameter(Mandatory)] [string]$Region,
    [Parameter(Mandatory)] [string]$RuntimeServiceAccount,
    [Parameter(Mandatory)] [string]$CorsOrigins,
    [string]$Service = "oss-health-api",
    [switch]$ConfirmCosts
)
. "$PSScriptRoot\_common.ps1"

if (-not $ConfirmCosts) { throw "Pass -ConfirmCosts to acknowledge that Cloud Build/Run usage may be billed." }
if (-not (Get-Command gcloud -ErrorAction SilentlyContinue)) { throw "gcloud CLI not found. Install the Google Cloud CLI first." }
if ($ProjectId -notmatch '^[a-z][a-z0-9-]{4,28}[a-z0-9]$') { throw "Invalid project id." }

$active = gcloud config get-value account 2>$null
if (-not $active) { throw "gcloud is not logged in. Run: gcloud auth login" }
Write-Step "Deploying $Service to project $ProjectId ($Region) as $active"

$envFile = New-TemporaryFile
try {
    "CORS_ALLOWED_ORIGINS: `"$CorsOrigins`"`nRATE_LIMIT_PER_MINUTE: `"120`"`nSTALE_AFTER_HOURS: `"36`"" | Set-Content -Encoding utf8 $envFile
    gcloud run deploy $Service `
        --project $ProjectId --region $Region --source $BackendDir `
        --service-account $RuntimeServiceAccount --allow-unauthenticated `
        --set-secrets "API_DATABASE_URL=oss-health-api-database-url:latest" `
        --env-vars-file $envFile `
        --port 8080 --cpu 1 --memory 512Mi --concurrency 40 `
        --min-instances 0 --max-instances 3 --timeout 30 --cpu-throttling --quiet
    if ($LASTEXITCODE -ne 0) { throw "gcloud run deploy failed (exit $LASTEXITCODE)." }
} finally {
    Remove-Item $envFile -ErrorAction SilentlyContinue
}

$url = gcloud run services describe $Service --project $ProjectId --region $Region --format "value(status.url)"
Write-Ok "Deployed: $url"
foreach ($path in "/health/live", "/health/ready") {
    $response = Invoke-WebRequest -UseBasicParsing "$url$path" -TimeoutSec 30
    Write-Ok "$path -> $($response.StatusCode)"
}
