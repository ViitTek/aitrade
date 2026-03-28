param(
    [string]$ProjectRoot = "C:\aiinvest",
    [int]$KillDelaySec = 3,
    [int]$ArmTimeoutSec = 900,
    [int]$MonitorTimeoutSec = 900,
    [int]$PollSeconds = 2,
    [int]$MinUnlockWaitSec = 60,
    [string]$RunDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

Add-Type @"
using System;
using System.Runtime.InteropServices;
using System.Text;

public static class NativeDesktopState
{
    [DllImport("user32.dll", SetLastError = true)]
    public static extern IntPtr OpenInputDesktop(uint dwFlags, bool fInherit, uint dwDesiredAccess);

    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool CloseDesktop(IntPtr hDesktop);

    [DllImport("user32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    public static extern bool GetUserObjectInformation(IntPtr hObj, int nIndex, StringBuilder pvInfo, int nLength, ref int lpnLengthNeeded);
}
"@

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$layoutHelper = Join-Path $ScriptDir "resolve_aiinvest_layout.ps1"
if (Test-Path $layoutHelper) {
    . $layoutHelper
}

if (Get-Command Get-AIInvestLayout -ErrorAction SilentlyContinue) {
    $layout = Get-AIInvestLayout -RepoRoot $ProjectRoot
    $RepoRoot = $layout.RepoRoot
    $ProjectDir = $layout.ProjectDir
    $ReportsDir = $layout.ReportsDir
} else {
    $RepoRoot = $ProjectRoot
    $ProjectDir = Join-Path $RepoRoot "PRJCT"
    $ReportsDir = Join-Path $RepoRoot "RPRTS"
}

if ([string]::IsNullOrWhiteSpace($RunDir)) {
    $runTs = Get-Date -Format "yyyyMMdd-HHmmss"
    $RunDir = Join-Path (Join-Path $ReportsDir "_ibkr-lock-tests") ("ibkr-lock-test-" + $runTs)
}

New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
$logPath = Join-Path $RunDir "test.log"
$summaryPath = Join-Path $RunDir "summary.txt"
$resultPath = Join-Path $RunDir "result.json"
$statusPath = Join-Path $RunDir "status.json"
$script:CurrentState = $null

trap {
    $errorText = ($_ | Out-String).Trim()
    $timestamp = Get-Date -Format s

    try {
        Add-Content -Path $logPath -Value "$timestamp [IBKR-LOCK-TEST] unhandled error | $errorText"
    } catch {
    }

    try {
        $crashState = [ordered]@{
            run_dir = $RunDir
            status = "crashed"
            outcome = "fail"
            passed = $false
            failure_reason = $errorText
            crashed_at = (Get-Date).ToString("o")
        }

        if ($null -ne $script:CurrentState) {
            foreach ($entry in $script:CurrentState.GetEnumerator()) {
                $crashState[$entry.Key] = $entry.Value
            }
            $crashState["status"] = "crashed"
            $crashState["outcome"] = "fail"
            $crashState["passed"] = $false
            $crashState["failure_reason"] = $errorText
            $crashState["crashed_at"] = (Get-Date).ToString("o")
        }

        $crashState | ConvertTo-Json -Depth 8 | Set-Content -Path $statusPath -Encoding UTF8
        $crashState | ConvertTo-Json -Depth 8 | Set-Content -Path $resultPath -Encoding UTF8
        @(
            "IBKR lock test: FAIL"
            "RunDir: $RunDir"
            "Failure reason: $errorText"
        ) | Set-Content -Path $summaryPath -Encoding UTF8
    } catch {
    }

    exit 1
}

function Log {
    param([string]$Message)
    $line = "$(Get-Date -Format s) [IBKR-LOCK-TEST] $Message"
    $line | Tee-Object -FilePath $logPath -Append | Out-Null
    Write-Host $line
}

function Save-Json {
    param(
        [string]$Path,
        [object]$Data
    )

    $json = $Data | ConvertTo-Json -Depth 8
    $lastError = $null
    for ($attempt = 1; $attempt -le 8; $attempt++) {
        try {
            Set-Content -Path $Path -Value $json -Encoding UTF8
            return
        } catch {
            $lastError = $_
            Start-Sleep -Milliseconds (120 * $attempt)
        }
    }

    if ($null -ne $lastError) {
        throw $lastError
    }
}

function Save-Status {
    param([hashtable]$State)
    $snapshot = [ordered]@{}
    foreach ($key in $State.Keys) {
        $snapshot[$key] = $State[$key]
    }
    $snapshot["updated_at"] = (Get-Date).ToString("o")
    $script:CurrentState = $snapshot
    Save-Json -Path $statusPath -Data $snapshot
}

function Read-DotEnv {
    param([string]$Path)
    $values = @{}
    if (-not (Test-Path $Path)) { return $values }
    foreach ($line in Get-Content $Path) {
        $trimmed = if ($null -eq $line) { "" } else { [string]$line }
        $trimmed = $trimmed.Trim()
        if (-not $trimmed -or $trimmed.StartsWith("#") -or -not $trimmed.Contains("=")) { continue }
        $parts = $trimmed.Split("=", 2)
        if ($parts.Count -ne 2) { continue }
        $key = $parts[0].Trim()
        $value = $parts[1].Trim().Trim("'").Trim('"')
        if (-not [string]::IsNullOrWhiteSpace($key)) {
            $values[$key] = $value
        }
    }
    return $values
}

function Get-FirstEnvValue {
    param([hashtable]$Values, [string[]]$Names)
    foreach ($name in $Names) {
        if ($Values.ContainsKey($name)) {
            $value = [string]$Values[$name]
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                return $value.Trim()
            }
        }
    }
    return ""
}

function Get-IntValue {
    param($Value, [int]$Default)
    try {
        if ($null -eq $Value) { return $Default }
        $text = [string]$Value
        if ([string]::IsNullOrWhiteSpace($text)) { return $Default }
        return [int]$text
    } catch {
        return $Default
    }
}

function Normalize-TradingMode {
    param([string]$Mode)
    $raw = if ($null -eq $Mode) { "" } else { [string]$Mode }
    $raw = $raw.Trim().ToLowerInvariant()
    if ($raw -in @("live", "l")) { return "live" }
    return "paper"
}

function Get-PortCandidates {
    param([int]$ConfiguredPort, [string]$TradingMode)
    $ports = New-Object System.Collections.Generic.List[int]
    $preferred = if ($TradingMode -eq "live") { @(4001, 7496) } else { @(4002, 7497) }
    $fallback = if ($TradingMode -eq "live") { @(4002, 7497) } else { @(4001, 7496) }
    foreach ($port in @($ConfiguredPort) + $preferred + $fallback) {
        if ($port -le 0 -or $ports.Contains($port)) { continue }
        $ports.Add($port)
    }
    return $ports
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port, [int]$TimeoutMs = 1200)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne($TimeoutMs)
        if (-not $ok) { return $false }
        $client.EndConnect($iar) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-OpenPort {
    param([string]$HostName, [System.Collections.Generic.List[int]]$Ports)
    foreach ($port in $Ports) {
        if (Test-TcpPort -HostName $HostName -Port $port) {
            return $port
        }
    }
    return $null
}

function Get-GatewayProcesses {
    $result = New-Object System.Collections.Generic.List[object]
    $seen = New-Object System.Collections.Generic.HashSet[int]

    $native = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ProcessName -match "^(ibgateway|tws)$"
    } | Sort-Object StartTime -Descending)
    foreach ($proc in $native) {
        if ($seen.Add([int]$proc.Id)) {
            [void]$result.Add($proc)
        }
    }

    $javaGateway = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^javaw?\.exe$' -and [string]$_.CommandLine -match 'ibcalpha\.ibc\.IbcGateway'
    })
    foreach ($procInfo in $javaGateway) {
        try {
            $proc = Get-Process -Id ([int]$procInfo.ProcessId) -ErrorAction Stop
            if ($seen.Add([int]$proc.Id)) {
                [void]$result.Add($proc)
            }
        } catch {
        }
    }

    return @($result | Sort-Object StartTime -Descending)
}

