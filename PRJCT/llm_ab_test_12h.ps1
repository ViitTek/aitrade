param(
  [int]$PhaseMinutes = 720,
  [int]$SampleMinutes = 20
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$root = 'C:\aiinvest'
$envPath = Join-Path $root 'python-core\.env'
$outRoot = Join-Path $root '_llm_tests'
$runTs = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $outRoot ("ab12h-" + $runTs)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null

$backupEnv = Join-Path $runDir 'env.before'
Copy-Item $envPath $backupEnv -Force

$phases = @(
  @{ Name='Qwen3B'; Minutes=$PhaseMinutes; Model='C:\aiinvest\models\qwen2.5-3b-instruct-q4_k_m.gguf' },
  @{ Name='Qwen7B'; Minutes=$PhaseMinutes; Model='C:\aiinvest\models\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf' },
  @{ Name='Mistral7B'; Minutes=$PhaseMinutes; Model='C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf' }
)
$phases | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $runDir 'phases.json') -Encoding UTF8

function Set-EnvValue {
  param([string]$Key,[string]$Value)
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

function Wait-Api {
  param([int]$TimeoutSec=180)
  $deadline = (Get-Date).AddSeconds($TimeoutSec)
  while ((Get-Date) -lt $deadline) {
    try {
      $h = Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 3
      if ($h.status -eq 'ok') { return $true }
    } catch {}
    Start-Sleep -Seconds 2
  }
  return $false
}

function Capture-Metrics {
  param([string]$Phase,[string]$Model)
  $ts = (Get-Date).ToString('o')
  $obj = [ordered]@{ t=$ts; phase=$Phase; model=$Model }
  try { $obj.health = (Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 5).status } catch { $obj.health = 'down' }
  try {
    $sr = Invoke-RestMethod "http://localhost:8010/bot/signal-quality/shadow-report?lookback_hours=720&horizon_min=120&limit=10000&actions=shadow,policy,executed" -TimeoutSec 25
    $obj.shadow = [ordered]@{
      total = $sr.counts.total
      total_dedup = $sr.counts.total_dedup
      shadow = $sr.counts.shadow
      policy = $sr.counts.policy
      executed = $sr.counts.executed
      eval_input = $sr.counts.eval_input
      shadow_eval_samples = $sr.summary.shadow_eval_samples
      win_rate = $sr.summary.shadow_win_rate_h
      pf = $sr.summary.shadow_profit_factor_h
      avg_ret = $sr.summary.shadow_avg_ret_h
    }
  } catch {
    $obj.shadow = $null
  }
  ($obj | ConvertTo-Json -Depth 8 -Compress) | Add-Content (Join-Path $runDir 'metrics.jsonl') -Encoding UTF8
}

"START $(Get-Date -Format s)" | Set-Content (Join-Path $runDir 'run.log') -Encoding UTF8
foreach ($p in $phases) {
  "PHASE $($p.Name) START $(Get-Date -Format s)" | Add-Content (Join-Path $runDir 'run.log')
  Set-EnvValue -Key 'LLAMA_MODEL_PATH' -Value $p.Model
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_aiinvest.ps1') -CleanFirst | Out-Null
  $null = Wait-Api -TimeoutSec 240
  $ticks = [Math]::Max(1, [int]([Math]::Floor($p.Minutes / $SampleMinutes)))
  for ($i=0; $i -lt $ticks; $i++) {
    Capture-Metrics -Phase $p.Name -Model $p.Model
    Start-Sleep -Seconds ($SampleMinutes * 60)
  }
  "PHASE $($p.Name) END $(Get-Date -Format s)" | Add-Content (Join-Path $runDir 'run.log')
}
Set-EnvValue -Key 'LLAMA_MODEL_PATH' -Value 'C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf'
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_aiinvest.ps1') -CleanFirst | Out-Null
Capture-Metrics -Phase 'FINAL_MISTRAL_ACTIVE' -Model 'C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf'
"END $(Get-Date -Format s)" | Add-Content (Join-Path $runDir 'run.log')
