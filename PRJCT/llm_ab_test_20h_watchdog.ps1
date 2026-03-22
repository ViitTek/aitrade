param(
  [int]$PhaseHours = 20,
  [int]$SampleMinutes = 20,
  [int]$MainHorizonMin = 60,
  [int]$ControlHorizonMin = 120,
  [string]$EarlyHorizons = "15,30,45",
  [int]$WarmupSamples = 2,
  [int]$WarmupDelaySec = 30
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = 'C:\aiinvest'
$envPath = Join-Path $root 'python-core\.env'
$outRoot = Join-Path $root '_llm_tests'
$runTs = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $outRoot ("ab20h-watchdog-" + $runTs)
$heartbeatPath = Join-Path $runDir 'heartbeat.json'
$runLogPath = Join-Path $runDir 'run.log'
$metricsPath = Join-Path $runDir 'metrics.jsonl'

New-Item -ItemType Directory -Path $runDir -Force | Out-Null
Copy-Item $envPath (Join-Path $runDir 'env.before') -Force

$earlyList = @()
foreach($x in ($EarlyHorizons -split ',')){
  $v = 0
  if([int]::TryParse($x.Trim(), [ref]$v) -and $v -gt 0){ $earlyList += $v }
}
$earlyList = $earlyList | Sort-Object -Unique

$phases = @(
  @{ Name='Qwen3B'; Hours=$PhaseHours; Model='C:\aiinvest\models\qwen2.5-3b-instruct-q4_k_m.gguf' },
  @{ Name='Qwen7B'; Hours=$PhaseHours; Model='C:\aiinvest\models\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf' },
  @{ Name='Mistral7B'; Hours=$PhaseHours; Model='C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf' }
)
$phases | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $runDir 'phases.json') -Encoding UTF8

function Log([string]$msg){
  "$(Get-Date -Format s) $msg" | Tee-Object -FilePath $runLogPath -Append | Out-Null
}

function Write-Heartbeat([string]$phase, [string]$state){
  $hb = [ordered]@{
    t = (Get-Date).ToString('o')
    phase = $phase
    state = $state
    pid = $PID
  }
  $hb | ConvertTo-Json -Depth 4 | Set-Content -Path $heartbeatPath -Encoding UTF8
}

function Set-EnvValue([string]$Key,[string]$Value){
  $lines = Get-Content $envPath
  $found = $false
  for ($i=0; $i -lt $lines.Count; $i++) {
    if ($lines[$i] -match "^\s*$([regex]::Escape($Key))=") {
      $lines[$i] = "$Key=$Value"
      $found = $true
      break
    }
  }
  if (-not $found) { $lines += "$Key=$Value" }
  Set-Content -Path $envPath -Value $lines -Encoding UTF8
}

function Stop-ByPort([int]$port){
  $pids = @(netstat -ano | Select-String ":$port\s" | ForEach-Object { ($_ -split '\s+')[-1] } | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique)
  foreach($procId in $pids){
    try { Stop-Process -Id ([int]$procId) -Force -ErrorAction Stop } catch {}
  }
}

function Stop-Stack {
  Stop-ByPort 8010
  Stop-ByPort 8001
  Stop-ByPort 5173
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $cl = ($_.CommandLine -as [string])
    ($_.Name -in @('python.exe','node.exe','cmd.exe','powershell.exe')) -and (
      $cl -like '*data_collector.py*' -or
      $cl -like '*uvicorn app:app*' -or
      $cl -like '*npm*run dev*' -or
      $cl -like '*market_data_worker.py*' -or
      $cl -like '*market_intel_worker.py*' -or
      $cl -like '*news_worker.py*'
    )
  } | ForEach-Object {
    try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {}
  }
  Start-Sleep -Seconds 2
}