function Get-EnsureProcesses {
    return @(Get-CimInstance Win32_Process -Filter "Name='powershell.exe'" -ErrorAction SilentlyContinue | Where-Object {
        $commandLine = [string]$_.CommandLine
        $commandLine -like "*ensure_ibkr_gateway.ps1*"
    })
}

function Get-DesktopState {
    $desktopHandle = [IntPtr]::Zero
    $desktopName = ""

    try {
        $desktopHandle = [NativeDesktopState]::OpenInputDesktop(0, $false, 0x0001)
        if ($desktopHandle -ne [IntPtr]::Zero) {
            $needed = 0
            $buffer = New-Object System.Text.StringBuilder 256
            if (-not [NativeDesktopState]::GetUserObjectInformation($desktopHandle, 2, $buffer, $buffer.Capacity, [ref]$needed) -and $needed -gt $buffer.Capacity) {
                $buffer = New-Object System.Text.StringBuilder ($needed + 1)
                [void][NativeDesktopState]::GetUserObjectInformation($desktopHandle, 2, $buffer, $buffer.Capacity, [ref]$needed)
            }
            $desktopName = $buffer.ToString().Trim()
        }
    } catch {
    } finally {
        if ($desktopHandle -ne [IntPtr]::Zero) {
            try { [void][NativeDesktopState]::CloseDesktop($desktopHandle) } catch { }
        }
    }

    $isLocked = $false
    if (-not [string]::IsNullOrWhiteSpace($desktopName)) {
        $isLocked = -not [string]::Equals($desktopName, "Default", [System.StringComparison]::OrdinalIgnoreCase)
    } else {
        $isLocked = @(Get-Process -Name "LogonUI" -ErrorAction SilentlyContinue).Count -gt 0
        if ($isLocked) {
            $desktopName = "LogonUI"
        }
    }

    if ([string]::IsNullOrWhiteSpace($desktopName)) {
        $desktopName = "unknown"
    }

    return [pscustomobject]@{
        DesktopName = $desktopName
        IsLocked = $isLocked
    }
}

