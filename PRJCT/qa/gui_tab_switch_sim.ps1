param(
  [string]$ProjectRoot = "C:\aiinvest",
  [string]$ApiBase = "http://localhost:8010",
  [string]$DashboardBase = "http://localhost:5173",
  [int]$DurationMinutes = 5,
  [int]$StepDelayMs = 800
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $ProjectRoot ("qa\runs\tab-switch-" + $ts)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$eventsPath = Join-Path $runDir "events.log"
$samplesPath = Join-Path $runDir "samples.jsonl"
$summaryPath = Join-Path $runDir "summary.md"

function Log([string]$msg) {
  $line = "$(Get-Date -Format s) $msg"
  $line | Tee-Object -FilePath $eventsPath -Append | Out-Null
}

function Invoke-TimedGet([string]$name, [string]$url, [int]$timeoutSec = 8) {
  $sw = [System.Diagnostics.Stopwatch]::StartNew()
  try {
    $resp = Invoke-WebRequest -Uri $url -TimeoutSec $timeoutSec -UseBasicParsing
    $sw.Stop()
    return [pscustomobject]@{
      name = $name; ok = $true; ms = [int]$sw.ElapsedMilliseconds; code = [int]$resp.StatusCode; err = $null
    }
  } catch {
    $sw.Stop()
    $code = $null
    try { $code = [int]$_.Exception.Response.StatusCode.value__ } catch {}
    return [pscustomobject]@{
      name = $name; ok = $false; ms = [int]$sw.ElapsedMilliseconds; code = $code; err = $_.Exception.Message
    }
  }
}

function Test-Port([int]$port) {
  try {
    $tcp = New-Object Net.Sockets.TcpClient
    $iar = $tcp.BeginConnect("127.0.0.1", $port, $null, $null)
    $ok = $iar.AsyncWaitHandle.WaitOne(1000)
    if (-not $ok) { return $false }
    $tcp.EndConnect($iar) | Out-Null
    $tcp.Close()
    return $true
  } catch { return $false }
}

function Get-SymbolForChart() {
  $r = Invoke-TimedGet -name "chart.symbols" -url "$ApiBase/market/symbols?tf=60"
  if (-not $r.ok) { return "BTC/USDT" }
  try {
    $json = Invoke-RestMethod "$ApiBase/market/symbols?tf=60" -TimeoutSec 6
    if ($json.symbols -and $json.symbols.Count -gt 0) { return [string]$json.symbols[0] }
  } catch {}
  return "BTC/USDT"
}

Log "START duration_min=$DurationMinutes step_delay_ms=$StepDelayMs api=$ApiBase dash=$DashboardBase"

$steps = @(
  @{ tab = "Dashboard"; name = "dashboard.index"; url = "$DashboardBase/" },
  @{ tab = "Dashboard"; name = "dashboard.status"; url = "$ApiBase/bot/status" },
  @{ tab = "Dashboard"; name = "dashboard.portfolio"; url = "$ApiBase/bot/portfolio" },
  @{ tab = "Dashboard"; name = "dashboard.signals"; url = "$ApiBase/bot/signals?limit=20" },

  @{ tab = "Trades"; name = "trades.closed"; url = "$ApiBase/bot/positions/closed?limit=100" },
  @{ tab = "Trades"; name = "trades.signals"; url = "$ApiBase/bot/signals?limit=100" },

  @{ tab = "Chart"; name = "chart.symbols"; url = "$ApiBase/market/symbols?tf=60" },

  @{ tab = "Config"; name = "config.get"; url = "$ApiBase/bot/config" },

  @{ tab = "Sentiment"; name = "sentiment.summary"; url = "$ApiBase/sentiment/summary?symbol=BTC&window=120" },
  @{ tab = "Sentiment"; name = "sentiment.recent"; url = "$ApiBase/sentiment/recent?symbol=BTC&limit=30" },
  @{ tab = "Sentiment"; name = "sentiment.intel"; url = "$ApiBase/sentiment/intel" },

  @{ tab = "MarketData"; name = "market.data"; url = "$ApiBase/bot/market-data" },
  @{ tab = "MarketData"; name = "market.coverage"; url = "$ApiBase/bot/data-coverage?days=60&tf=60" }
)

$end = (Get-Date).AddMinutes($DurationMinutes)
$count = 0
$fails = 0
$lat = New-Object System.Collections.Generic.List[int]
$portDownApi = 0
$portDownDash = 0
$portDownBt = 0
$loops = 0

while ((Get-Date) -lt $end) {
  $loops++
  $sym = [System.Uri]::EscapeDataString((Get-SymbolForChart))
  $chartStep = @{ tab = "Chart"; name = "chart.candles"; url = "$ApiBase/market/candles?symbol=$sym&tf=60&limit=200" }

  foreach ($step in ($steps + $chartStep)) {
    $r = Invoke-TimedGet -name $step.name -url $step.url
    $obj = [ordered]@{
      t = (Get-Date).ToString("o")
      loop = $loops
      tab = $step.tab
      name = $step.name
      ok = $r.ok
      ms = $r.ms
      code = $r.code
      err = $r.err
      api_port = (Test-Port 8010)
      bt_port = (Test-Port 8001)
      dash_port = (Test-Port 5173)
    }
    ($obj | ConvertTo-Json -Compress) | Add-Content -Path $samplesPath -Encoding UTF8
    $count++
    if (-not $r.ok) { $fails++ } else { $lat.Add($r.ms) }
    if (-not $obj.api_port) { $portDownApi++ }
    if (-not $obj.bt_port) { $portDownBt++ }
    if (-not $obj.dash_port) { $portDownDash++ }

    Start-Sleep -Milliseconds ([Math]::Max(100, $StepDelayMs))
    if ((Get-Date) -ge $end) { break }
  }
}

$avg = 0
$p95 = 0
if ($lat.Count -gt 0) {
  $avg = [int][Math]::Round(($lat | Measure-Object -Average).Average, 0)
  $sorted = $lat | Sort-Object
  $idx = [int][Math]::Floor(0.95 * ($sorted.Count - 1))
  $p95 = $sorted[$idx]
}

$okRate = if ($count -gt 0) { [Math]::Round((($count - $fails) / $count), 4) } else { 0.0 }
$apiUpRate = if ($count -gt 0) { [Math]::Round((($count - $portDownApi) / $count), 4) } else { 0.0 }

$md = @()
$md += "# GUI Tab Switch Simulation (5 min)"
$md += ""
$md += "- run_dir: $runDir"
$md += "- total_requests: $count"
$md += "- failures: $fails"
$md += "- request_success_rate: $okRate"
$md += "- latency_avg_ms: $avg"
$md += "- latency_p95_ms: $p95"
$md += "- api_port_up_rate: $apiUpRate"
$md += "- dashboard_port_down_samples: $portDownDash"
$md += "- backtest_port_down_samples: $portDownBt"
$md += ""
$md += "## Gates"
$md += ""
$md += "| Gate | Target | Actual | Result |"
$md += "|---|---:|---:|---|"
$g1 = if ($okRate -ge 0.98) { "PASS" } else { "FAIL" }
$g2 = if ($p95 -le 3000) { "PASS" } else { "FAIL" }
$g3 = if ($apiUpRate -ge 0.995) { "PASS" } else { "FAIL" }
$md += "| Request success rate | 0.9800 | $okRate | $g1 |"
$md += "| Latency p95 (ms) | 3000 | $p95 | $g2 |"
$md += "| API port up rate | 0.9950 | $apiUpRate | $g3 |"

$md -join "`r`n" | Set-Content -Path $summaryPath -Encoding UTF8
Log "END requests=$count failures=$fails ok_rate=$okRate p95=$p95 api_up_rate=$apiUpRate"
Write-Host "Done: $runDir" -ForegroundColor Green
