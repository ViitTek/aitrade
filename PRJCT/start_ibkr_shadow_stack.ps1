param(
    [string]$ProjectRoot = "C:\aiinvest",
    [int]$ApiPort = 8110,
    [int]$BacktestPort = 8101,
    [switch]$NoBacktestApi,
    [switch]$NoAutoBot,
    [switch]$CleanFirst,
    [switch]$Headless
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$layoutHelper = Join-Path $ScriptDir "resolve_aiinvest_layout.ps1"
if (Test-Path $layoutHelper) { . $layoutHelper }
$RepoRoot = if (Test-Path (Join-Path $ProjectRoot "PRJCT")) { $ProjectRoot } elseif (Test-Path (Join-Path (Split-Path -Parent $ScriptDir) "PRJCT")) { Split-Path -Parent $ScriptDir } else { $ProjectRoot }
if (Get-Command Get-AIInvestLayout -ErrorAction SilentlyContinue) {
    $layout = Get-AIInvestLayout -RepoRoot $RepoRoot
    $RepoRoot = $layout.RepoRoot
    $ProjectDir = $layout.ProjectDir
    $ReportsRoot = $layout.ReportsDir
} else {
    $ProjectDir = if (Test-Path (Join-Path $RepoRoot "PRJCT")) { Join-Path $RepoRoot "PRJCT" } else { $ScriptDir }
    $ReportsRoot = if (Test-Path (Join-Path $RepoRoot "RPRTS")) { Join-Path $RepoRoot "RPRTS" } else { $RepoRoot }
}
$PyCoreDir = Join-Path $ProjectDir "python-core"
$Py = Join-Path $PyCoreDir "venv\Scripts\python.exe"
$RuntimeDir = Join-Path $ProjectDir "_runtime\ibkr"
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

$ensureGatewayScript = Join-Path $ProjectDir "ensure_ibkr_gateway.ps1"
if (Test-Path $ensureGatewayScript) {
    try {
        & powershell.exe -Sta -NoProfile -ExecutionPolicy Bypass -File $ensureGatewayScript -ProjectRoot $RepoRoot -LoginTimeoutSec 150
    } catch {
        Write-Warning "IBKR gateway ensure failed: $($_.Exception.Message)"
    }
}

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(1000)
        if (-not $ok) { return $false }
        $client.EndConnect($iar) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Stop-ByPort {
    param([int]$Port)
    try {
        $lines = netstat -ano | findstr LISTENING | findstr ":$Port"
        foreach ($line in @($lines)) {
            if (-not $line) { continue }
            $parts = ($line -split "\s+") | Where-Object { $_ }
            $pid = 0
            if ($parts.Count -gt 0 -and [int]::TryParse($parts[-1], [ref]$pid) -and $pid -gt 0) {
                try { Stop-Process -Id $pid -Force -ErrorAction Stop } catch {}
            }
        }
    } catch {}
}

function Resolve-PreferredChildProcess {
    param(
        [object[]]$Children,
        [string]$Needle = ""
    )
    $list = @($Children)
    if ($list.Count -eq 0) { return $null }
    $primary = @($list | Where-Object {
        $name = (($_.Name -as [string]) | ForEach-Object { if ($_){ $_ } else { "" } }).ToLowerInvariant()
        $name.Contains("python") -or $name.Contains("node") -or $name.Contains("cmd")
    })
    if ($primary.Count -gt 0) {
        $list = $primary
    }
    if (-not [string]::IsNullOrWhiteSpace($Needle)) {
        $matched = @($list | Where-Object { (($_.CommandLine -as [string]) -like "*$Needle*") })
        if ($matched.Count -gt 0) { return $matched[0] }
    }
    $pythonLike = @($list | Where-Object { (($_.Name -as [string]).ToLowerInvariant().Contains("python")) })
    if ($pythonLike.Count -gt 0) { return $pythonLike[0] }
    return $list[0]
}

function Get-ListeningPortProcessId {
    param(
        [int]$Port,
        [int]$TimeoutMs = 10000
    )
    $deadline = [DateTime]::UtcNow.AddMilliseconds([Math]::Max(500, $TimeoutMs))
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $lines = @(netstat -ano | findstr LISTENING | findstr ":$Port")
            foreach ($line in $lines) {
                if (-not $line) { continue }
                $parts = ($line -split "\s+") | Where-Object { $_ }
                $procId = 0
                if ($parts.Count -gt 0 -and [int]::TryParse($parts[-1], [ref]$procId) -and $procId -gt 0) {
                    return $procId
                }
            }
        } catch {
        }
        Start-Sleep -Milliseconds 250
    }
    return $null
}