function Format-Iso {
    param($Value)
    if ($null -eq $Value) { return $null }
    try {
        return ([datetime]$Value).ToString("o")
    } catch {
        return $null
    }
}

function Parse-BracketedTimestamp {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
    if ($Line -match '^\[(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]') {
        try {
            return [datetime]::ParseExact($Matches.ts, 'yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture)
        } catch {
            return $null
        }
    }
    return $null
}

function Parse-PlainTimestamp {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
    if ($Line -match '^(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})') {
        try {
            return [datetime]::ParseExact($Matches.ts, 'yyyy-MM-dd HH:mm:ss', [System.Globalization.CultureInfo]::InvariantCulture)
        } catch {
            return $null
        }
    }
    return $null
}

function Parse-IbcTimestamp {
    param([string]$Line)
    if ([string]::IsNullOrWhiteSpace($Line)) { return $null }
    if ($Line -match '^(?<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}:\d{3})') {
        try {
            return [datetime]::ParseExact($Matches.ts, 'yyyy-MM-dd HH:mm:ss:fff', [System.Globalization.CultureInfo]::InvariantCulture)
        } catch {
            return $null
        }
    }
    return $null
}

function Get-LatestLauncherLogPath {
    param([string]$LogDir)
    if ([string]::IsNullOrWhiteSpace($LogDir) -or -not (Test-Path $LogDir)) { return $null }
    $latest = Get-ChildItem -Path $LogDir -Filter "launcher-*.log" -File -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1
    if ($null -eq $latest) { return $null }
    return $latest.FullName
}

function Get-RecentLogEventTime {
    param(
        [string]$Path,
        [datetime]$AfterTime,
        [string[]]$Needles,
        [ValidateSet("bracketed", "plain", "ibc")]
        [string]$TimestampKind,
        [int]$TailLines = 6000
    )

    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path $Path)) { return $null }
    $lines = @(Get-Content -Path $Path -Tail $TailLines -ErrorAction SilentlyContinue)
    foreach ($line in $lines) {
        $text = if ($null -eq $line) { "" } else { [string]$line }
        $matched = $false
        foreach ($needle in $Needles) {
            if (-not [string]::IsNullOrWhiteSpace($needle) -and $text.Contains($needle)) {
                $matched = $true
                break
            }
        }
        if (-not $matched) { continue }

        $timestamp = if ($TimestampKind -eq "bracketed") {
            Parse-BracketedTimestamp -Line $text
        } elseif ($TimestampKind -eq "ibc") {
            Parse-IbcTimestamp -Line $text
        } else {
            Parse-PlainTimestamp -Line $text
        }

        if ($null -ne $timestamp -and $timestamp -ge $AfterTime) {
            return $timestamp
        }
    }

    return $null
}

function Get-EarliestObservedTime {
    param([object[]]$Values)
    $earliest = $null
    foreach ($value in $Values) {
        if ($null -eq $value) { continue }
        try {
            $timestamp = [datetime]$value
        } catch {
            continue
        }
        if ($null -eq $earliest -or $timestamp -lt $earliest) {
            $earliest = $timestamp
        }
    }
    return $earliest
}

