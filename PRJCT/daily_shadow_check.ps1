param(
    [string]$ApiBase = "http://localhost:8010",
    [int]$LookbackHours = 720,
    [int]$HorizonMin = 120,
    [int]$Limit = 10000
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$root = "C:\aiinvest"
$logDir = Join-Path $root "_Kontext\daily_checks"
if (-not (Test-Path $logDir)) {
    New-Item -ItemType Directory -Path $logDir -Force | Out-Null
}

$ts = Get-Date
$stamp = $ts.ToString("yyyy-MM-dd_HH-mm-ss")
$logPath = Join-Path $logDir ("shadow_check_{0}.json" -f $stamp)

$status = Invoke-RestMethod ("{0}/bot/status" -f $ApiBase)
$coverage = Invoke-RestMethod ("{0}/bot/data-coverage?lookback_days=60&tf=60" -f $ApiBase)
$shadow = Invoke-RestMethod ("{0}/bot/signal-quality/shadow-report?lookback_hours={1}&horizon_min={2}&limit={3}&actions=shadow,policy,executed" -f $ApiBase, $LookbackHours, $HorizonMin, $Limit)

$report = [ordered]@{
    created_at = $ts.ToString("o")
    api_base = $ApiBase
    run_id = $status.run_id
    bot_running = [bool]$status.running
    workers = $status.workers
    shadow_summary = $shadow.summary
    shadow_counts = $shadow.counts
    shadow_window = $shadow.window
    top_symbols_coverage = @($coverage.symbols | Select-Object -First 9)
}

$report | ConvertTo-Json -Depth 8 | Set-Content -Path $logPath -Encoding UTF8

$line = "{0} | run={1} | samples={2} | wr={3} | pf={4}" -f `
    $ts.ToString("o"), `
    $shadow.run_id, `
    $shadow.summary.shadow_eval_samples, `
    $shadow.summary.shadow_win_rate_h, `
    $shadow.summary.shadow_profit_factor_h

$summaryPath = Join-Path $logDir "daily_summary.log"
Add-Content -Path $summaryPath -Value $line -Encoding UTF8

Write-Output ("Saved: {0}" -f $logPath)
Write-Output $line
