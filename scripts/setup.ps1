<#
.SYNOPSIS
  One-time developer setup: project .venv (Python 3.12), backend packages, frontend packages,
  and a .env created from .env.example ONLY if .env does not exist yet.
#>
. "$PSScriptRoot\_common.ps1"

Write-Step "Project root: $ProjectRoot"

Write-Step "Checking prerequisites"
if (-not (Get-Command py -ErrorAction SilentlyContinue)) { throw "The Python launcher (py) is missing. Install Python 3.12 from python.org." }
& py -3.12 --version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "Python 3.12 is not installed. Install it from python.org." }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw "Node.js/npm is missing. Install Node.js 20 or newer." }
Write-Ok "Python 3.12 and npm found"

if (-not (Test-Path $VenvPython)) {
    Write-Step "Creating .venv with Python 3.12"
    Invoke-Checked "venv creation" { & py -3.12 -m venv (Join-Path $ProjectRoot ".venv") }
}
Assert-Venv
Write-Ok ".venv uses Python 3.12"

Write-Step "Installing backend (pipeline, api, dev extras) into .venv"
Invoke-Checked "pip upgrade" { & $VenvPython -m pip install --quiet --upgrade pip }
Invoke-Checked "backend install" { & $VenvPython -m pip install --quiet -e "$BackendDir[pipeline,api,dev]" }
Write-Ok "Backend installed"

Write-Step "Installing frontend packages (npm ci)"
Push-Location $FrontendDir
try { Invoke-Checked "npm ci" { npm ci --no-fund --no-audit } } finally { Pop-Location }
Write-Ok "Frontend installed"

$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    Write-Ok ".env already exists; left unchanged"
} else {
    Copy-Item (Join-Path $ProjectRoot ".env.example") $envFile
    Write-Warn ".env created from .env.example; fill in your own values (it is git-ignored)"
}

Write-Host ""
Write-Host "Next steps:" -ForegroundColor Cyan
Write-Host "  1. Optional local PostgreSQL:  scripts\local_postgres.ps1"
Write-Host "  2. Check configuration:        .venv\Scripts\oss-health.exe check-config"
Write-Host "  3. Run all tests:              scripts\test.ps1"