function Test-ObservedBeforeUnlock {
    param(
        $ObservedAt,
        $UnlockAt
    )

    if ($null -eq $ObservedAt) { return $false }
    $observed = [datetime]$ObservedAt
    if ($null -eq $UnlockAt) { return $true }
    $unlock = [datetime]$UnlockAt
    return $observed -lt $unlock
}

$envPath = Join-Path $ProjectDir "python-core\.env"
$envVars = Read-DotEnv -Path $envPath
$hostName = Get-FirstEnvValue -Values $envVars -Names @("IBKR_TWS_HOST")
if ([string]::IsNullOrWhiteSpace($hostName)) { $hostName = "127.0.0.1" }
$tradingMode = Normalize-TradingMode (Get-FirstEnvValue -Values $envVars -Names @("IBKR_GATEWAY_TRADING_MODE", "IBKR_TRADING_MODE"))
$defaultPort = if ($tradingMode -eq "live") { 4001 } else { 4002 }
$configuredPort = Get-IntValue (Get-FirstEnvValue -Values $envVars -Names @("IBKR_TWS_PORT")) $defaultPort
$portCandidates = Get-PortCandidates -ConfiguredPort $configuredPort -TradingMode $tradingMode
$launcherLogsDir = Join-Path $ProjectDir "csharp-ui\AIInvestLauncher\bin\Debug\net8.0-windows\logs\launcher"
$jtsLauncherLogPath = "C:\Jts\launcher.log"
$ibcStdoutLogPath = Join-Path $ProjectDir "_runtime\ibc\Logs\ibc-java-stdout.log"

$state = [ordered]@{
    run_dir = $RunDir
    project_root = $RepoRoot
    launcher_logs_dir = $launcherLogsDir
    launcher_log_path = $null
    jts_launcher_log_path = $jtsLauncherLogPath
    ibc_stdout_log_path = $ibcStdoutLogPath
    host = $hostName
    configured_port = $configuredPort
    port_candidates = @($portCandidates)
    trading_mode = $tradingMode
    kill_delay_sec = $KillDelaySec
    monitor_timeout_sec = $MonitorTimeoutSec
    min_unlock_wait_sec = $MinUnlockWaitSec
    status = "armed"
    started_at = (Get-Date).ToString("o")
    lock_detected_at = $null
    kill_at = $null
    unlock_candidate_at = $null
    authoritative_unlock_at = $null
    unlock_source = $null
    unlocked_at = $null
    launcher_recovery_requested_at = $null
    launcher_ensure_started_at = $null
    launcher_session_unlock_at = $null
    launcher_unlock_recovery_requested_at = $null
    restart_detected_at = $null
    restart_pid = $null
    jts_restart_at = $null
    jts_auth_completed_at = $null
    ibc_session_started_at = $null
    ibc_login_completed_at = $null
    port_open_at = $null
    port_open_value = $null
    ensure_seen = $false
    ensure_seen_at = $null
    ensure_pids = @()
    killed_pids = @()
    precondition_process_running = $false
    precondition_port_open = $false
    passed = $false
    outcome = "pending"
    failure_reason = $null
}

Save-Status -State $state

Log "test armed | run_dir=$RunDir | host=$hostName | configured_port=$configuredPort | mode=$tradingMode | kill_delay=${KillDelaySec}s"

$initialProcesses = @(Get-GatewayProcesses)
$initialPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
$state.precondition_process_running = @($initialProcesses).Count -gt 0
$state.precondition_port_open = $null -ne $initialPort
Save-Status -State $state

if (-not $state.precondition_process_running) {
    $state.status = "aborted"
    $state.outcome = "fail"
    $state.failure_reason = "No running IB Gateway/TWS process was found at test start."
    Save-Status -State $state
    Log "abort: no running IB Gateway/TWS process found at test start"
    "IBKR lock test: FAIL`r`nDuvod: na zacatku testu nebyl nalezen bezici proces IB Gateway/TWS.`r`nRunDir: $RunDir" | Set-Content -Path $summaryPath -Encoding UTF8
    Save-Json -Path $resultPath -Data $state
    exit 2
}

Log "precondition | gateway_processes=$(@($initialProcesses).Count) | initial_open_port=$(if ($null -ne $initialPort) { $initialPort } else { 'none' })"

$armDeadline = (Get-Date).AddSeconds([Math]::Max(30, $ArmTimeoutSec))
$continuousLockStart = $null

