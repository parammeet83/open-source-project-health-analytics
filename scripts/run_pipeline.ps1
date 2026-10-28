<#
.SYNOPSIS
  Run the ingestion pipeline locally (same run_pipeline() as the GitHub Actions workflow).
.EXAMPLE
  scripts\run_pipeline.ps1 --repo psf/requests
  scripts\run_pipeline.ps1 --dry-run
.NOTES
  Reads GITHUB_TOKEN / PIPELINE_DATABASE_URL from .env. Exit codes: 0 success,
  2 partial_success, 1 failed, 3 not started.
#>
. "$PSScriptRoot\_common.ps1"
Assert-Venv
if (-not (Test-Path $OssHealth)) { throw "oss-health CLI not found in .venv; run scripts\setup.ps1." }

Push-Location $BackendDir
try {
    $pipelineArgs = $args
    $code = Invoke-Native { & $OssHealth run @pipelineArgs }
} finally {
    Pop-Location
}
switch ($code) {
    0 { Write-Ok "Pipeline succeeded" }
    2 { Write-Warn "Pipeline finished with partial_success; see backend\reports\latest_quality_report.json" }
    3 { Write-Warn "Pipeline did not start (configuration, database or lock problem)" }
    default { Write-Warn "Pipeline failed (exit code $code); see the log above and the quality report" }
}
exit $code
