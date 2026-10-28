<#
.SYNOPSIS
  Run every automated check: Ruff, backend unit tests, backend integration tests (only when
  TEST_DATABASE_URL points at a verified *_test database), frontend type check, tests, build and
  bundle scan.
.PARAMETER SkipFrontend
  Skip the frontend checks.
#>
param([switch]$SkipFrontend)
. "$PSScriptRoot\_common.ps1"
Assert-Venv

# Load TEST_DATABASE_URL etc. from .env without printing values.
$envFile = Join-Path $ProjectRoot ".env"
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^\s*(TEST_DATABASE_URL|DEV_DATABASE_URL)\s*=\s*(.+)\s*$' -and -not (Get-Item "Env:$($Matches[1])" -ErrorAction SilentlyContinue)) {
            Set-Item "Env:$($Matches[1])" $Matches[2]
        }
    }
}

Push-Location $BackendDir
try {
    Write-Step "Ruff"
    Invoke-Checked "ruff check" { & $VenvPython -m ruff check . }
    Invoke-Checked "ruff format" { & $VenvPython -m ruff format --check . }
    Write-Step "Backend tests"
    if ($env:TEST_DATABASE_URL) {
        Write-Ok "TEST_DATABASE_URL is set; integration tests verify the target before running"
    } else {
        Write-Warn "TEST_DATABASE_URL is not set; integration tests will be skipped"
    }
    Invoke-Checked "pytest" { & $VenvPython -m pytest -q --cov=oss_health --cov-report=term-missing:skip-covered }
} finally {
    Pop-Location
}

if (-not $SkipFrontend) {
    Push-Location $FrontendDir
    try {
        Write-Step "Frontend type check, tests, build and bundle scan"
        Invoke-Checked "typecheck" { npm run --silent typecheck }
        Invoke-Checked "vitest" { npm test --silent }
        $previous = $env:VITE_API_BASE_URL
        $env:VITE_API_BASE_URL = "https://local-check.example.vercel.app"
        try {
            Invoke-Checked "build" { npm run --silent build }
            Invoke-Checked "bundle scan" { npm run --silent check:dist }
        } finally {
            $env:VITE_API_BASE_URL = $previous
        }
    } finally {
        Pop-Location
    }
}
Write-Ok "All checks passed"