while ((Get-Date) -lt $armDeadline) {
    $desktopState = Get-DesktopState
    if ($desktopState.IsLocked) {
        if ($null -eq $continuousLockStart) {
            $continuousLockStart = Get-Date
            $state.lock_detected_at = $continuousLockStart.ToString("o")
            $state.status = "lock_detected"
            Save-Status -State $state
            Log "lock detected on desktop '$($desktopState.DesktopName)'"
        }

        $lockedForSec = ((Get-Date) - $continuousLockStart).TotalSeconds
        if ($lockedForSec -ge [Math]::Max(1, $KillDelaySec)) {
            break
        }
    } else {
        if ($null -ne $continuousLockStart) {
            Log "lock detection reset because workstation became unlocked before kill delay elapsed"
        }
        $continuousLockStart = $null
        $state.lock_detected_at = $null
        $state.status = "armed"
        Save-Status -State $state
    }
    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}

if ($null -eq $continuousLockStart -or -not (Get-DesktopState).IsLocked) {
    $state.status = "timeout_waiting_for_lock"
    $state.outcome = "fail"
    $state.failure_reason = "Workstation lock was not detected within the arming timeout."
    Save-Status -State $state
    Log "timeout waiting for workstation lock"
    "IBKR lock test: FAIL`r`nDuvod: do ${ArmTimeoutSec}s nebylo detekovano uzamceni PC.`r`nRunDir: $RunDir" | Set-Content -Path $summaryPath -Encoding UTF8
    Save-Json -Path $resultPath -Data $state
    exit 3
}

$processesToKill = @(Get-GatewayProcesses)
$state.kill_at = (Get-Date).ToString("o")
$state.status = "killing_gateway"
$killedPids = New-Object System.Collections.Generic.List[int]

foreach ($proc in $processesToKill) {
    try {
        Stop-Process -Id $proc.Id -Force -ErrorAction Stop
        [void]$killedPids.Add([int]$proc.Id)
        Log "gateway process killed | pid=$($proc.Id) | name=$($proc.ProcessName)"
    } catch {
        Log "failed to kill gateway process | pid=$($proc.Id) | err=$($_.Exception.Message)"
    }
}

$state.killed_pids = @($killedPids)
Save-Status -State $state

if (@($state.killed_pids).Count -eq 0) {
    $state.status = "kill_failed"
    $state.outcome = "fail"
    $state.failure_reason = "No IB Gateway/TWS process could be terminated after the lock delay."
    Save-Status -State $state
    "IBKR lock test: FAIL`r`nDuvod: po zamceni se nepodarilo ukoncit zadny proces IB Gateway/TWS.`r`nRunDir: $RunDir" | Set-Content -Path $summaryPath -Encoding UTF8
    Save-Json -Path $resultPath -Data $state
    exit 4
}

$killAt = [datetime]$state.kill_at
$minimumObservationUntil = $killAt.AddSeconds([Math]::Max(60, $MinUnlockWaitSec))
$monitorDeadline = $killAt.AddSeconds([Math]::Max([Math]::Max(60, $MonitorTimeoutSec), [Math]::Max(60, $MinUnlockWaitSec)))
$state.status = "monitoring_locked_session"
Save-Status -State $state
Log "monitoring started after kill | timeout=${MonitorTimeoutSec}s | minimum_observation=${MinUnlockWaitSec}s"

$heartbeatIndex = 0
$unlockDetected = $false

