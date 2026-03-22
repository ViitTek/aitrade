param(
  [Parameter(Mandatory=$true)][string]$RunDir,
  [string]$ProjectRoot = 'C:\aiinvest'
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$metricsPath = Join-Path $RunDir 'metrics.jsonl'
$runLogPath = Join-Path $RunDir 'run.log'
if(-not (Test-Path $metricsPath)){ throw "metrics.jsonl missing: $metricsPath" }

$rows = Get-Content $metricsPath | ForEach-Object { $_ | ConvertFrom-Json }
if($rows.Count -lt 2){ throw 'Not enough metrics rows for review.' }

$phases = @('Qwen3B','Qwen7B','Mistral7B')
$phaseStats = @()
foreach($p in $phases){
  $it = @($rows | Where-Object { $_.phase -eq $p } | Sort-Object {[datetime]$_.t})
  if($it.Count -lt 2){ continue }
  $f = $it[0]
  $l = $it[-1]
  $evalDelta = [int]($l.main.shadow_eval_samples - $f.main.shadow_eval_samples)
  $totalDelta = [int]($l.main.total - $f.main.total)
  $dedupDelta = [int]($l.main.total_dedup - $f.main.total_dedup)
  $uniqueRatio = if([double]$l.main.total -gt 0){ [math]::Round(([double]$l.main.total_dedup/[double]$l.main.total),4) } else { 0.0 }
  $dedupImpact = if([double]$l.main.total -gt 0){ [math]::Round(([double]$l.main.eval_dedup_dropped/[double]$l.main.total),4) } else { 0.0 }

  $pfSeries = @($it | ForEach-Object { [double]$_.main.pf })
  $wrSeries = @($it | ForEach-Object { [double]$_.main.win_rate })
  $pfTrend = [math]::Round(($pfSeries[-1] - $pfSeries[0]),4)
  $wrTrend = [math]::Round(($wrSeries[-1] - $wrSeries[0]),4)

  $phaseStats += [pscustomobject]@{
    phase = $p
    samples = $it.Count
    main60_pf = [double]$l.main.pf
    main60_wr = [double]$l.main.win_rate
    main60_eval = [int]$l.main.shadow_eval_samples
    control120_pf = [double]$l.control.pf
    control120_wr = [double]$l.control.win_rate
    control120_eval = [int]$l.control.shadow_eval_samples
    main60_pf_trend = $pfTrend
    main60_wr_trend = $wrTrend
    new_eval_samples = $evalDelta
    total_events_delta = $totalDelta
    unique_candidates_delta = $dedupDelta
    unique_to_total_ratio = $uniqueRatio
    dedup_impact_ratio = $dedupImpact
  }
}

if($phaseStats.Count -eq 0){ throw 'No completed phases with >=2 samples.' }

# Winner score: main60 PF, WR, trend stability, control120, new eval, unique ratio
$winner = $phaseStats |
  Sort-Object -Property \
    @{Expression='main60_pf';Descending=$true},
    @{Expression='main60_wr';Descending=$true},
    @{Expression='main60_pf_trend';Descending=$true},
    @{Expression='control120_pf';Descending=$true},
    @{Expression='new_eval_samples';Descending=$true},
    @{Expression='unique_to_total_ratio';Descending=$true} |
  Select-Object -First 1

# Decision matrix (aii-next)
$decision = ''
$actions = @()
if(($winner.main60_pf -ge 1.0) -and ($winner.main60_pf_trend -ge 0)){
  $decision = 'VARIANTA_1'
  $actions += 'Vybrat vitezny model jako produkcni advisory model.'
  $actions += 'Nemenit hned strategii, jen model profil/path.'
  $actions += 'Spustit 1 overovaci shadow beh po nasazeni.'
}
elseif(($winner.main60_pf -lt 1.0) -and ($winner.control120_pf -gt 0.9 -or $winner.main60_pf_trend -gt 0)){
  $decision = 'VARIANTA_2'
  $actions += 'Model nezahazovat, snizit agresivitu.'
  $actions += 'Ladit risk multiplier, quality gate, symbol filtering.'
  $actions += 'Otestovat vhodnost modelu pro delsi horizont nez intraday.'
}
else{
  $decision = 'VARIANTA_3'
  $actions += 'Vratit se na nejstabilnejsi predchozi config/model.'
  $actions += 'Nezacit prompt tuningem, nejdriv quality gate + universe + vstupni filtry.'
  $actions += 'Revidovat deduplikacni logiku nebo interpretaci kandidatu.'
}

$checklist = @()
$checklist += [pscustomobject]@{ item='Winner on main60'; value=$winner.phase }
$checklist += [pscustomobject]@{ item='main60 PF'; value=$winner.main60_pf }
$checklist += [pscustomobject]@{ item='main60 WR'; value=$winner.main60_wr }
$checklist += [pscustomobject]@{ item='Trend stability (PF delta)'; value=$winner.main60_pf_trend }
$checklist += [pscustomobject]@{ item='control120 PF'; value=$winner.control120_pf }
$checklist += [pscustomobject]@{ item='New eval samples'; value=$winner.new_eval_samples }
$checklist += [pscustomobject]@{ item='Unique/total ratio'; value=$winner.unique_to_total_ratio }
$checklist += [pscustomobject]@{ item='Dedup impact ratio'; value=$winner.dedup_impact_ratio }
$checklist += [pscustomobject]@{ item='LLM value mode'; value='Primarne risk reduction/blockace ztratovych vstupu, sekundarne alpha.' }

$outJson = [pscustomobject]@{
  generated_at = (Get-Date).ToString('o')
  run_dir = $RunDir
  decision_variant = $decision
  winner = $winner
  phase_stats = $phaseStats
  checklist = $checklist
  actions = $actions
}

$outJson | ConvertTo-Json -Depth 8 | Set-Content (Join-Path $RunDir 'post_test_review.json') -Encoding UTF8

$txt = @()
$txt += 'AIInvest post-test review'
$txt += "Generated: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))"
$txt += "RunDir: $RunDir"
$txt += "Decision: $decision"
$txt += ''
$txt += 'Checklist:'
$txt += ($checklist | Format-Table -AutoSize | Out-String)
$txt += 'Per-phase stats:'
$txt += ($phaseStats | Format-Table -AutoSize | Out-String)
$txt += 'Actions:'
foreach($a in $actions){ $txt += "- $a" }
$txt | Set-Content (Join-Path $RunDir 'post_test_review.txt') -Encoding UTF8

# Update aii-act with a short post-test section
$actPath = Join-Path $ProjectRoot 'aii-act.txt'
$section = @()
$section += ''
$section += 'POST-TEST REVIEW (auto-generated)'
$section += "Date: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss'))"
$section += "Run: $RunDir"
$section += "Decision: $decision"
$section += "Winner: $($winner.phase)"
$section += "main60 PF/WR: $($winner.main60_pf) / $($winner.main60_wr)"
$section += "control120 PF: $($winner.control120_pf)"
$section += "New eval samples: $($winner.new_eval_samples)"
$section += "Unique/total ratio: $($winner.unique_to_total_ratio)"
$section += "Dedup impact ratio: $($winner.dedup_impact_ratio)"
$section += 'Recommended actions:'
foreach($a in $actions){ $section += "- $a" }
if(Test-Path $actPath){ Add-Content $actPath -Value ($section -join "`r`n") -Encoding UTF8 }

Write-Output "POST_TEST_REVIEW_DONE: $RunDir"

