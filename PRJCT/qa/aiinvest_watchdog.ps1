param(
  [string]$ProjectRoot = "C:\aiinvest",
  [int]$ApiPort = 8010,
  [int]$BacktestPort = 8001,
  [int]$DashboardPort = 5173,
  [int]$LoopSeconds = 20,
  [int]$UnhealthyThreshold = 3
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$runTs = Get-Date -Format "yyyyMMdd-HHmmss"
$runDir = Join-Path $ProjectRoot ("qa\runs\watchdog-" + $runTs)
New-Item -ItemType Directory -Path $runDir -Force | Out-Null
$logPath = Join-Path $runDir "watchdog.log"

function Log([string]$msg) {
  $line = "$(Get-Date -Format s) $msg"
  $line | Tee-Object -FilePath $logPath -Append | Out-Null
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

function Test-Api() {
  try {
    $t0 = Get-Date
    $r = Invoke-RestMethod "http://localhost:$ApiPort/health" -TimeoutSec 6
    $ms = [int]((Get-Date) - $t0).TotalMilliseconds
    return [pscustomobject]@{ ok = ($r.status -eq "ok"); ms = $ms }
  } catch {
    return [pscustomobject]@{ ok = $false; ms = -1 }
  }
}

function Heal-Stack() {
  $startScript = Join-Path $ProjectRoot "start_aiinvest.ps1"
  if (-not (Test-Path $startScript)) {
    Log "HEAL_FAIL missing start script: $startScript"
    return
  }
  Log "HEAL start_aiinvest.ps1 -CleanFirst"
  try {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $startScript -CleanFirst | Out-Null
  } catch {
    Log "HEAL_FAIL $($_.Exception.Message)"
  }
}

Log "WATCHDOG_START api=$ApiPort bt=$BacktestPort dash=$DashboardPort loop=$LoopSeconds"
$failStreak = 0

while ($true) {
  $apiHealth = Test-Api
  $apiPortUp = Test-Port $ApiPort
  $btPortUp = Test-Port $BacktestPort
  $dashPortUp = Test-Port $DashboardPort

  $ok = ($apiHealth.ok -and $apiPortUp -and $btPortUp)
  if ($ok) {
    $failStreak = 0
    Log "OK api_ms=$($apiHealth.ms) api_port=$apiPortUp bt_port=$btPortUp dash_port=$dashPortUp"
  } else {
    $failStreak++
    Log "WARN fail_streak=$failStreak api_ok=$($apiHealth.ok) api_ms=$($apiHealth.ms) api_port=$apiPortUp bt_port=$btPortUp dash_port=$dashPortUp"
    if ($failStreak -ge $UnhealthyThreshold) {
      Heal-Stack
      $failStreak = 0
      Start-Sleep -Seconds 8
    }
  }

  Start-Sleep -Seconds ([Math]::Max(5, $LoopSeconds))
}