while ((Get-Date) -lt $monitorDeadline) {
    $heartbeatIndex++
    $desktopState = Get-DesktopState
    $isLocked = $desktopState.IsLocked
    $now = Get-Date
    $launcherLogPath = Get-LatestLauncherLogPath -LogDir $launcherLogsDir
    if (-not [string]::IsNullOrWhiteSpace($launcherLogPath) -and $state.launcher_log_path -ne $launcherLogPath) {
        $state.launcher_log_path = $launcherLogPath
        Save-Status -State $state
        Log "using launcher log '$launcherLogPath'"
    }

    if (-not [string]::IsNullOrWhiteSpace($launcherLogPath)) {
        if ($null -eq $state.launcher_recovery_requested_at) {
            $launcherRecoveryRequested = Get-RecentLogEventTime -Path $launcherLogPath -AfterTime $killAt -Needles @("IBKR Gateway recovery requested:") -TimestampKind "bracketed"
            if ($null -ne $launcherRecoveryRequested) {
                $state.launcher_recovery_requested_at = $launcherRecoveryRequested.ToString("o")
                Save-Status -State $state
                Log "launcher recovery request observed at $($state.launcher_recovery_requested_at)"
            }
        }

        if ($null -eq $state.launcher_ensure_started_at) {
            $launcherEnsureStarted = Get-RecentLogEventTime -Path $launcherLogPath -AfterTime $killAt -Needles @("IBKR-GATEWAY | START") -TimestampKind "bracketed"
            if ($null -ne $launcherEnsureStarted) {
                $state.launcher_ensure_started_at = $launcherEnsureStarted.ToString("o")
                Save-Status -State $state
                Log "launcher ensure start observed at $($state.launcher_ensure_started_at)"
            }
        }

        if ($null -eq $state.launcher_session_unlock_at) {
            $unlockFromLauncher = Get-RecentLogEventTime -Path $launcherLogPath -AfterTime $killAt -Needles @("=== Launcher SessionUnlock ===") -TimestampKind "bracketed"
            if ($null -ne $unlockFromLauncher) {
                $state.launcher_session_unlock_at = $unlockFromLauncher.ToString("o")
                $state.authoritative_unlock_at = $state.launcher_session_unlock_at
                $state.unlocked_at = $state.launcher_session_unlock_at
                $state.unlock_source = "launcher_sessionunlock"
                Save-Status -State $state
                Log "launcher session unlock event observed at $($state.launcher_session_unlock_at)"
            }
        }

        if ($null -eq $state.launcher_unlock_recovery_requested_at) {
            $unlockRecoveryRequested = Get-RecentLogEventTime -Path $launcherLogPath -AfterTime $killAt -Needles @("Session unlock recovery: requesting IBKR Gateway ensure") -TimestampKind "bracketed"
            if ($null -ne $unlockRecoveryRequested) {
                $state.launcher_unlock_recovery_requested_at = $unlockRecoveryRequested.ToString("o")
                Save-Status -State $state
                Log "launcher unlock-driven recovery request observed at $($state.launcher_unlock_recovery_requested_at)"
            }
        }
    }

    if ($null -eq $state.ibc_session_started_at -and (Test-Path $ibcStdoutLogPath)) {
        $ibcSessionStarted = Get-RecentLogEventTime -Path $ibcStdoutLogPath -AfterTime $killAt -Needles @("IBC: Starting session") -TimestampKind "ibc"
        if ($null -ne $ibcSessionStarted) {
            $state.ibc_session_started_at = $ibcSessionStarted.ToString("o")
            Save-Status -State $state
            Log "IBC session start observed at $($state.ibc_session_started_at)"
        }
    }

    if ($null -eq $state.ibc_login_completed_at -and (Test-Path $ibcStdoutLogPath)) {
        $ibcLoginCompleted = Get-RecentLogEventTime -Path $ibcStdoutLogPath -AfterTime $killAt -Needles @("IBC: Login has completed") -TimestampKind "ibc"
        if ($null -ne $ibcLoginCompleted) {
            $state.ibc_login_completed_at = $ibcLoginCompleted.ToString("o")
            Save-Status -State $state
            Log "IBC login completion observed at $($state.ibc_login_completed_at)"
        }
    }

    $ensureProcesses = @(Get-EnsureProcesses)
    if (-not $state.ensure_seen -and @($ensureProcesses).Count -gt 0) {
        $state.ensure_seen = $true
        $state.ensure_seen_at = $now.ToString("o")
        $state.ensure_pids = @($ensureProcesses | ForEach-Object { [int]$_.ProcessId })
        Save-Status -State $state
        Log "ensure script observed | pids=$($state.ensure_pids -join ',')"
    }

    if ($null -eq $state.jts_restart_at) {
        $jtsRestart = Get-RecentLogEventTime -Path $jtsLauncherLogPath -AfterTime $killAt -Needles @("IB GATEWAY RESTART") -TimestampKind "plain"
        if ($null -ne $jtsRestart) {
            $state.jts_restart_at = $jtsRestart.ToString("o")
            Save-Status -State $state
            Log "JTS gateway restart observed at $($state.jts_restart_at)"
        }
    }

    if ($null -eq $state.jts_auth_completed_at) {
        $jtsAuth = Get-RecentLogEventTime -Path $jtsLauncherLogPath -AfterTime $killAt -Needles @("Authentication completed.", "Authentication complete") -TimestampKind "plain"
        if ($null -ne $jtsAuth) {
            $state.jts_auth_completed_at = $jtsAuth.ToString("o")
            Save-Status -State $state
            Log "JTS authentication completion observed at $($state.jts_auth_completed_at)"
        }
    }

    $restartedGateway = @(Get-GatewayProcesses | Where-Object {
        try { $_.StartTime -gt $killAt.AddSeconds(-1) } catch { $false }
    } | Sort-Object StartTime -Descending)
    if ($null -eq $state.restart_detected_at -and @($restartedGateway).Count -gt 0) {
        $latest = $restartedGateway[0]
        $state.restart_detected_at = $latest.StartTime.ToString("o")
        $state.restart_pid = [int]$latest.Id
        Save-Status -State $state
        Log "gateway process restart observed | pid=$($latest.Id) | started=$($latest.StartTime.ToString('o'))"
    }

    $openPort = Get-OpenPort -HostName $hostName -Ports $portCandidates
    if ($null -eq $state.port_open_at -and $null -ne $openPort) {
        $state.port_open_at = $now.ToString("o")
        $state.port_open_value = [int]$openPort
        Save-Status -State $state
        Log "gateway API reachable | port=$openPort"
    }

    if (-not $isLocked) {
        if ($null -eq $state.unlock_candidate_at) {
            $state.unlock_candidate_at = $now.ToString("o")
            Save-Status -State $state
            Log "desktop unlock candidate detected on '$($desktopState.DesktopName)'"
        }
        if ($now -ge $minimumObservationUntil) {
            $state.unlocked_at = $now.ToString("o")
            $state.unlock_source = "desktop_heuristic"
            Save-Status -State $state
            Log "heuristic unlock accepted after minimum observation window on desktop '$($desktopState.DesktopName)'"
            break
        }
    } elseif ($null -ne $state.unlock_candidate_at -and -not $unlockDetected) {
        Log "desktop returned to locked state before minimum observation window elapsed"
        $state.unlock_candidate_at = $null
        Save-Status -State $state
    }

    if (($heartbeatIndex % 15) -eq 0) {
        Log "monitor heartbeat | locked=$isLocked | restart_seen=$([bool]$state.restart_detected_at) | port_seen=$([bool]$state.port_open_at) | ensure_seen=$($state.ensure_seen)"
    }

    if (($null -ne $state.ibc_login_completed_at -or $null -ne $state.port_open_at) -and $now -ge $minimumObservationUntil) {
        Log "required post-kill observation window elapsed and recovery evidence captured"
        break
    }

    Start-Sleep -Seconds ([Math]::Max(1, $PollSeconds))
}

