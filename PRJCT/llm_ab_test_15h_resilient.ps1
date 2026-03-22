param(
  [int]$PhaseHours = 15,
  [int]$SampleMinutes = 20,
  [int]$ShadowHorizonMin = 60
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$root = 'C:\aiinvest'
$envPath = Join-Path $root 'python-core\.env'
$outRoot = Join-Path $root '_llm_tests'
$runTs = Get-Date -Format 'yyyyMMdd-HHmmss'
$runDir = Join-Path $outRoot ("ab15h-" + $runTs)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
Copy-Item $envPath (Join-Path $runDir 'env.before') -Force
$phases = @(
  @{ Name='Qwen3B'; Hours=$PhaseHours; Model='C:\aiinvest\models\qwen2.5-3b-instruct-q4_k_m.gguf' },
  @{ Name='Qwen7B'; Hours=$PhaseHours; Model='C:\aiinvest\models\qwen2.5-7b-instruct-q4_k_m-00001-of-00002.gguf' },
  @{ Name='Mistral7B'; Hours=$PhaseHours; Model='C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf' }
)
$phases | ConvertTo-Json -Depth 4 | Set-Content (Join-Path $runDir 'phases.json') -Encoding UTF8
function Log($msg){ "$(Get-Date -Format s) $msg" | Tee-Object -FilePath (Join-Path $runDir 'run.log') -Append | Out-Null }
function Set-EnvValue([string]$Key,[string]$Value){ $lines = Get-Content $envPath; $found = $false; for ($i=0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match "^\s*$([regex]::Escape($Key))=") { $lines[$i] = "$Key=$Value"; $found = $true; break } }; if (-not $found) { $lines += "$Key=$Value" }; Set-Content -Path $envPath -Value $lines -Encoding UTF8 }
function Stop-ByPort([int]$port){ $pids = @(netstat -ano | Select-String ":$port\s" | ForEach-Object { ($_ -split '\s+')[-1] } | Where-Object { $_ -match '^\d+$' } | Select-Object -Unique); foreach($pid in $pids){ try { Stop-Process -Id ([int]$pid) -Force -ErrorAction Stop } catch {} } }
function Stop-Stack{ Stop-ByPort 8010; Stop-ByPort 8001; Stop-ByPort 5173; Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $cl = ($_.CommandLine -as [string]); $_.Name -in @('python.exe','powershell.exe','cmd.exe','node.exe') -and ( $cl -like '*data_collector.py*' -or $cl -like '*market_data_worker.py*' -or $cl -like '*market_intel_worker.py*' -or $cl -like '*news_worker.py*' -or $cl -like '*uvicorn app:app*' -or $cl -like '*start_aiinvest.ps1*' -or $cl -like '*npm*run dev*' ) } | ForEach-Object { try { Stop-Process -Id $_.ProcessId -Force -ErrorAction Stop } catch {} }; Start-Sleep -Seconds 2 }
function Wait-Api([int]$TimeoutSec=240){ $deadline = (Get-Date).AddSeconds($TimeoutSec); while ((Get-Date) -lt $deadline) { try { $h = Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 4; if ($h.status -eq 'ok') { return $true } } catch {}; Start-Sleep -Seconds 2 }; return $false }
function Ensure-Stack { param([string]$PhaseName,[string]$ModelPath); Set-EnvValue -Key 'LLAMA_MODEL_PATH' -Value $ModelPath; Stop-Stack; & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_aiinvest.ps1') -CleanFirst | Out-Null; if (-not (Wait-Api 300)) { Log "WARN[$PhaseName] API did not come up, retry once"; Stop-Stack; & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $root 'start_aiinvest.ps1') -CleanFirst | Out-Null; $null = Wait-Api 300 } }
function Ensure-WorkersHealthy([string]$PhaseName){ $collector = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue | Where-Object { ($_.CommandLine -as [string]) -like '*data_collector.py*' }; $apiUp = $false; try { $apiUp = ((Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 4).status -eq 'ok') } catch {}; if ((-not $apiUp) -or ($collector.Count -eq 0)) { Log "HEAL[$PhaseName] apiUp=$apiUp collector=$($collector.Count) -> restart"; Ensure-Stack -PhaseName $PhaseName -ModelPath ((Get-Content $envPath | Select-String '^LLAMA_MODEL_PATH=').ToString().Split('=')[1]) } }
function Capture-Metrics([string]$Phase,[string]$Model){ $ts = (Get-Date).ToString('o'); $obj = [ordered]@{ t=$ts; phase=$Phase; model=$Model }; try { $obj.health = (Invoke-RestMethod 'http://localhost:8010/health' -TimeoutSec 6).status } catch { $obj.health = 'down' }; try { $obj.status = Invoke-RestMethod 'http://localhost:8010/bot/status' -TimeoutSec 8 } catch { $obj.status = $null }; try { $sr = Invoke-RestMethod ("http://localhost:8010/bot/signal-quality/shadow-report?lookback_hours=720&horizon_min={0}&limit=10000&actions=shadow,policy,executed" -f $ShadowHorizonMin) -TimeoutSec 35; $obj.shadow = [ordered]@{ total = $sr.counts.total; total_dedup = $sr.counts.total_dedup; shadow = $sr.counts.shadow; policy = $sr.counts.policy; executed = $sr.counts.executed; eval_input = $sr.counts.eval_input; eval_dedup_dropped = $sr.counts.eval_dedup_dropped; shadow_eval_samples = $sr.summary.shadow_eval_samples; win_rate = $sr.summary.shadow_win_rate_h; pf = $sr.summary.shadow_profit_factor_h; avg_ret = $sr.summary.shadow_avg_ret_h } } catch { $obj.shadow = $null }; ($obj | ConvertTo-Json -Depth 8 -Compress) | Add-Content (Join-Path $runDir 'metrics.jsonl') -Encoding UTF8 }
function Build-FinalReport { $p = Join-Path $runDir 'metrics.jsonl'; if (-not (Test-Path $p)) { return }; $rows = Get-Content $p | ForEach-Object { $_ | ConvertFrom-Json }; $sum = @(); foreach($name in @('Qwen3B','Qwen7B','Mistral7B')){ $it = @($rows | Where-Object { $_.phase -eq $name } | Sort-Object {[datetime]$_.t}); if ($it.Count -lt 2) { continue }; $f=$it[0]; $l=$it[-1]; $sum += [pscustomobject]@{ phase=$name; samples=$it.Count; health_ok=(@($it | Where-Object {$_.health -eq 'ok'}).Count); total_delta=($l.shadow.total-$f.shadow.total); dedup_delta=($l.shadow.total_dedup-$f.shadow.total_dedup); shadow_delta=($l.shadow.shadow-$f.shadow.shadow); policy_delta=($l.shadow.policy-$f.shadow.policy); eval_delta=($l.shadow.shadow_eval_samples-$f.shadow.shadow_eval_samples); pf_end=$l.shadow.pf; win_rate_end=$l.shadow.win_rate } }; $sum | ConvertTo-Json -Depth 5 | Set-Content (Join-Path $runDir 'final_summary.json') -Encoding UTF8; $best = $sum | Sort-Object -Property @{Expression='eval_delta';Descending=$true}, @{Expression='shadow_delta';Descending=$true}, @{Expression='pf_end';Descending=$true} | Select-Object -First 1; $txt = @(); $txt += "RunDir: $runDir"; $txt += "Finished: $(Get-Date -Format s)"; $txt += "BestPhaseByEvalShadowPf: $($best.phase)"; $txt += ($sum | Format-Table -AutoSize | Out-String); Set-Content (Join-Path $runDir 'final_report.txt') $txt -Encoding UTF8 }
Log "START runDir=$runDir phaseHours=$PhaseHours sampleMin=$SampleMinutes horizon=$ShadowHorizonMin"
foreach ($p in $phases) { Log "PHASE $($p.Name) START model=$($p.Model)"; Ensure-Stack -PhaseName $p.Name -ModelPath $p.Model; $ticks = [Math]::Max(1, [int]([Math]::Floor(($p.Hours * 60) / $SampleMinutes))); for ($i=0; $i -lt $ticks; $i++) { Ensure-WorkersHealthy -PhaseName $p.Name; Capture-Metrics -Phase $p.Name -Model $p.Model; Start-Sleep -Seconds ($SampleMinutes * 60) }; Log "PHASE $($p.Name) END" }
Set-EnvValue -Key 'LLAMA_MODEL_PATH' -Value 'C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf'
Ensure-Stack -PhaseName 'FINAL_MISTRAL_ACTIVE' -ModelPath 'C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf'
Capture-Metrics -Phase 'FINAL_MISTRAL_ACTIVE' -Model 'C:\aiinvest\models\mistral-7b-instruct-v0.2.Q4_K_M.gguf'
Build-FinalReport
Log "END"
