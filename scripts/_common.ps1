# Shared helpers. Dot-source from the other scripts: . "$PSScriptRoot\_common.ps1"
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BackendDir = Join-Path $ProjectRoot "backend"
$FrontendDir = Join-Path $ProjectRoot "frontend"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$OssHealth = Join-Path $ProjectRoot ".venv\Scripts\oss-health.exe"

if (-not (Test-Path (Join-Path $BackendDir "pyproject.toml")) -or -not (Test-Path (Join-Path $FrontendDir "package.json"))) {
    throw "Project layout not found under $ProjectRoot; refusing to continue."
}

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "    OK  $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "    !!  $Message" -ForegroundColor Yellow }

function Assert-Venv {
    if (-not (Test-Path $VenvPython)) {
        throw "The project virtual environment is missing. Run scripts\setup.ps1 first."
    }
    $version = & $VenvPython -c "import sys; print('%d.%d' % sys.version_info[:2])"
    if ($version -ne "3.12") { throw ".venv uses Python $version; Python 3.12 is required." }
}

function Invoke-Checked([string]$Label, [scriptblock]$Block) {
    & $Block
    if ($LASTEXITCODE -ne 0) { throw "$Label failed (exit code $LASTEXITCODE)." }
}
