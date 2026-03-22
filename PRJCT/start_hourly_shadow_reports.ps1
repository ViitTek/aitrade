param(
    [string]$ProjectRoot = "C:\aiinvest",
    [string]$ApiBase = "http://localhost:8010",
    [string]$RunDir = "",
    [string]$OutputDir = "",
    [string]$SuiteName = ""
)

$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$repoRoot = if (Test-Path (Join-Path $ProjectRoot "PRJCT")) { $ProjectRoot } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "PRJCT")) { Split-Path -Parent $ScriptDir } else { $ProjectRoot }
$projectDir = if (Test-Path (Join-Path $repoRoot "PRJCT")) { Join-Path $repoRoot "PRJCT" } else { $ScriptDir }
$reportsRootBase = if (Test-Path (Join-Path $repoRoot "RPRTS")) { Join-Path $repoRoot "RPRTS" } else { $repoRoot }
$py = Join-Path $projectDir "python-core\venv\Scripts\python.exe"
$job = Join-Path $projectDir "python-core\hourly_shadow_report_job.py"
$rawSuite = if (-not [string]::IsNullOrWhiteSpace($SuiteName)) {
    $SuiteName.Trim().ToLowerInvariant()
} elseif (-not [string]::IsNullOrWhiteSpace($OutputDir) -and ([IO.Path]::GetFileName($OutputDir).ToLowerInvariant() -eq "ibkr")) {
    "ibkr"
} else {
    "bin_krak"
}
$resolvedSuite = if ($rawSuite -eq "main") { "bin_krak" } else { $rawSuite }
$reportsRoot = Join-Path $reportsRootBase "_shadow-reports"
$runtimeStateDir = Join-Path (Join-Path $projectDir "_runtime\\report_jobs") $resolvedSuite
$reportDir = if ([string]::IsNullOrWhiteSpace($OutputDir)) { Join-Path $reportsRoot $resolvedSuite } else { $OutputDir }
$logFile = Join-Path $runtimeStateDir "hourly_report_job.log"

New-Item -ItemType Directory -Force -Path $runtimeStateDir | Out-Null
New-Item -ItemType Directory -Force -Path $reportDir | Out-Null

Set-Location $projectDir

$selfPid = $PID

$oldPy = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cl = ($_.CommandLine -as [string])
    $cl -and
    $cl.Contains("hourly_shadow_report_job.py") -and
    (
        [string]::IsNullOrWhiteSpace($RunDir) -or
        $cl.Contains($RunDir)
    ) -and
    (
        [string]::IsNullOrWhiteSpace($reportDir) -or
        $cl.Contains($reportDir)
    )
}
foreach ($p in $oldPy) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
}

$oldPs = Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cl = ($_.CommandLine -as [string])
    $_.ProcessId -ne $selfPid -and
    $cl -like "*start_hourly_shadow_reports.ps1*" -and
    (
        [string]::IsNullOrWhiteSpace($RunDir) -or
        $cl.Contains($RunDir)
    ) -and
    (
        [string]::IsNullOrWhiteSpace($reportDir) -or
        $cl.Contains($reportDir)
    )
}
foreach ($p in $oldPs) {
    try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
}

Write-Output ("[{0}] starting hourly shadow report job" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) | Tee-Object -FilePath $logFile -Append
while ($true) {
    Write-Output ("[{0}] hourly report tick start" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) | Tee-Object -FilePath $logFile -Append
    try {
        $jobArgs = @($job, "--api-base", $ApiBase, "--output-dir", $reportDir, "--state-dir", $runtimeStateDir, "--suite", $resolvedSuite)
        if (-not [string]::IsNullOrWhiteSpace($RunDir)) {
            $jobArgs += @("--run-dir", $RunDir)
        }
        & $py @jobArgs 2>&1 | Tee-Object -FilePath $logFile -Append
        Write-Output ("[{0}] hourly report tick done" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss")) | Tee-Object -FilePath $logFile -Append
    } catch {
        Write-Output ("[{0}] hourly report tick error: {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $_.Exception.Message) | Tee-Object -FilePath $logFile -Append
    }
    Start-Sleep -Seconds 3600
}
