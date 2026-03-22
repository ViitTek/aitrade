param(
  [string]$ProjectRoot = "C:\aiinvest",
  [int]$ApiPort = 8010,
  [int]$BacktestPort = 8001,
  [int]$DashboardPort = 5173,
  [int]$RestartCycles = 3,
  [int]$DurationMinutes = 120,
  [int]$SampleSeconds = 20,
  [switch]$CrashDrill
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $ProjectRoot ("qa\runs\stability-" + $ts)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$eventsLog = Join-Path $runDir "events.log"
$samplesPath = Join-Path $runDir "samples.jsonl"
$summaryJson = Join-Path $runDir "summary.json"
$summaryMd = Join-Path $runDir "summary.md"

function Log([string]$msg) {
  $line = "$(Get-Date -Format s) $msg"
  $line | Tee-Object -FilePath $eventsLog -Append | Out-Null
}

function Test-Port([int]$port) {
  try {
    $tcp = New-Object Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(1200)
    if (-not $ok) { return $false }
    $tcp.EndConnect($iar) | Out-Null
    $tcp.Close()
    return $true
  } catch { return $false }
}

function Get-ApiHealth() {
  try {
    $t0 = Get-Date
    $r = Invoke-RestMethod "http://localhost:$ApiPort/health" -TimeoutSec 6
    $ms = [int]((Get-Date) - $t0).TotalMilliseconds
    return [pscustomobject]@{ ok = ($r.status -eq "ok"); ms = $ms; err = $null }
  } catch {
    return [pscustomobject]@{ ok = $false; ms = -1; err = $_.Exception.Message }
  }
}

function Get-BotStatus() {
  try {
    return Invoke-RestMethod "http://localhost:$ApiPort/bot/status" -TimeoutSec 8
  } catch { return $null }
}

function Wait-Ready([int]$timeoutSec = 90) {
  $deadline = (Get-Date).AddSeconds($timeoutSec)
  while ((Get-Date) -lt $deadline) {
    $h = Get-ApiHealth
    if ($h.ok -and (Test-Port $ApiPort) -and (Test-Port $BacktestPort) -and (Test-Port $DashboardPort)) {
      return $true
    }
    Start-Sleep -Seconds 2
  }
  return $false
}

function Start-Stack() {
  $startScript = Join-Path $ProjectRoot "start_aiinvest.ps1"
  if (-not (Test-Path $startScript)) { throw "Missing $startScript" }
  Log "STACK_START"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -CleanFirst | Out-Null
}

function Stop-Stack() {
  $stopScript = Join-Path $ProjectRoot "stop_aiinvest.ps1"
  if (-not (Test-Path $stopScript)) { throw "Missing $stopScript" }
  Log "STACK_STOP"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $stopScript | Out-Null
}

function Kill-ApiProcess() {
  $pids = @()
  try {
    $rows = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object {
      ($_.CommandLine -as [string]) -match "--port\s+$ApiPort(\s|$)"
    }
    $pids = @($rows | Select-Object -ExpandProperty ProcessId)
  } catch {}
  foreach ($pid in $pids) {
    try {
      Stop-Process -Id $pid -Force -ErrorAction Stop
      Log "CRASH_DRILL killed_api_pid=$pid"
    } catch {
      Log "CRASH_DRILL kill_failed pid=$pid err=$($_.Exception.Message)"
    }
  }
}

Log "SUITE_START run_dir=$runDir restart_cycles=$RestartCycles duration_min=$DurationMinutes sample_s=$SampleSeconds crash_drill=$($CrashDrill.IsPresent)"

$restartPass = 0
for ($i = 1; $i -le $RestartCycles; $i++) {
  try {
    Start-Stack
    $ok = Wait-Ready -timeoutSec 90
    if ($ok) {
      $restartPass++
      Log "RESTART_CYCLE cycle=$i result=PASS"
    } else {
      Log "RESTART_CYCLE cycle=$i result=FAIL"
    }
  } catch {
    Log "RESTART_CYCLE cycle=$i result=ERROR err=$($_.Exception.Message)"
  }
}

$soakEnd = (Get-Date).AddMinutes($DurationMinutes)
$samples = 0
$apiDownSamples = 0
$dashDownSamples = 0
$btDownSamples = 0
$latencies = New-Object System.Collections.Generic.List[int]
$minConsecutiveDown = 999999
$maxConsecutiveDown = 0
$currentConsecutiveDown = 0
$crashDrillDone = $false

while ((Get-Date) -lt $soakEnd) {
  $h = Get-ApiHealth
  $apiPortUp = Test-Port $ApiPort
  $btPortUp = Test-Port $BacktestPort
  $dashPortUp = Test-Port $DashboardPort
  $bot = Get-BotStatus

  if ($h.ms -ge 0) { $latencies.Add($h.ms) }
  if (-not $h.ok) { $apiDownSamples++ }
  if (-not $btPortUp) { $btDownSamples++ }
  if (-not $dashPortUp) { $dashDownSamples++ }

  if ($h.ok -and $apiPortUp -and $btPortUp) {
    if ($currentConsecutiveDown -gt 0) {
      $maxConsecutiveDown = [Math]::Max($maxConsecutiveDown, $currentConsecutiveDown)
      $minConsecutiveDown = [Math]::Min($minConsecutiveDown, $currentConsecutiveDown)
      $currentConsecutiveDown = 0
    }
  } else {
    $currentConsecutiveDown++
  }

  $obj = [ordered]@{
    t = (Get-Date).ToString("o")
    api_ok = $h.ok
    api_ms = $h.ms
    api_err = $h.err
    api_port = $apiPortUp
    backtest_port = $btPortUp
    dashboard_port = $dashPortUp
    bot_running = if ($bot) { [bool]$bot.running } else { $false }
    run_id = if ($bot) { $bot.run_id } else { $null }
  }
  ($obj | ConvertTo-Json -Compress) | Add-Content -Path $samplesPath -Encoding UTF8
  $samples++

  if ($CrashDrill -and -not $crashDrillDone -and $samples -ge [Math]::Max(3, [int](300 / [Math]::Max(5, $SampleSeconds)))) {
    Kill-ApiProcess
    $crashDrillDone = $true
  }

  Start-Sleep -Seconds ([Math]::Max(5, $SampleSeconds))
}

if ($currentConsecutiveDown -gt 0) {
  $maxConsecutiveDown = [Math]::Max($maxConsecutiveDown, $currentConsecutiveDown)
  $minConsecutiveDown = [Math]::Min($minConsecutiveDown, $currentConsecutiveDown)
}
if ($minConsecutiveDown -eq 999999) { $minConsecutiveDown = 0 }

$p95 = 0
$avg = 0
if ($latencies.Count -gt 0) {
  $sorted = $latencies | Sort-Object
  $idx = [int][Math]::Floor(0.95 * ($sorted.Count - 1))
  $p95 = $sorted[$idx]
  $avg = [int][Math]::Round(($latencies | Measure-Object -Average).Average, 0)
}

$summary = [ordered]@{
  generated_at = (Get-Date).ToString("o")
  run_dir = $runDir
  restart_cycles = $RestartCycles
  restart_pass = $restartPass
  restart_pass_rate = if ($RestartCycles -gt 0) { [math]::Round($restartPass / $RestartCycles, 4) } else { 0.0 }
  soak_minutes = $DurationMinutes
  samples = $samples
  api_down_samples = $apiDownSamples
  backtest_down_samples = $btDownSamples
  dashboard_down_samples = $dashDownSamples
  api_uptime_ratio = if ($samples -gt 0) { [math]::Round((($samples - $apiDownSamples) / $samples), 4) } else { 0.0 }
  api_latency_avg_ms = $avg
  api_latency_p95_ms = $p95
  max_consecutive_down_samples = $maxConsecutiveDown
  min_consecutive_down_samples = $minConsecutiveDown
  crash_drill = [bool]$CrashDrill
  crash_drill_executed = $crashDrillDone
}

$summary | ConvertTo-Json -Depth 6 | Set-Content -Path $summaryJson -Encoding UTF8

$md = @()
$md += "# AIInvest Stability Suite"
$md += ""
$md += "- generated_at: $($summary.generated_at)"
$md += "- run_dir: $($summary.run_dir)"
$md += "- restart_pass_rate: $($summary.restart_pass_rate) ($($summary.restart_pass)/$($summary.restart_cycles))"
$md += "- api_uptime_ratio: $($summary.api_uptime_ratio)"
$md += "- api_latency_avg_ms: $($summary.api_latency_avg_ms)"
$md += "- api_latency_p95_ms: $($summary.api_latency_p95_ms)"
$md += "- max_consecutive_down_samples: $($summary.max_consecutive_down_samples)"
$md += "- crash_drill_executed: $($summary.crash_drill_executed)"
$md += ""
$md += "## Acceptance Gates"
$md += ""
$md += "| Gate | Target | Actual | Result |"
$md += "|---|---:|---:|---|"
$g1 = if ($summary.restart_pass_rate -ge 1.0) { "PASS" } else { "FAIL" }
$g2 = if ($summary.api_uptime_ratio -ge 0.995) { "PASS" } else { "FAIL" }
$g3 = if ($summary.api_latency_p95_ms -le 3000) { "PASS" } else { "FAIL" }
$g4 = if ($summary.max_consecutive_down_samples -le 2) { "PASS" } else { "FAIL" }
$md += "| Restart reliability | 1.0000 | $($summary.restart_pass_rate) | $g1 |"
$md += "| API uptime ratio | 0.9950 | $($summary.api_uptime_ratio) | $g2 |"
$md += "| API p95 latency (ms) | 3000 | $($summary.api_latency_p95_ms) | $g3 |"
$md += "| Max consecutive down samples | 2 | $($summary.max_consecutive_down_samples) | $g4 |"

$md -join "`r`n" | Set-Content -Path $summaryMd -Encoding UTF8

Log "SUITE_END summary=$summaryJson"
Write-Host "Run finished: $runDir" -ForegroundColor Green
