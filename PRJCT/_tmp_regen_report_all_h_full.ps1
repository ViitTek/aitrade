$ErrorActionPreference = 'Stop'
$root = 'C:\aiinvest'
$projectDir = Join-Path $root 'PRJCT'
$reportsDir = Join-Path $root 'RPRTS'
$runDir = Join-Path $reportsDir '_shadow_tests\shadow-suite-20260305-152706'
$csvPath = Join-Path $runDir 'report_all_h_full.csv'
$mdPath = Join-Path $runDir 'report_all_h_full.md'

$state = Get-Content (Join-Path $runDir 'state.json') -Raw | ConvertFrom-Json
$started = [datetimeoffset]$state.started_at
$now = [datetimeoffset](Get-Date)
$runId = '72208ec2578f'
try { $st = Invoke-RestMethod 'http://127.0.0.1:8010/bot/status' -TimeoutSec 8; if($st.run_id){ $runId = [string]$st.run_id } } catch {}

$horizons = @(15,30,45) + (60..10080 | Where-Object { $_ % 60 -eq 0 })

function F([double]$x,[int]$d=4){ return $x.ToString("F$d", [System.Globalization.CultureInfo]::GetCultureInfo('cs-CZ')) }

$rows = @()

foreach($h in $horizons){
  try {
    $u = "http://127.0.0.1:8010/bot/signal-quality/shadow-report?lookback_hours=720&horizon_min=$h&limit=10000&actions=shadow,policy,executed"
    $sr = Invoke-RestMethod $u -TimeoutSec 25

    $pnlRaw = & (Join-Path $projectDir 'python-core\venv\Scripts\python.exe') (Join-Path $projectDir 'python-core\shadow_local_pnl.py') `
      --run-id $runId `
      --from-iso $started.ToString('o') `
      --to-iso $now.ToString('o') `
      --horizon-min $h `
      --actions 'shadow,executed' `
      --kraken-eur 100 --binance-eur 100 --ibkr-eur 100 `
      --stake-pct 0.10 `
      --binance-fee-rate 0.001 --kraken-fee-rate 0.0025 --ibkr-fee-rate 0.0 `
      --kraken-bases 'BTC,ETH' --binance-bases 'SOL,BNB,DOGE,TRX,XRP,PAXG,USDC' --ibkr-bases ''
    $pnl = $pnlRaw | ConvertFrom-Json

    $day = if($h -le 1440){ 1 } else { [int][math]::Ceiling($h/1440.0) }
    $rows += [pscustomobject]@{
      day = $day
      h = $h
      total = [int]$sr.counts.total
      dedup = [int]$sr.counts.total_dedup
      shadow = [int]$sr.counts.shadow
      policy = [int]$sr.counts.policy
      executed = [int]$sr.counts.executed
      eval = [int]$sr.summary.shadow_eval_samples
      wr = [double]$sr.summary.shadow_win_rate_h
      pf = [double]$sr.summary.shadow_profit_factor_h
      avg_ret = [double]$sr.summary.shadow_avg_ret_h
      kraken_eq = [double]$pnl.kraken.equity
      binance_eq = [double]$pnl.binance.equity
      ibkr_eq = [double]$pnl.ibkr.equity
      total_eq = [double]$pnl.total_equity
      total_pnl_eur = [double]$pnl.total_pnl_eur
      fees = [double]$pnl.total_fees
    }
  } catch {
    $rows += [pscustomobject]@{
      day = if($h -le 1440){ 1 } else { [int][math]::Ceiling($h/1440.0) }
      h = $h; total = 0; dedup = 0; shadow = 0; policy = 0; executed = 0; eval = 0;
      wr = 0.0; pf = 0.0; avg_ret = 0.0; kraken_eq = 100.0; binance_eq = 100.0; ibkr_eq = 100.0; total_eq = 300.0; total_pnl_eur = 0.0; fees = 0.0
    }
  }
}

$csvRows = foreach($r in $rows){
  [pscustomobject]@{
    day = $r.day; h = $r.h; total = $r.total; dedup = $r.dedup; shadow = $r.shadow; policy = $r.policy; executed = $r.executed; eval = $r.eval;
    wr = F $r.wr 4; pf = F $r.pf 4; avg_ret = F $r.avg_ret 6;
    kraken_eq = F $r.kraken_eq 4; binance_eq = F $r.binance_eq 4; ibkr_eq = F $r.ibkr_eq 4;
    total_eq = F $r.total_eq 4; total_pnl_eur = F $r.total_pnl_eur 4; fees = F $r.fees 4
  }
}
$csvRows | Export-Csv -Path $csvPath -NoTypeInformation -Encoding UTF8

$md = @()
$md += '# Full Report All Horizons'
$md += ''
$md += "Generated: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
$md += "RunDir: $($runDir.ToLower())"
$md += "RunId: $runId"
$md += "Window: $($started.ToString('o')) -> $($now.ToString('o'))"
$md += 'Horizons: 15,30,45,60..10080 (step 60)'
$md += ''
$md += '| day | h | total | dedup | shadow | policy | executed | eval | WR | PF | avg_ret | KrakenEqEUR | BinanceEqEUR | IBKREqEUR | TotalEqEUR | TotalPnLEUR | FeesEUR |'
$md += '|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|'
foreach($r in $rows){
  $md += "| $($r.day) | $($r.h) | $($r.total) | $($r.dedup) | $($r.shadow) | $($r.policy) | $($r.executed) | $($r.eval) | $(F $r.wr 4) | $(F $r.pf 4) | $(F $r.avg_ret 6) | $(F $r.kraken_eq 4) | $(F $r.binance_eq 4) | $(F $r.ibkr_eq 4) | $(F $r.total_eq 4) | $(F $r.total_pnl_eur 4) | $(F $r.fees 4) |"
}
Set-Content -Path $mdPath -Value $md -Encoding UTF8

Write-Output "UPDATED: $csvPath"
Write-Output "UPDATED: $mdPath"
Write-Output "ROWS: $($rows.Count)"
