param(
  [string]$ApiBase = "http://localhost:8010",
  [int]$DurationHours = 24,
  [int]$SampleMinutes = 20,
  [string]$Horizons = "15,30,45,60,120,180,240,300,360,420,480,540,600,660,720,780,840,900,960,1020,1080,1140,1200,1260,1320,1380,1440,1500,1560,1620,1680,1740,1800,1860,1920,1980,2040,2100,2160,2220,2280,2340,2400,2460,2520,2580,2640,2700,2760,2820,2880,2940,3000,3060,3120,3180,3240,3300,3360,3420,3480,3540,3600,3660,3720,3780,3840,3900,3960,4020,4080,4140,4200,4260,4320,4380,4440,4500,4560,4620,4680,4740,4800,4860,4920,4980,5040,5100,5160,5220,5280,5340,5400,5460,5520,5580,5640,5700,5760,5820,5880,5940,6000,6060,6120,6180,6240,6300,6360,6420,6480,6540,6600,6660,6720,6780,6840,6900,6960,7020,7080,7140,7200,7260,7320,7380,7440,7500,7560,7620,7680,7740,7800,7860,7920,7980,8040,8100,8160,8220,8280,8340,8400,8460,8520,8580,8640,8700,8760,8820,8880,8940,9000,9060,9120,9180,9240,9300,9360,9420,9480,9540,9600,9660,9720,9780,9840,9900,9960,10020,10080",
  [int]$LookbackHours = 720,
  [int]$Limit = 10000,
  [switch]$HealStack,
  [string]$ProjectRoot = "C:\aiinvest",
  [double]$LocalSimKrakenEur = 100.0,
  [double]$LocalSimBinanceEur = 100.0,
  [double]$LocalSimIbkrEur = 100.0,
  [double]$LocalSimStakePct = 0.10,
  [int]$LocalSimHorizonMin = 60,
  [string]$LocalSimActions = "shadow",
  [string]$LocalSimKrakenBases = "BTC,ETH",
  [string]$LocalSimBinanceBases = "SOL,BNB,XRP,DOGE,TRX,USDC",
  [string]$LocalSimIbkrBases = "PAXG,EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL",
  [double]$LocalSimIbkrFeeRate = 0.00005,
  [string]$RunDir = "",
  [string]$HealScript = "",
  [string]$SuiteName = ""
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

function Parse-Horizons([string]$src) {
  $out = @()
  foreach ($x in ($src -split ",")) {
    $v = 0
    if ([int]::TryParse($x.Trim(), [ref]$v) -and $v -gt 0) { $out += $v }
  }
  return @($out | Sort-Object -Unique)
}

if (-not $RunDir) {
  $runTs = Get-Date -Format "yyyyMMdd-HHmmss"
  $outRoot = Join-Path $ReportsRootBase "_shadow_tests"
  $RunDir = Join-Path $outRoot ("shadow-suite-" + $runTs)
}
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

$metricsPath = Join-Path $RunDir "metrics.jsonl"
$runLogPath = Join-Path $RunDir "run.log"
$heartbeatPath = Join-Path $RunDir "heartbeat.json"
$statePath = Join-Path $RunDir "state.json"
$cfgPath = Join-Path $RunDir "config.json"

function Log([string]$msg) {
  "$(Get-Date -Format s) $msg" | Tee-Object -FilePath $runLogPath -Append | Out-Null
}

function Console-Status([string]$msg) {
  $line = "$(Get-Date -Format s) $msg"
  Write-Host $line
  $line | Tee-Object -FilePath $runLogPath -Append | Out-Null
}

function Write-JsonAtomic {
  param(
    [Parameter(Mandatory=$true)] [string]$Path,
    [Parameter(Mandatory=$true)] [string]$Content
  )

  $dir = Split-Path -Parent $Path
  if (-not [string]::IsNullOrWhiteSpace($dir)) {
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
  }

  for ($attempt = 1; $attempt -le 5; $attempt++) {
    $tmp = Join-Path $dir ("." + [IO.Path]::GetFileName($Path) + ".tmp-" + $PID + "-" + $attempt)
    try {
      Set-Content -Path $tmp -Value $Content -Encoding UTF8
      Move-Item -Path $tmp -Destination $Path -Force
      return
    } catch {
      try { Remove-Item $tmp -Force -ErrorAction SilentlyContinue } catch {}
      if ($attempt -eq 5) { throw }
      Start-Sleep -Milliseconds (120 * $attempt)
    }
  }
}

function Write-Heartbeat([string]$state) {
  $hb = [ordered]@{
    t = (Get-Date).ToString("o")
    state = $state
    pid = $PID
  }
  Write-JsonAtomic -Path $heartbeatPath -Content ($hb | ConvertTo-Json -Depth 4)
}

function Get-Health() {
  try { return Invoke-RestMethod "$ApiBase/health" -TimeoutSec 6 } catch { return $null }
}
function Get-Status() {
  try { return Invoke-RestMethod "$ApiBase/bot/status" -TimeoutSec 8 } catch { return $null }
}

function Try-Heal() {
  if (-not $HealStack) { return }
  $healTarget = $HealScript
  if ([string]::IsNullOrWhiteSpace($healTarget)) {
    $healTarget = Join-Path $ProjectDir "start_aiinvest.ps1"
  }
  Log "HEAL attempt via $healTarget"
  Console-Status "HEAL_TRIGGERED action=$healTarget"
  try {
    $healArgs = @(
      "-NoProfile", "-ExecutionPolicy", "Bypass",
      "-File", $healTarget
    )
    $healLeaf = Split-Path -Leaf $healTarget
    if (
      [string]::Equals($healLeaf, "start_aiinvest.ps1", [System.StringComparison]::OrdinalIgnoreCase) -or
      [string]::Equals($healLeaf, "start_ibkr_shadow_stack.ps1", [System.StringComparison]::OrdinalIgnoreCase)
    ) {
      $healArgs += "-CleanFirst"
      $healArgs += "-Headless"
    }
    & powershell @healArgs | Out-Null
    Start-Sleep -Seconds 6
    Console-Status "HEAL_RESULT ok=1"
  } catch {
    Log "HEAL failed: $($_.Exception.Message)"
    Console-Status "HEAL_RESULT ok=0 err=$($_.Exception.Message)"
  }
}

function Get-Shadow([int]$horizon) {
  try {
    $url = "$ApiBase/bot/signal-quality/shadow-report?lookback_hours=$LookbackHours&horizon_min=$horizon&limit=$Limit&actions=shadow,policy,executed"
    return Invoke-RestMethod $url -TimeoutSec 40
  } catch {
    return [pscustomobject]@{ error = $_.Exception.Message; horizon = $horizon }
  }
}

function Capture([int]$tick, [int[]]$hs) {
  Write-Heartbeat -state "capture_$tick"
  Write-Heartbeat -state "capture_${tick}_health"
  $health = Get-Health
  if (-not $health) { Try-Heal; $health = Get-Health }
  Write-Heartbeat -state "capture_${tick}_status"
  $status = Get-Status
  $coverage = $null
  Write-Heartbeat -state "capture_${tick}_coverage"
  try { $coverage = Invoke-RestMethod "$ApiBase/bot/data-coverage?lookback_days=60&tf=60" -TimeoutSec 25 } catch {}

  $sh = @{}
  foreach ($h in $hs) {
    Write-Heartbeat -state "capture_${tick}_h$h"
    $sh["h$h"] = Get-Shadow -horizon $h
  }
  Write-Heartbeat -state "capture_${tick}_persist"

  $obj = [ordered]@{
    t = (Get-Date).ToString("o")
    tick = $tick
    health = if ($health) { $health.status } else { "down" }
    status = $status
    coverage = $coverage
    shadow = $sh
  }
  ($obj | ConvertTo-Json -Depth 12 -Compress) | Add-Content $metricsPath -Encoding UTF8
}

function Save-State($s) {
  $s.run_dir = $RunDir
  $s.updated_at = (Get-Date).ToString("o")
  $s.current_pid = $PID
  Write-JsonAtomic -Path $statePath -Content ($s | ConvertTo-Json -Depth 8)
}

function Get-ResolvedSuiteName() {
  if (-not [string]::IsNullOrWhiteSpace($SuiteName)) { return $SuiteName }
  if ($ApiBase -like "*8110*") { return "ibkr" }
  return "bin_krak"
}

function Get-ReportOutputDir() {
  $suite = Get-ResolvedSuiteName
  return Join-Path (Join-Path $ReportsRootBase "_shadow-reports") $suite
}

function Build-Final([int[]]$hs, $state) {
  if (-not (Test-Path $metricsPath)) { return }
  $rows = Get-Content $metricsPath | ForEach-Object { $_ | ConvertFrom-Json }
  if ($rows.Count -lt 1) { return }
  $first = $rows[0]
  $last = $rows[-1]

  $byH = @()
  foreach ($h in $hs) {
    $k = "h$h"; $f = $first.shadow.$k; $l = $last.shadow.$k
    if ($null -eq $f -or $null -eq $l -or $f.error -or $l.error) { continue }
    $byH += [pscustomobject]@{
      horizon = $h
      total_delta = ([int]$l.counts.total - [int]$f.counts.total)
      dedup_delta = ([int]$l.counts.total_dedup - [int]$f.counts.total_dedup)
      shadow_delta = ([int]$l.counts.shadow - [int]$f.counts.shadow)
      policy_delta = ([int]$l.counts.policy - [int]$f.counts.policy)
      eval_delta = ([int]$l.summary.shadow_eval_samples - [int]$f.summary.shadow_eval_samples)
      eval_end = [int]$l.summary.shadow_eval_samples
      pf_end = $l.summary.shadow_profit_factor_h
      wr_end = $l.summary.shadow_win_rate_h
    }
  }

  $healthOk = @($rows | Where-Object { $_.health -eq "ok" }).Count
  $summary = [ordered]@{
    generated_at = (Get-Date).ToString("o")
    run_dir = $RunDir
    samples = $rows.Count
    health_ok_samples = $healthOk
    health_ratio = if ($rows.Count -gt 0) { [math]::Round($healthOk / $rows.Count, 4) } else { 0.0 }
    run_id = $last.status.run_id
    bot_running = [bool]$last.status.running
    first_t = $first.t
    last_t = $last.t
    target_duration_hours = $DurationHours
    horizons = $byH
  }

  $localSim = $null
  try {
    $py = Join-Path $ProjectDir "python-core\venv\Scripts\python.exe"
    $sim = Join-Path $ProjectDir "python-core\shadow_local_pnl.py"
    $runIds = @()
    foreach ($row in $rows) {
      if ($row.status -and $row.status.run_id -and $row.status.running -eq $true) {
        $rid = [string]$row.status.run_id
        if ($runIds -notcontains $rid) { $runIds += $rid }
      }
    }
    if ((Test-Path $py) -and (Test-Path $sim) -and $runIds.Count -gt 0) {
      $raw = & $py $sim `
        --run-ids ($runIds -join ",") `
        --from-iso $first.t `
        --to-iso $last.t `
        --horizon-min $LocalSimHorizonMin `
        --actions $LocalSimActions `
        --kraken-eur $LocalSimKrakenEur `
        --binance-eur $LocalSimBinanceEur `
        --ibkr-eur $LocalSimIbkrEur `
        --ibkr-fee-rate $LocalSimIbkrFeeRate `
        --stake-pct $LocalSimStakePct `
        --kraken-bases $LocalSimKrakenBases `
        --binance-bases $LocalSimBinanceBases `
        --ibkr-bases $LocalSimIbkrBases
      if ($raw) { $localSim = $raw | ConvertFrom-Json }
    }
  } catch {
    $localSim = [pscustomobject]@{ ok = $false; error = $_.Exception.Message }
  }
  if ($localSim) { $summary.local_shadow_sim = $localSim }

  $finalReport = $null
  try {
    $py = Join-Path $ProjectDir "python-core\venv\Scripts\python.exe"
    $job = Join-Path $ProjectDir "python-core\hourly_shadow_report_job.py"
    $suite = Get-ResolvedSuiteName
    $outputDir = Get-ReportOutputDir
    if ((Test-Path $py) -and (Test-Path $job)) {
      $reportLines = & $py $job `
        --api-base $ApiBase `
        --run-dir $RunDir `
        --output-dir $outputDir `
        --suite $suite
      $okLine = @($reportLines | Where-Object { $_ -like "OK *" } | Select-Object -Last 1)
      if ($okLine) {
        $finalReport = ($okLine -replace '^OK\s+', '').Trim()
      }
    }
  } catch {
    Log "FINAL_REPORT_ERR $($_.Exception.Message)"
  }
  if ($finalReport) { $summary.final_suite_report = $finalReport }

  Write-JsonAtomic -Path (Join-Path $RunDir "final_summary.json") -Content ($summary | ConvertTo-Json -Depth 10)
  $txt = @()
  $txt += "Shadow Trading Test Suite - Final Report"
  $txt += "Generated: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))"
  $txt += "RunDir: $RunDir"
  $txt += "Samples: $($rows.Count), health_ok: $healthOk"
  $txt += "RunId: $($last.status.run_id), bot_running: $([bool]$last.status.running)"
  $txt += "Window: $($first.t) -> $($last.t)"
  if ($summary.final_suite_report) {
    $txt += "Final suite report: $($summary.final_suite_report)"
  }
  $txt += ""
  $txt += ($byH | Format-Table -AutoSize | Out-String)
  if ($localSim) {
    $txt += ""
    $txt += "Local shadow simulation:"
    $txt += ($localSim | ConvertTo-Json -Depth 8)
  }
  Write-JsonAtomic -Path (Join-Path $RunDir "final_report.txt") -Content ($txt -join [Environment]::NewLine)
}

function Finalize-Run([int[]]$hs, $state, [string]$completionReason = "target_elapsed") {
  $state.completed = $true
  $state.completion_reason = $completionReason
  $state.completed_at = (Get-Date).ToString("o")
  Save-State $state
  Write-Heartbeat -state "finalizing"
  Console-Status "FINALIZE_START reason=$completionReason"

  try {
    Build-Final -hs $hs -state $state
    Log "FINALIZE_OK reason=$completionReason"
  } catch {
    $errMsg = $_.Exception.Message
    $stack = $_.ScriptStackTrace
    Log "FINALIZE_ERR reason=$completionReason err=$errMsg"
    if (-not [string]::IsNullOrWhiteSpace($stack)) {
      Log "FINALIZE_STACK $stack"
    }
    Console-Status "FINALIZE_ERR reason=$completionReason err=$errMsg"
  }

  Save-State $state
  Write-Heartbeat -state "end"
  Log "END"
  Console-Status "RUNNER_END run_dir=$RunDir completion_reason=$completionReason"
  Write-Output "SHADOW_TEST_DONE: $RunDir"
}

$hs = Parse-Horizons -src $Horizons
if ($hs.Count -eq 0) { throw "No valid horizons in '$Horizons'" }

# Init / resume state
$state = $null
if (Test-Path $statePath) {
  try { $state = Get-Content $statePath -Raw | ConvertFrom-Json } catch { $state = $null }
}
if (-not $state) {
  $now = Get-Date
  $state = [ordered]@{
    run_dir = $RunDir
    started_at = $now.ToString("o")
    target_duration_sec = [int]($DurationHours * 3600)
    sample_minutes = $SampleMinutes
    next_tick = 0
    next_sample_at = $now.ToString("o")
    completed = $false
  }
  Save-State $state
  $cfg = [ordered]@{
    started_at = $state.started_at
    api_base = $ApiBase
    duration_hours = $DurationHours
    sample_minutes = $SampleMinutes
    lookback_hours = $LookbackHours
    horizons = $hs
    limit = $Limit
    heal_stack = [bool]$HealStack
    suite_name = (Get-ResolvedSuiteName)
  }
  Write-JsonAtomic -Path $cfgPath -Content ($cfg | ConvertTo-Json -Depth 6)
  Log "START runDir=$RunDir targetHours=$DurationHours sampleMin=$SampleMinutes horizons=$($hs -join ',') lookback=$LookbackHours"
} else {
  Log "RESUME runDir=$RunDir next_tick=$($state.next_tick)"
}

$startDt = [datetime]::Parse($state.started_at).ToUniversalTime()
$targetSec = [int]$state.target_duration_sec

while ($true) {
  $nowUtc = (Get-Date).ToUniversalTime()
  $elapsed = [int]($nowUtc - $startDt).TotalSeconds
  if ($elapsed -ge $targetSec) { break }

  Write-Heartbeat -state "loop"
  try {
    $due = [datetime]::Parse($state.next_sample_at)
  } catch {
    $due = Get-Date
  }

  if ((Get-Date) -ge $due) {
    $tick = [int]$state.next_tick
    Console-Status "TICK_START tick=$tick due=$($due.ToString('o')) elapsed_sec=$elapsed next_sample_at=$($state.next_sample_at)"
    try {
      Capture -tick $tick -hs $hs
      Log "TICK $tick done"
      Console-Status "TICK_DONE tick=$tick horizons=$($hs.Count) metrics_path=$metricsPath"
    } catch {
      Log "ERR tick=${tick}: $($_.Exception.Message)"
      Console-Status "TICK_ERR tick=$tick err=$($_.Exception.Message)"
      if ($HealStack) { Try-Heal }
    }
    $state.next_tick = $tick + 1
    $state.next_sample_at = ($due.AddMinutes($SampleMinutes)).ToString("o")
    Save-State $state
  }

  # Minute checkpoint for crash-resume safety.
  Save-State $state
  $hbAgeSec = $null
  if (Test-Path $heartbeatPath) {
    try {
      $hb = Get-Content $heartbeatPath -Raw | ConvertFrom-Json
      $hbTs = [datetimeoffset]::Parse($hb.t)
      $hbAgeSec = [int][Math]::Round(((Get-Date) - $hbTs.LocalDateTime).TotalSeconds)
    } catch {}
  }
  Console-Status "STACK_OK tick=$($state.next_tick) hb_age_sec=$hbAgeSec next_sample_at=$($state.next_sample_at) completed=$($state.completed)"
  Start-Sleep -Seconds 60
}

Finalize-Run -hs $hs -state $state -completionReason "target_elapsed"