$state.status = "finalizing"
$unlockDetected = $null -ne $state.unlocked_at
$lockDurationSec = $null
if ($state.lock_detected_at -and $state.unlocked_at) {
    try {
        $lockDurationSec = [int][Math]::Round((([datetime]$state.unlocked_at) - ([datetime]$state.lock_detected_at)).TotalSeconds)
    } catch {
        $lockDurationSec = $null
    }
}

$unlockSignalAt = Get-EarliestObservedTime -Values @($state.launcher_unlock_recovery_requested_at, $state.launcher_session_unlock_at)
$firstIbcActivityAt = Get-EarliestObservedTime -Values @($state.ibc_session_started_at, $state.ibc_login_completed_at, $state.port_open_at)
$restartObservedAt = Get-EarliestObservedTime -Values @($state.jts_restart_at, $state.restart_detected_at, $state.ibc_session_started_at)
$authObservedAt = Get-EarliestObservedTime -Values @($state.ibc_login_completed_at, $state.jts_auth_completed_at, $state.port_open_at)

$recoveryBeforeUnlockPath = Test-ObservedBeforeUnlock -ObservedAt $firstIbcActivityAt -UnlockAt $unlockSignalAt

if ($null -ne $state.launcher_recovery_requested_at -and $null -ne $firstIbcActivityAt -and $recoveryBeforeUnlockPath) {
    $state.passed = $true
    $state.outcome = "pass"
    $state.status = "finished"
    $state.failure_reason = $null
} else {
    $state.passed = $false
    $state.status = "finished"
    if ($null -eq $state.launcher_recovery_requested_at) {
        $state.outcome = "fail_no_recovery"
        $state.failure_reason = "Launcher watchdog recovery request was not observed after the forced kill."
    } elseif ($null -eq $firstIbcActivityAt) {
        $state.outcome = "fail_no_login"
        $state.failure_reason = "No IBC start, login completion, or API port reopen was observed after the recovery request."
    } elseif (-not $recoveryBeforeUnlockPath) {
        $state.outcome = "fail_unlock_driven"
        $state.failure_reason = "First IBC recovery activity happened only after the launcher unlock-driven recovery path."
    } else {
        $state.outcome = "fail"
        $state.failure_reason = "Recovery flow did not satisfy lock-driven criteria."
    }
}

