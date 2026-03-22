param(
  [string]$ProjectRoot = "C:\aiinvest",
  [string]$RunDir = "",
  [string]$ApiBase = "http://127.0.0.1:8010",
  [string]$ReportOutputDir = "",
  [string]$SuiteName = "main",
  [string]$HealScript = "",
  [int]$DurationHours = 168,
  [int]$SampleMinutes = 20,
  [int]$PollSeconds = 60,
  [int]$StaleThresholdSec = 1800,
  [switch]$HealStack
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$layoutHelper = Join-Path $ScriptDir "resolve_aiinvest_layout.ps1"
if (Test-Path $layoutHelper) { . $layoutHelper }
$RepoRoot = if (Test-Path (Join-Path $ProjectRoot "PRJCT")) { $ProjectRoot } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "PRJCT")) { Split-Path -Parent $ScriptDir } else { $ProjectRoot }
if (Get-Command Get-AIInvestLayout -ErrorAction SilentlyContinue) {
  $layout = Get-AIInvestLayout -RepoRoot $RepoRoot
  $RepoRoot = $layout.RepoRoot
  $ProjectDir = $layout.ProjectDir
  $ReportsRootBase = $layout.ReportsDir
} else {
  $ProjectDir = if (Test-Path (Join-Path $RepoRoot "PRJCT")) { Join-Path $RepoRoot "PRJCT" } else { $ScriptDir }
  $ReportsRootBase = if (Test-Path (Join-Path $RepoRoot "RPRTS")) { Join-Path $RepoRoot "RPRTS" } else { $RepoRoot }
}

function Log([string]$msg, [string]$runDir) {
  $p = Join-Path $runDir "watchdog.log"
  $line = "$(Get-Date -Format s) $msg"
  $line | Tee-Object -FilePath $p -Append | Out-Null
  Write-Host $line
}

function Get-RunnerProcesses([string]$runDir) {
  @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cl = ($_.CommandLine -as [string])
    $cl -like "*shadow_trading_test_suite.ps1*" -and $cl -like "*$runDir*"
  })
}

function Get-WatchdogProcesses([string]$runDir) {
  @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object {
    $cl = ($_.CommandLine -as [string])
    $cl -like "*shadow_trading_watchdog.ps1*" -and $cl -like "*$runDir*"
  })
}

function Get-StateCurrentPid([string]$runDir) {
  $statePath = Join-Path $runDir "state.json"
  if (-not (Test-Path $statePath)) { return $null }
  try {
    $st = Get-Content $statePath -Raw | ConvertFrom-Json
    if ($null -eq $st.current_pid) { return $null }
    return [int]$st.current_pid
  } catch {
    return $null
  }
}

function Resolve-PreferredProcess($procs, [int]$preferredPid = 0) {
  if (@($procs).Count -eq 0) { return $null }
  if ($preferredPid -gt 0) {
    $preferred = @($procs) | Where-Object { [int]$_.ProcessId -eq $preferredPid } | Select-Object -First 1
    if ($preferred) { return $preferred }
  }
  return @($procs) | Sort-Object { [int]$_.ProcessId } -Descending | Select-Object -First 1
}

function Stop-DuplicateProcesses([string]$label, $procs, $keepProc, [string]$runDir) {
  foreach ($proc in @($procs)) {
    if (-not $keepProc -or [int]$proc.ProcessId -eq [int]$keepProc.ProcessId) { continue }
    try {
      Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction Stop
      Log "$label`_KILLED_DUPLICATE pid=$($proc.ProcessId) keep_pid=$($keepProc.ProcessId)" $runDir
    } catch {
      Log "$label`_KILL_DUPLICATE_FAILED pid=$($proc.ProcessId) err=$($_.Exception.Message)" $runDir
    }
  }
}

function Get-HeartbeatAgeSeconds([string]$runDir) {
  $hbPath = Join-Path $runDir "heartbeat.json"
  if (-not (Test-Path $hbPath)) { return $null }
  try {
    $hb = Get-Content $hbPath -Raw | ConvertFrom-Json
    $ts = [datetimeoffset]::Parse($hb.t)
    return [int][Math]::Round(((Get-Date) - $ts.LocalDateTime).TotalSeconds)
  } catch {
    return $null
  }
}

function Get-StateSnapshot([string]$runDir) {
  $statePath = Join-Path $runDir "state.json"
  if (-not (Test-Path $statePath)) { return $null }
  try {
    return Get-Content $statePath -Raw | ConvertFrom-Json
  } catch {
    return $null
  }
}

function Save-StateSnapshot([string]$runDir, $state) {
  $statePath = Join-Path $runDir "state.json"
  try {
    $state.updated_at = (Get-Date).ToString("o")
  } catch {}
  $tmpPath = "$statePath.tmp"
  $state | ConvertTo-Json -Depth 8 | Set-Content -Path $tmpPath -Encoding UTF8
  Move-Item -Path $tmpPath -Destination $statePath -Force
}

