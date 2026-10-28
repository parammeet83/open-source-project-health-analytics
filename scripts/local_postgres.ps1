<#
.SYNOPSIS
  Optional: start a project-local PostgreSQL (pgserver, installed into .venv only) with the
  dedicated databases osp_health_dev and osp_health_test. Data lives in .pgdata (git-ignored).
  Nothing is dropped or reset; existing databases are left as they are.
#>
. "$PSScriptRoot\_common.ps1"
Assert-Venv

$installed = & $VenvPython -c "import importlib.util as u; print(u.find_spec('pgserver') is not None)"
if ($installed -ne "True") {
    Write-Step "Installing pgserver into .venv (local development tool only)"
    Invoke-Checked "pgserver install" { & $VenvPython -m pip install --quiet pgserver }
}

# Some pgserver Windows builds ship without time-zone data; reuse the tzdata package's files.
$site = & $VenvPython -c "import pgserver, os; print(os.path.dirname(pgserver.__file__))"
$tzTarget = Join-Path $site "pginstall\share\postgresql\timezone"
if (-not (Test-Path $tzTarget)) {
    $tzSource = & $VenvPython -c "import tzdata, os; print(os.path.join(os.path.dirname(tzdata.__file__), 'zoneinfo'))"
    Write-Step "Adding time-zone data to the pgserver install"
    Copy-Item -Recurse $tzSource $tzTarget
    Get-ChildItem $tzTarget -Recurse -Filter "*.py" | Remove-Item
}

$code = @'
import pathlib, sys
import pgserver
srv = pgserver.get_server(pathlib.Path(sys.argv[1]), cleanup_mode=None)
for db in ("osp_health_dev", "osp_health_test"):
    if "1 row" not in srv.psql(f"SELECT 1 FROM pg_database WHERE datname = '{db}';"):
        srv.psql(f"CREATE DATABASE {db};")
uri = srv.get_uri().rsplit("/", 1)[0]
print(uri)
'@
$dataDir = Join-Path $ProjectRoot ".pgdata"
$base = $code | & $VenvPython - $dataDir
if ($LASTEXITCODE -ne 0) { throw "Could not start the local PostgreSQL server." }
Write-Ok "Local PostgreSQL is running (data: .pgdata)"
Write-Host "  Development DB : $base/osp_health_dev"
Write-Host "  Test DB        : $base/osp_health_test"
Write-Host "Put these into .env (MIGRATION_DATABASE_URL / DEV_DATABASE_URL / TEST_DATABASE_URL)."
Write-Host "The port can change after a reboot; re-run this script to see the current one."
