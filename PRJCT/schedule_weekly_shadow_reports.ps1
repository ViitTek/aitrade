param(
    [Parameter(Mandatory = $true)][string]$MainRunDir,
    [Parameter(Mandatory = $true)][string]$IbkrRunDir,
    [string]$ProjectRoot = "C:\aiinvest",
    [string]$OutputDir = "",
    [string]$Label = "",
    [int]$DailyHour = 15,
    [int]$PollMinutes = 5
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

if ([string]::IsNullOrWhiteSpace($OutputDir)) {
    $OutputDir = Join-Path $reportsRootBase "_shadow-reports\weekly"
}
if ([string]::IsNullOrWhiteSpace($Label)) {
    $Label = "weekly-" + (Get-Date -Format "yyyyMMdd")
}

$runner = Join-Path $projectDir "run_weekly_shadow_report.ps1"
$logDir = Join-Path $projectDir "_runtime\weekly_reports"
$logFile = Join-Path $logDir "scheduler.log"
New-Item -ItemType Directory -Path $logDir -Force | Out-Null

function Log([string]$msg) {
    $line = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    $line | Tee-Object -FilePath $logFile -Append | Out-Null
}

function Get-State([string]$RunDir) {
    $statePath = Join-Path $RunDir "state.json"
    if (-not (Test-Path $statePath)) { return $null }
    try {
        return Get-Content $statePath -Raw | ConvertFrom-Json
    } catch {
        return $null
    }
}

function Invoke-Report([string]$Kind) {
    Log "REPORT start kind=$Kind"
    try {
        & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $runner `
            -MainRunDir $MainRunDir `
            -IbkrRunDir $IbkrRunDir `
            -ProjectRoot $repoRoot `
            -OutputDir $OutputDir `
            -Kind $Kind `
            -Label $Label | Tee-Object -FilePath $logFile -Append | Out-Null
        Log "REPORT done kind=$Kind"
    } catch {
        Log "REPORT error kind=$Kind err=$($_.Exception.Message)"
    }
}

$today = Get-Date
$nextDaily = Get-Date -Hour $DailyHour -Minute 0 -Second 0
if ($today -ge $nextDaily) {
    $nextDaily = $nextDaily.AddDays(1)
}
$finalDone = $false

Log "SCHEDULER start label=$Label daily_hour=$DailyHour"

while (-not $finalDone) {
    $now = Get-Date
    if ($now -ge $nextDaily) {
        Invoke-Report -Kind "daily"
        $nextDaily = $nextDaily.AddDays(1)
    }

    $mainState = Get-State -RunDir $MainRunDir
    $ibkrState = Get-State -RunDir $IbkrRunDir
    $mainDone = $mainState -and [bool]$mainState.completed
    $ibkrDone = $ibkrState -and [bool]$ibkrState.completed

    if ($mainDone -and $ibkrDone) {
        Invoke-Report -Kind "final"
        $finalDone = $true
        break
    }

    Start-Sleep -Seconds ([Math]::Max(60, $PollMinutes * 60))
}

Log "SCHEDULER end label=$Label"