function Write-PidInfo {
    param(
        [string]$Component,
        [System.Diagnostics.Process]$Process,
        [string]$WorkDir,
        [string]$Command,
        [string]$Needle = "",
        [int]$ListenPort = 0
    )
    try {
        $listeningPid = if ($ListenPort -gt 0) { Get-ListeningPortProcessId -Port $ListenPort } else { $null }
        $childPid = $null
        if (-not $listeningPid) {
            $deadline = [DateTime]::UtcNow.AddMilliseconds(5000)
            while ([DateTime]::UtcNow -lt $deadline -and -not $childPid) {
                try {
                    $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $($Process.Id)" -ErrorAction SilentlyContinue)
                    if ($children.Count -gt 0) {
                        $selected = Resolve-PreferredChildProcess -Children $children -Needle $Needle
                        while ($selected) {
                            $grandChildren = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = " + [int]$selected.ProcessId) -ErrorAction SilentlyContinue)
                            if ($grandChildren.Count -eq 0) {
                                $childPid = [int]$selected.ProcessId
                                break
                            }
                            $next = Resolve-PreferredChildProcess -Children $grandChildren -Needle $Needle
                            if ($null -eq $next -or [int]$next.ProcessId -eq [int]$selected.ProcessId) {
                                $childPid = [int]$selected.ProcessId
                                break
                            }
                            $selected = $next
                        }
                    }
                } catch {}
                if (-not $childPid) { Start-Sleep -Milliseconds 200 }
            }
        }
        $effectivePid = if ($listeningPid) { $listeningPid } elseif ($childPid) { $childPid } else { $Process.Id }
        if ($listeningPid) { $childPid = $listeningPid }
        $info = [ordered]@{
            suite = "ibkr"
            component = $Component
            pid = $effectivePid
            wrapper_pid = $Process.Id
            child_pid = $childPid
            started_at = (Get-Date).ToString("o")
            headless = [bool]$Headless
            api_port = $ApiPort
            backtest_port = $BacktestPort
            workdir = $WorkDir
            command = $Command
            repo_root = $RepoRoot
            project_dir = $ProjectDir
            reports_root = $ReportsRoot
            python = $Py
        }
        $path = Join-Path $RuntimeDir ($Component.ToLowerInvariant() + ".json")
        $info | ConvertTo-Json -Depth 6 | Set-Content -Path $path -Encoding UTF8
    } catch {
        Write-Warning "PID info write failed for ${Component}: $($_.Exception.Message)"
    }
}

function Start-StackProcess {
    param(
        [string]$Command,
        [string]$Component,
        [string]$Needle = "",
        [int]$ListenPort = 0
    )
    $args = if ($Headless) {
        @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $Command)
    } else {
        @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $Command)
    }
    $windowStyle = if ($Headless) { "Hidden" } else { "Normal" }
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $PyCoreDir -WindowStyle $windowStyle -PassThru
    Write-PidInfo -Component $Component -Process $p -WorkDir $PyCoreDir -Command $Command -Needle $Needle -ListenPort $ListenPort
    return $p
}

if ($CleanFirst) {
    Stop-ByPort -Port $ApiPort
    if (-not $NoBacktestApi) {
        Stop-ByPort -Port $BacktestPort
    }
    Start-Sleep -Seconds 1
}

