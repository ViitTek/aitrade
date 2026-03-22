param(
    [string]$ProjectRoot = "C:\aiinvest",
    [int]$DurationDays = 7,
    [int]$SampleMinutes = 20,
    [int]$DailyHour = 15,
    [switch]$Headless,
    [switch]$CleanFirst
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = if (Test-Path (Join-Path $ProjectRoot "PRJCT")) { $ProjectRoot } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "PRJCT")) { Split-Path -Parent $ScriptDir } else { $ProjectRoot }
$projectDir = if (Test-Path (Join-Path $repoRoot "PRJCT")) { Join-Path $repoRoot "PRJCT" } else { $ScriptDir }
$reportsRootBase = if (Test-Path (Join-Path $repoRoot "RPRTS")) { Join-Path $repoRoot "RPRTS" } else { $repoRoot }

$suiteTs = Get-Date -Format "yyyyMMdd-HHmmss"
$suiteRoot = Join-Path (Join-Path $reportsRootBase "_shadow_tests") ("weekly-suite-" + $suiteTs)
$mainRunDir = Join-Path $suiteRoot "bin_krak"
$ibkrRunDir = Join-Path $suiteRoot "ibkr"
$weeklyReportsDir = Join-Path (Join-Path $reportsRootBase "_shadow-reports") ("weekly_" + $suiteTs)

New-Item -ItemType Directory -Path $suiteRoot -Force | Out-Null
New-Item -ItemType Directory -Path $mainRunDir -Force | Out-Null
New-Item -ItemType Directory -Path $ibkrRunDir -Force | Out-Null
New-Item -ItemType Directory -Path $weeklyReportsDir -Force | Out-Null

$runner = Join-Path $projectDir "shadow_trading_test_suite.ps1"
$hourly = Join-Path $projectDir "start_hourly_shadow_reports.ps1"
$scheduler = Join-Path $projectDir "schedule_weekly_shadow_reports.ps1"
$mainHeal = Join-Path $projectDir "start_aiinvest.ps1"
$ibkrHeal = Join-Path $projectDir "start_ibkr_shadow_stack.ps1"

function Start-DetachedPs {
    param(
        [string[]]$Arguments,
        [string]$WorkDir
    )
    $windowStyle = if ($Headless) { "Hidden" } else { "Normal" }
    Start-Process -FilePath "powershell.exe" -ArgumentList $Arguments -WorkingDirectory $WorkDir -WindowStyle $windowStyle -PassThru | Out-Null
}

$commonRunner = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $runner, "-DurationHours", ($DurationDays * 24), "-SampleMinutes", $SampleMinutes)
if ($Headless) {
    $commonRunner += "-Headless"
}

$preStartArgs = @()
if ($Headless) {
    $preStartArgs += "-Headless"
}
if ($CleanFirst) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $mainHeal -CleanFirst @preStartArgs | Out-Null
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $ibkrHeal -CleanFirst @preStartArgs | Out-Null
}

$mainArgs = @($commonRunner + @(
    "-ApiBase", "http://127.0.0.1:8010",
    "-RunDir", $mainRunDir,
    "-HealStack",
    "-HealScript", $mainHeal,
    "-SuiteName", "bin_krak"
))
Start-DetachedPs -Arguments $mainArgs -WorkDir $projectDir

$ibkrArgs = @($commonRunner + @(
    "-ApiBase", "http://127.0.0.1:8110",
    "-RunDir", $ibkrRunDir,
    "-HealStack",
    "-HealScript", $ibkrHeal,
    "-SuiteName", "ibkr"
))
Start-DetachedPs -Arguments $ibkrArgs -WorkDir $projectDir

$mainHourlyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $hourly, "-ProjectRoot", $repoRoot, "-ApiBase", "http://127.0.0.1:8010", "-RunDir", $mainRunDir, "-OutputDir", (Join-Path $weeklyReportsDir "bin_krak"), "-SuiteName", "bin_krak")
$ibkrHourlyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $hourly, "-ProjectRoot", $repoRoot, "-ApiBase", "http://127.0.0.1:8110", "-RunDir", $ibkrRunDir, "-OutputDir", (Join-Path $weeklyReportsDir "ibkr"), "-SuiteName", "ibkr")
$schedulerArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $scheduler, "-ProjectRoot", $repoRoot, "-MainRunDir", $mainRunDir, "-IbkrRunDir", $ibkrRunDir, "-OutputDir", (Join-Path $weeklyReportsDir "combined"), "-Label", ("weekly-" + $suiteTs), "-DailyHour", $DailyHour)

Start-DetachedPs -Arguments $mainHourlyArgs -WorkDir $projectDir
Start-DetachedPs -Arguments $ibkrHourlyArgs -WorkDir $projectDir
Start-DetachedPs -Arguments $schedulerArgs -WorkDir $projectDir

$summary = [ordered]@{
    created_at = (Get-Date).ToString("o")
    suite_root = $suiteRoot
    weekly_reports_root = $weeklyReportsDir
    duration_days = $DurationDays
    sample_minutes = $SampleMinutes
    daily_report_hour = $DailyHour
    main_run_dir = $mainRunDir
    ibkr_run_dir = $ibkrRunDir
    main_api_base = "http://127.0.0.1:8010"
    ibkr_api_base = "http://127.0.0.1:8110"
}
$summaryPath = Join-Path $suiteRoot "weekly_suite_summary.json"
$summary | ConvertTo-Json -Depth 6 | Set-Content -Path $summaryPath -Encoding UTF8

Write-Output ("WEEKLY_SHADOW_SUITE_STARTED: {0}" -f $suiteRoot)
Write-Output ("SUMMARY_JSON: {0}" -f $summaryPath)