function Wait-Api([int]$TimeoutSec=300){
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $h = Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 4
      if ($h.status -eq 'ok') { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  return $false
}

function Ensure-Stack([string]$phaseName,[string]$modelPath){
  Write-Heartbeat -phase $phaseName -state 'ensure_stack'
  Set-EnvValue -Key 'LLAMA_MODEL_PATH' -Value $modelPath
  Stop-Stack
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_aiinvest.ps1') -CleanFirst | Out-Null
  if (-not (Wait-Api 300)) {
    Log "WARN[$phaseName] API not ready after start, retrying"
    Stop-Stack
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_aiinvest.ps1') -CleanFirst | Out-Null
    if (-not (Wait-Api 300)) { throw "API failed to start in phase $phaseName" }
  }
}

function Ensure-Components([string]$phaseName,[string]$modelPath){
  $apiOk = $false
  try { $apiOk = ((Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 4).status -eq 'ok') } catch {}

  $collectorCount = @(
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
      Where-Object { ($_.CommandLine -as [string]) -like '*data_collector.py*' }
  ).Count

  if ((-not $apiOk) -or ($collectorCount -lt 1)) {
    Log "HEAL[$phaseName] apiOk=$apiOk collectorCount=$collectorCount -> restarting stack"
    Ensure-Stack -phaseName $phaseName -modelPath $modelPath
  }
}

function Get-Shadow([int]$horizon){
  try {
    $url = "http://localhost:8010/bot/signal-quality/shadow-report?lookback_hours=720&horizon_min=$horizon&limit=10000&actions=shadow,policy,executed"
    $sr = Invoke-RestMethod $url -TimeoutSec 35
    return [ordered]@{
      horizon = $horizon
      total = $sr.counts.total
      total_dedup = $sr.counts.total_dedup
      shadow = $sr.counts.shadow
      policy = $sr.counts.policy
      executed = $sr.counts.executed
      eval_input = $sr.counts.eval_input
      eval_dedup_dropped = $sr.counts.eval_dedup_dropped
      shadow_eval_samples = $sr.summary.shadow_eval_samples
      win_rate = $sr.summary.shadow_win_rate_h
      pf = $sr.summary.shadow_profit_factor_h
      avg_ret = $sr.summary.shadow_avg_ret_h
    }
  } catch {
    return [ordered]@{ horizon = $horizon; error = $_.Exception.Message }
  }
}

function Capture-Metrics([string]$phase,[string]$model,[int]$tick){
  Write-Heartbeat -phase $phase -state "capture_$tick"
  $obj = [ordered]@{
    t = (Get-Date).ToString('o')
    phase = $phase
    model = $model
    tick = $tick
    main = Get-Shadow -horizon $MainHorizonMin
    control = Get-Shadow -horizon $ControlHorizonMin
    early = @{}
  }
  foreach($eh in $earlyList){
    $obj.early["h$eh"] = Get-Shadow -horizon $eh
  }
  try { $obj.health = (Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 6).status } catch { $obj.health = 'down' }
  try { $obj.status = Invoke-RestMethod 'http://localhost:8010/bot/status' -TimeoutSec 8 } catch { $obj.status = $null }

  ($obj | ConvertTo-Json -Depth 10 -Compress) | Add-Content $metricsPath -Encoding UTF8
}

function Build-FinalReport {
  if (-not (Test-Path $metricsPath)) { return }
  $rows = Get-Content $metricsPath | ForEach-Object { $_ | ConvertFrom-Json }
  $sum = @()
  foreach($name in @('Qwen3B','Qwen7B','Mistral7B')){
    $it = @($rows | Where-Object { $_.phase -eq $name } | Sort-Object {[datetime]$_.t})
    if($it.Count -lt 2){ continue }
    $f = $it[0]; $l = $it[-1]
    $sum += [pscustomobject]@{
      phase = $name
      samples = $it.Count
      health_ok = (@($it | Where-Object { $_.health -eq 'ok' }).Count)
      main60_eval_delta = ($l.main.shadow_eval_samples - $f.main.shadow_eval_samples)
      main60_shadow_delta = ($l.main.shadow - $f.main.shadow)
      main60_pf_end = $l.main.pf
      control120_eval_delta = ($l.control.shadow_eval_samples - $f.control.shadow_eval_samples)
      control120_pf_end = $l.control.pf
    }
  }
  $sum | ConvertTo-Json -Depth 6 | Set-Content (Join-Path $runDir 'final_summary.json') -Encoding UTF8
  $txt = @()
  $txt += "RunDir: $runDir"
  $txt += "Finished: $(Get-Date -Format s)"
  $txt += "Auto-tune decision basis: main60 + control120 (early 15/30/45 pouze signalizace)."
  $txt += ($sum | Format-Table -AutoSize | Out-String)
  Set-Content (Join-Path $runDir 'final_report.txt') -Value $txt -Encoding UTF8
}

Log "START runDir=$runDir phaseHours=$PhaseHours sampleMin=$SampleMinutes main=$MainHorizonMin control=$ControlHorizonMin early=$EarlyHorizons"

foreach($p in $phases){
  $phase = $p.Name
  $model = $p.Model
  $ticks = [Math]::Max(1, [int]([Math]::Floor(($p.Hours * 60) / $SampleMinutes)))
  Log "PHASE $phase START model=$model ticks=$ticks"

  Ensure-Stack -phaseName $phase -modelPath $model

  # quick proof samples
  for($w=1; $w -le $WarmupSamples; $w++){
    try {
      Ensure-Components -phaseName $phase -modelPath $model
      Capture-Metrics -phase $phase -model $model -tick $w
      Log "WARMUP[$phase] sample=$w done"
    } catch {
      Log "ERR[$phase] warmup sample=$w failed: $($_.Exception.Message)"
      Ensure-Stack -phaseName $phase -modelPath $model
    }
    if($w -lt $WarmupSamples){ Start-Sleep -Seconds $WarmupDelaySec }
  }

  for($i=1; $i -le $ticks; $i++){
    try {
      Ensure-Components -phaseName $phase -modelPath $model
      Capture-Metrics -phase $phase -model $model -tick ($WarmupSamples + $i)
      Log "TICK[$phase] $i/$ticks done"
    } catch {
      Log "ERR[$phase] tick=$i failed: $($_.Exception.Message)"
      Ensure-Stack -phaseName $phase -modelPath $model
    }

    # watchdog: stale heartbeat check
    try {
      if(Test-Path $heartbeatPath){
        $hb = Get-Content $heartbeatPath -Raw | ConvertFrom-Json
        $hbt = [datetime]$hb.t
        $ageSec = [int]((Get-Date) - $hbt).TotalSeconds
        $maxAge = [Math]::Max(180, ($SampleMinutes * 60) + 300)
        if($ageSec -gt $maxAge){
          Log "WATCHDOG[$phase] stale heartbeat ageSec=$ageSec max=$maxAge -> heal"
          Ensure-Stack -phaseName $phase -modelPath $model
        }
      }
    } catch {}

    Start-Sleep -Seconds ($SampleMinutes * 60)
  }

  Log "PHASE $phase END"
}

# keep runtime on Mistral
$finalModel = 'C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf'
Set-EnvValue -Key 'LLAMA_MODEL_PATH' -Value $finalModel
Ensure-Stack -phaseName 'FINAL_MISTRAL_ACTIVE' -modelPath $finalModel
Capture-Metrics -phase 'FINAL_MISTRAL_ACTIVE' -model $finalModel -tick 1
Build-FinalReport
Log "END"