$envBlock = @(
    '$env:MODE=''live''',
    '$env:SHADOW_MODE_ENABLED=''true''',
    '$env:DEFAULT_BROKER=''ibkr''',
    '$env:INTERVAL_MINUTES=''60''',
    '$env:TRADING_BINANCE_ENABLED=''false''',
    '$env:TRADING_IBKR_ENABLED=''true''',
    '$env:IBKR_GATEWAY_TRADING_MODE=''paper''',
    '$env:CROSS_ASSET_SHADOW_ENABLED=''true''',
    '$env:CROSS_ASSET_PROVIDER=''ibkr''',
    '$env:INTEL_ENABLED=''false''',
    '$env:AUTO_TUNE_ENABLED=''false''',
    '$env:AUTO_TUNE_APPLY=''false''',
    '$env:DYNAMIC_ASSETS_ENABLED=''false''',
    '$env:EXPAND_UNIVERSE_FROM_RECOMMENDATIONS=''false''',
    '$env:PF_GUARD_ENABLED=''true''',
    '$env:TIME_EXIT_MINUTES=''1440''',
    '$env:RESUME_ON_START=''false''',
    '$env:NEWS_WORKER_ENABLED=''false''',
    '$env:MARKET_DATA_WORKER_ENABLED=''false''',
    '$env:SYMBOLS=''''',
    '$env:BINANCE_SYMBOLS=''''',
    '$env:ALWAYS_ACTIVE_SYMBOLS=''''',
    '$env:IBKR_SYMBOLS=''EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL''',
    '$env:CROSS_ASSET_FX_SYMBOLS=''EURUSD,GBPUSD,USDJPY''',
    '$env:CROSS_ASSET_COMMODITY_SYMBOLS=''XAUUSD,XAGUSD,CL''',
    '$env:CROSS_ASSET_INDEX_SYMBOLS=''''',
    '$env:SIGNAL_QUALITY_ENABLED=''false'''
)

if (-not (Test-TcpPort -HostName "127.0.0.1" -Port $ApiPort)) {
    $apiCmd = ($envBlock + @("& '$Py' -m uvicorn app:app --host 127.0.0.1 --port $ApiPort")) -join "; "
    Start-StackProcess -Command $apiCmd -Component "api" -Needle "--port $ApiPort" -ListenPort $ApiPort | Out-Null
}

if (-not $NoBacktestApi -and -not (Test-TcpPort -HostName "127.0.0.1" -Port $BacktestPort)) {
    $btCmd = ($envBlock + @("& '$Py' -m uvicorn app:app --host 127.0.0.1 --port $BacktestPort")) -join "; "
    Start-StackProcess -Command $btCmd -Component "backtest" -Needle "--port $BacktestPort" -ListenPort $BacktestPort | Out-Null
}

if (-not $NoAutoBot) {
    $deadline = (Get-Date).AddSeconds(45)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName "127.0.0.1" -Port $ApiPort) { break }
        Start-Sleep -Milliseconds 800
    }
    try {
        $cfg = @{
            MODE = "live"
            SHADOW_MODE_ENABLED = $true
            DEFAULT_BROKER = "ibkr"
            INTERVAL_MINUTES = 60
            TRADING_BINANCE_ENABLED = $false
            TRADING_IBKR_ENABLED = $true
            CROSS_ASSET_SHADOW_ENABLED = $true
            CROSS_ASSET_PROVIDER = "ibkr"
            INTEL_ENABLED = $false
            AUTO_TUNE_ENABLED = $false
            AUTO_TUNE_APPLY = $false
            DYNAMIC_ASSETS_ENABLED = $false
            EXPAND_UNIVERSE_FROM_RECOMMENDATIONS = $false
            PF_GUARD_ENABLED = $true
            TIME_EXIT_MINUTES = 1440
            RESUME_ON_START = $false
            NEWS_WORKER_ENABLED = $false
            MARKET_DATA_WORKER_ENABLED = $false
            SYMBOLS = ""
            BINANCE_SYMBOLS = ""
            ALWAYS_ACTIVE_SYMBOLS = ""
            IBKR_SYMBOLS = "EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL"
            CROSS_ASSET_FX_SYMBOLS = "EURUSD,GBPUSD,USDJPY"
            CROSS_ASSET_COMMODITY_SYMBOLS = "XAUUSD,XAGUSD,CL"
            CROSS_ASSET_INDEX_SYMBOLS = ""
            SIGNAL_QUALITY_ENABLED = $false
            SIGNAL_QUALITY_SHADOW_HORIZON_MIN = 60
        } | ConvertTo-Json
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/bot/stop" -Method Post -TimeoutSec 12 | Out-Null
        } catch {}
        Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/bot/config" -Method Put -ContentType "application/json" -Body $cfg -TimeoutSec 20 | Out-Null
        Invoke-RestMethod -Uri "http://127.0.0.1:$ApiPort/bot/start" -Method Post -TimeoutSec 12 | Out-Null
    } catch {
        Write-Warning "IBKR bot start failed: $($_.Exception.Message)"
    }
}