$unlockWaitOk = $false
if ($null -ne $lockDurationSec) {
    $unlockWaitOk = $lockDurationSec -ge $MinUnlockWaitSec
}

$summaryLines = @()
$summaryLines += "IBKR lock test: $(if ($state.passed) { 'PASS' } else { 'FAIL' })"
$summaryLines += "RunDir: $RunDir"
$summaryLines += "Host/port: $hostName / $configuredPort ($tradingMode)"
$summaryLines += "Lock detected: $(if ($state.lock_detected_at) { $state.lock_detected_at } else { 'no' })"
$summaryLines += "Gateway killed at: $(if ($state.kill_at) { $state.kill_at } else { 'no' })"
$summaryLines += "Launcher log: $(if ($state.launcher_log_path) { $state.launcher_log_path } else { 'not found' })"
$summaryLines += "IBC stdout log: $(if (Test-Path $state.ibc_stdout_log_path) { $state.ibc_stdout_log_path } else { 'not found' })"
$summaryLines += "Launcher recovery requested: $(if ($state.launcher_recovery_requested_at) { $state.launcher_recovery_requested_at } else { 'no' })"
$summaryLines += "Launcher ensure started: $(if ($state.launcher_ensure_started_at) { $state.launcher_ensure_started_at } else { 'no' })"
$summaryLines += "Launcher session unlock: $(if ($state.launcher_session_unlock_at) { $state.launcher_session_unlock_at } else { 'no' })"
$summaryLines += "Launcher unlock recovery requested: $(if ($state.launcher_unlock_recovery_requested_at) { $state.launcher_unlock_recovery_requested_at } else { 'no' })"
$summaryLines += "IBC session started: $(if ($state.ibc_session_started_at) { $state.ibc_session_started_at } else { 'no' })"
$summaryLines += "IBC login completed: $(if ($state.ibc_login_completed_at) { $state.ibc_login_completed_at } else { 'no' })"
$summaryLines += "Gateway restart observed: $(if ($restartObservedAt) { "$($restartObservedAt.ToString('o')) pid=$($state.restart_pid)" } else { 'no' })"
$summaryLines += "JTS restart log: $(if ($state.jts_restart_at) { $state.jts_restart_at } else { 'no' })"
$summaryLines += "JTS auth completed: $(if ($state.jts_auth_completed_at) { $state.jts_auth_completed_at } else { 'no' })"
$summaryLines += "API reachable: $(if ($state.port_open_at) { "$($state.port_open_at) port=$($state.port_open_value)" } else { 'no' })"
$summaryLines += "First IBC activity: $(if ($firstIbcActivityAt) { $firstIbcActivityAt.ToString('o') } else { 'no' })"
$summaryLines += "First IBC activity before unlock-path: $(if ($firstIbcActivityAt) { $(if ($recoveryBeforeUnlockPath) { 'yes' } else { 'no' }) } else { 'unknown' })"
$summaryLines += "Ensure observed: $(if ($state.ensure_seen) { "yes at $($state.ensure_seen_at) pid=$($state.ensure_pids -join ',')" } else { 'no' })"
$summaryLines += "Unlock candidate at: $(if ($state.unlock_candidate_at) { $state.unlock_candidate_at } else { 'no' })"
$summaryLines += "Unlocked at: $(if ($state.unlocked_at) { "$($state.unlocked_at) source=$($state.unlock_source)" } else { 'not observed before script ended' })"
$summaryLines += "Lock duration sec: $(if ($null -ne $lockDurationSec) { $lockDurationSec } else { 'unknown' })"
$summaryLines += "Minimum lock met (${MinUnlockWaitSec}s): $(if ($unlockWaitOk) { 'yes' } elseif ($null -ne $lockDurationSec) { 'no' } else { 'unknown' })"
if (-not $state.passed -and $state.failure_reason) {
    $summaryLines += "Failure reason: $($state.failure_reason)"
}

$summaryText = $summaryLines -join [Environment]::NewLine
$summaryText | Set-Content -Path $summaryPath -Encoding UTF8
Save-Json -Path $resultPath -Data $state
Save-Status -State $state
Log "test finished | outcome=$($state.outcome) | summary=$summaryPath"
Write-Host ""
Write-Host $summaryText

exit $(if ($state.passed) { 0 } else { 1 })
