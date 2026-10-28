<#
.SYNOPSIS
  Non-destructive verification of the whole project: environment, dependencies, configuration,
  migration status, tests, latest quality report, configuration consistency and secret scan.
.PARAMETER SkipTests
  Skip scripts\test.ps1 (fast mode).
#>
param([switch]$SkipTests)
. "$PSScriptRoot\_common.ps1"
$failures = New-Object System.Collections.Generic.List[string]
function Fail([string]$Message) { $failures.Add($Message); Write-Warn $Message }

Write-Step "Environment"
try { Assert-Venv; Write-Ok ".venv with Python 3.12" } catch { Fail $_.Exception.Message }
if (Get-Command node -ErrorAction SilentlyContinue) { Write-Ok "Node $(node --version)" } else { Fail "Node.js is not installed" }

Write-Step "Dependencies"
if ((Invoke-Native { & $VenvPython -m pip check } -Quiet) -eq 0) { Write-Ok "pip check: no broken requirements" } else { Fail "pip check reported problems" }
if (Test-Path (Join-Path $FrontendDir "node_modules")) { Write-Ok "frontend node_modules present" } else { Fail "frontend dependencies missing (scripts\setup.ps1)" }

Write-Step "Configuration"
Push-Location $BackendDir
try {
    if ((Invoke-Native { & $OssHealth check-config } -Quiet) -eq 0) { Write-Ok "backend/config/pipeline.toml is valid" } else { Fail "pipeline configuration is invalid" }
} finally { Pop-Location }

Write-Step "Configuration consistency"
$frontendEnv = Get-ChildItem $FrontendDir -Filter ".env*" -Force -ErrorAction SilentlyContinue
foreach ($file in $frontendEnv) {
    $content = Get-Content $file.FullName -Raw
    if ($content -match '(?im)^VITE_[A-Z_]*(SECRET|TOKEN|PASSWORD|DATABASE)') { Fail "$($file.Name) defines a secret-looking VITE_ variable" }
    if ($content -match 'postgres(ql)?://') { Fail "$($file.Name) contains a database URL" }
}
Write-Ok "frontend env files contain no secrets"
$tracked = @()
if (Test-Path (Join-Path $ProjectRoot ".git")) {
    $tracked = git -C $ProjectRoot ls-files
    if ($tracked | Where-Object { $_ -match '(^|/)\.env$' }) { Fail "A real .env file is tracked by git" } else { Write-Ok "no .env tracked by git" }
}

Write-Step "Migration status (read-only)"
if ($env:MIGRATION_DATABASE_URL) {
    Push-Location $BackendDir
    try {
        $previous = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $current = (& (Join-Path $ProjectRoot ".venv\Scripts\alembic.exe") current 2>&1 | ForEach-Object { "$_" }) -join " " }
        finally { $ErrorActionPreference = $previous }
        if ($current -match "\(head\)") { Write-Ok "database is at the latest migration" } else { Fail "database is not at head: $current" }
    } finally { Pop-Location }
} else {
    Write-Warn "MIGRATION_DATABASE_URL not set; migration status not checked"
}

if (-not $SkipTests) {
    Write-Step "Tests"
    & (Join-Path $PSScriptRoot "test.ps1")
    if ($LASTEXITCODE -ne 0 -and $null -ne $LASTEXITCODE) { Fail "scripts\test.ps1 failed" }
}

Write-Step "Latest quality report"
$report = Join-Path $BackendDir "reports\latest_quality_report.json"
if (Test-Path $report) {
    $json = Get-Content $report -Raw | ConvertFrom-Json
    $missing = @("run_id", "status", "exit_code", "started_at", "finished_at", "totals", "checks", "failed_checks", "errors") |
        Where-Object { -not ($json.PSObject.Properties.Name -contains $_) }
    if ($missing) { Fail "report is missing fields: $($missing -join ', ')" } else { Write-Ok "report structure OK (status: $($json.status))" }
} else {
    Write-Warn "no local quality report yet (run scripts\run_pipeline.ps1 --dry-run)"
}

Write-Step "Secret scan"
if (Get-Command gitleaks -ErrorAction SilentlyContinue) {
    if (Test-Path (Join-Path $ProjectRoot ".git")) {
        $code = Invoke-Native { gitleaks git --redact --no-banner $ProjectRoot } -Quiet
        if ($code -eq 0) { Write-Ok "gitleaks: no leaks in git history" } else { Fail "gitleaks found potential secrets in git history" }
    }
    $code = Invoke-Native { gitleaks dir --redact --no-banner (Join-Path $ProjectRoot "backend\src") } -Quiet
    if ($code -eq 0) { Write-Ok "gitleaks: backend sources clean" } else { Fail "gitleaks flagged backend sources" }
} else {
    Write-Warn "gitleaks not installed; secret scan skipped"
}

Write-Host ""
if ($failures.Count) {
    Write-Host "Verification FAILED ($($failures.Count) problem(s))." -ForegroundColor Red
    exit 1
}
Write-Host "Verification passed." -ForegroundColor Green
exit 0
