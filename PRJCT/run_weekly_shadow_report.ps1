param(
    [Parameter(Mandatory = $true)][string]$MainRunDir,
    [Parameter(Mandatory = $true)][string]$IbkrRunDir,
    [string]$ProjectRoot = "C:\aiinvest",
    [string]$OutputDir = "",
    [ValidateSet("daily", "final", "manual")][string]$Kind = "manual",
    [string]$Label = "",
    [string]$FromIso = "",
    [string]$ToIso = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$layoutHelper = Join-Path $ScriptDir "resolve_aiinvest_layout.ps1"
if (Test-Path $layoutHelper) { . $layoutHelper }
$repoRoot = if (Test-Path (Join-Path $ProjectRoot "PRJCT")) { $ProjectRoot } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "PRJCT")) { Split-Path -Parent $ScriptDir } else { $ProjectRoot }
if (Get-Command Get-AIInvestLayout -ErrorAction SilentlyContinue) {
    $layout = Get-AIInvestLayout -RepoRoot $repoRoot
    $repoRoot = $layout.RepoRoot
    $projectDir = $layout.ProjectDir
    $reportsRootBase = $layout.ReportsDir
} else {
    $projectDir = if (Test-Path (Join-Path $repoRoot "PRJCT")) { Join-Path $repoRoot "PRJCT" } else { $ScriptDir }
    $reportsRootBase = if (Test-Path (Join-Path $repoRoot "RPRTS")) { Join-Path $repoRoot "RPRTS" } else { $repoRoot }
}
$py = Join-Path $projectDir "python-core\venv\Scripts\python.exe"
$job = Join-Path $projectDir "python-core\weekly_shadow_report.py"

if (-not (Test-Path $py)) { throw "Python venv not found: $py" }
if (-not (Test-Path $job)) { throw "Report job not found: $job" }

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $reportsRootBase "_shadow-reports\weekly"
}
if ([string]::IsNullOrWhiteSpace($Label)) {
    $Label = "weekly-" + (Get-Date -Format "yyyyMMdd")
}

$args = @(
    $job,
    "--main-run-dir", $MainRunDir,
    "--ibkr-run-dir", $IbkrRunDir,
    "--output-dir", $OutputDir,
    "--kind", $Kind,
    "--label", $Label
)

if (-not [string]::IsNullOrWhiteSpace($FromIso)) {
    $args += @("--from-iso", $FromIso)
}
if (-not [string]::IsNullOrWhiteSpace($ToIso)) {
    $args += @("--to-iso", $ToIso)
}

& $py @args