function Try-CloseExpiredRun([string]$runDir) {
  $state = Get-StateSnapshot -runDir $runDir
  if ($null -eq $state) { return $false }
  if ($state.completed -eq $true) { return $true }
  $startedAtRaw = [string]($state.started_at)
  $targetDurationSec = 0
  try { $targetDurationSec = [int]$state.target_duration_sec } catch { $targetDurationSec = 0 }
  if ([string]::IsNullOrWhiteSpace($startedAtRaw) -or $targetDurationSec -le 0) { return $false }
  try {
    $deadline = ([datetimeoffset]::Parse($startedAtRaw)).AddSeconds($targetDurationSec + 1800)
    if ((Get-Date) -lt $deadline.LocalDateTime) { return $false }
  } catch {
    return $false
  }

  foreach ($proc in @(Get-RunnerProcesses -runDir $runDir)) {
    try {
      Stop-Process -Id ([int]$proc.ProcessId) -Force -ErrorAction Stop
      Log "RUNNER_KILLED_EXPIRED pid=$($proc.ProcessId)" $runDir
    } catch {
      Log "RUNNER_KILL_EXPIRED_FAILED pid=$($proc.ProcessId) err=$($_.Exception.Message)" $runDir
    }
  }

  try {
    $state.completed = $true
    Save-StateSnapshot -runDir $runDir -state $state
  } catch {
    Log "STATE_COMPLETE_FAILED err=$($_.Exception.Message)" $runDir
  }
  Log "WATCHDOG_END completed=true expired_run=1" $runDir
  return $true
}

if (-not $RunDir) {
  $runTs = Get-Date -Format "yyyyMMdd-HHmmss"
  $RunDir = Join-Path (Join-Path $ReportsRootBase "_shadow_tests") ("shadow-suite-" + $runTs)
}
if ([string]::IsNullOrWhiteSpace($ReportOutputDir)) {
  $subdir = if ($SuiteName -eq "ibkr") { "ibkr" } else { "bin_krak" }
  $ReportOutputDir = Join-Path (Join-Path $ReportsRootBase "_shadow-reports") $subdir
}

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

Log "WATCHDOG_START suite=$SuiteName runDir=$RunDir api=$ApiBase reportDir=$ReportOutputDir durationHours=$DurationHours sampleMin=$SampleMinutes" $RunDir

$loop = 0
while ($true) {
  $loop++
  $watchdogs = @(Get-WatchdogProcesses -runDir $RunDir)
  if (@($watchdogs).Count -gt 1) {
    $keepWatchdog = Resolve-PreferredProcess -procs $watchdogs -preferredPid $PID
    Stop-DuplicateProcesses -label "WATCHDOG" -procs $watchdogs -keepProc $keepWatchdog -runDir $RunDir
    if (-not $keepWatchdog -or [int]$keepWatchdog.ProcessId -ne $PID) {
      break
    }
  }
  if (Try-CloseExpiredRun -runDir $RunDir) {
    break
  }
  $statePath = Join-Path $RunDir "state.json"
  if (Test-Path $statePath) {
    try {
      $st = Get-Content $statePath -Raw | ConvertFrom-Json
      if ($st.completed -eq $true) {
        Log "WATCHDOG_END completed=true" $RunDir
        break
      }
    } catch {}
  }

  $procs = @(Get-RunnerProcesses -runDir $RunDir)
  $preferredRunnerPid = Get-StateCurrentPid -runDir $RunDir
  if (@($procs).Count -gt 1) {
    $keepRunner = Resolve-PreferredProcess -procs $procs -preferredPid $preferredRunnerPid
    Stop-DuplicateProcesses -label "RUNNER" -procs $procs -keepProc $keepRunner -runDir $RunDir
    Start-Sleep -Milliseconds 500
    $procs = @(Get-RunnerProcesses -runDir $RunDir)
  }

  # Stale threshold must exceed worst-case capture duration across many horizons.
  $staleThreshold = [Math]::Max($StaleThresholdSec, ($PollSeconds * 3))
  $hbAge = Get-HeartbeatAgeSeconds -runDir $RunDir
  $isStale = ($hbAge -ne $null -and $hbAge -gt $staleThreshold)

  if (($loop % 5) -eq 0) {
    Log "WATCHDOG_HEARTBEAT procs=$(@($procs).Count) hb_age_sec=$hbAge threshold_sec=$staleThreshold" $RunDir
  }


  if ($isStale -and @($procs).Count -gt 0) {
    foreach ($rp in $procs) {
      try {
        Stop-Process -Id ([int]$rp.ProcessId) -Force -ErrorAction Stop
        Log "RUNNER_KILLED_STALE pid=$($rp.ProcessId) hb_age_sec=$hbAge threshold_sec=$staleThreshold" $RunDir
      } catch {
        Log "RUNNER_KILL_FAILED pid=$($rp.ProcessId) err=$($_.Exception.Message)" $RunDir
      }
    }
    Start-Sleep -Seconds 2
    $procs = @(Get-RunnerProcesses -runDir $RunDir)
  }

  if (@($procs).Count -eq 0) {
    $args = @(
      "-NoProfile", "-ExecutionPolicy", "Bypass",
      "-File", (Join-Path $ProjectDir "shadow_trading_test_suite.ps1"),
      "-ApiBase", $ApiBase,
      "-RunDir", $RunDir,
      "-DurationHours", "$DurationHours",
      "-SampleMinutes", "$SampleMinutes",
      "-SuiteName", ($(if ($SuiteName -eq "ibkr") { "ibkr" } else { "bin_krak" }))
    )
    if ($HealStack) { $args += "-HealStack" }
    if (-not [string]::IsNullOrWhiteSpace($HealScript)) {
      $args += @("-HealScript", $HealScript)
    }
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $args -WindowStyle Hidden -PassThru
    Log "RUNNER_RESTART pid=$($p.Id) hb_age_sec=$hbAge threshold_sec=$staleThreshold" $RunDir
  }



  Start-Sleep -Seconds ([Math]::Max(15, $PollSeconds))
}
