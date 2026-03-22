param(
    [switch]$NoCollector,
    [switch]$NoBacktestApi,
    [switch]$NoAutoBot,
    [switch]$CleanFirst,
    [switch]$Headless,
    [int]$MainApiPort = 8010
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$layoutHelper = Join-Path $ScriptDir "resolve_aiinvest_layout.ps1"
if (Test-Path $layoutHelper) { . $layoutHelper }
$RepoRoot = Split-Path -Parent $ScriptDir
if (Get-Command Get-AIInvestLayout -ErrorAction SilentlyContinue) {
    $layout = Get-AIInvestLayout -RepoRoot $RepoRoot
    $RepoRoot = $layout.RepoRoot
    $ProjectDir = $layout.ProjectDir
    $DbRoot = $layout.DatabaseDir
    $ReportsRoot = $layout.ReportsDir
} else {
    $ProjectDir = if (Test-Path (Join-Path $RepoRoot "PRJCT")) { Join-Path $RepoRoot "PRJCT" } else { $ScriptDir }
    $DbRoot = if (Test-Path (Join-Path $RepoRoot "DTB")) { Join-Path $RepoRoot "DTB" } else { $RepoRoot }
    $ReportsRoot = if (Test-Path (Join-Path $RepoRoot "RPRTS")) { Join-Path $RepoRoot "RPRTS" } else { $RepoRoot }
}
$Py = Join-Path $ProjectDir "python-core\venv\Scripts\python.exe"
$MongoD = Join-Path $DbRoot "MongoDB\server\6.0\bin\mongod.exe"
$MongoData = Join-Path $DbRoot "MongoDB\data"
$DashboardDir = Join-Path $ProjectDir "dashboard"
$PyCoreDir = Join-Path $ProjectDir "python-core"
$NpmCmd = "C:\Program Files\nodejs\npm.cmd"
$RuntimeDir = Join-Path $ProjectDir "_runtime\main"
New-Item -ItemType Directory -Path $RuntimeDir -Force | Out-Null

function Test-TcpPort {
    param([string]$HostName, [int]$Port)
    $client = New-Object System.Net.Sockets.TcpClient
    try {
        $iar = $client.BeginConnect($HostName, $Port, $null, $null)
        $ok = $iar.AsyncWaitHandle.WaitOne(800)
        if (-not $ok) { return $false }
        $client.EndConnect($iar) | Out-Null
        return $true
    } catch {
        return $false
    } finally {
        $client.Close()
    }
}

function Get-ReachableHttpUrl {
    param([int]$Port)
    if (Test-TcpPort -HostName "localhost" -Port $Port) { return "http://localhost:$Port" }
    if (Test-TcpPort -HostName "127.0.0.1" -Port $Port) { return "http://127.0.0.1:$Port" }
    return $null
}

function Test-ProcessContains {
    param([string]$Needle)
    try {
        $items = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            ($_.CommandLine -as [string]) -like "*$Needle*"
        }
        return ($items | Measure-Object).Count -gt 0
    } catch {
        return $false
    }
}

function Get-ProcessesByNeedle {
    param([string]$Needle, [string[]]$ProcessNames = @())
    $items = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        ($_.CommandLine -as [string]) -like "*$Needle*"
    }
    if ($ProcessNames.Count -gt 0) {
        $allow = @{}
        foreach ($n in $ProcessNames) { $allow[$n.ToLowerInvariant()] = $true }
        $items = $items | Where-Object { $allow.ContainsKey(($_.Name -as [string]).ToLowerInvariant()) }
    }
    return @($items)
}

function Stop-Processes {
    param([object[]]$Items, [string]$Label)
    foreach ($p in $Items) {
        try {
            Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
            Write-Host "${Label}: stopped duplicate PID $($p.ProcessId)" -ForegroundColor Yellow
        } catch {
            Write-Warning "${Label}: failed to stop PID $($p.ProcessId): $($_.Exception.Message)"
        }
    }
}

function Get-CollectorProcesses {
    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cl = ($_.CommandLine -as [string])
            $name = ($_.Name -as [string])
            $name -and $name.ToLowerInvariant().Contains("python") -and $cl -and $cl.Contains("data_collector.py")
        } | Sort-Object ProcessId
    )
}

function Get-WorkerProcesses {
    param([string]$ScriptName)
    return @(
        Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
            $cl = ($_.CommandLine -as [string])
            $name = ($_.Name -as [string])
            $name -and $name.ToLowerInvariant().Contains("python") -and $cl -and $cl.Contains($ScriptName)
        } | Sort-Object ProcessId
    )
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

function Get-ChildProcessId {
    param(
        [int]$ParentPid,
        [string]$Needle = "",
        [int]$TimeoutMs = 5000
    )
    $deadline = [DateTime]::UtcNow.AddMilliseconds([Math]::Max(250, $TimeoutMs))
    while ([DateTime]::UtcNow -lt $deadline) {
        try {
            $children = @(Get-CimInstance Win32_Process -Filter "ParentProcessId = $ParentPid" -ErrorAction SilentlyContinue)
            if ($children.Count -gt 0) {
                $selected = Resolve-PreferredChildProcess -Children $children -Needle $Needle
                while ($selected) {
                    $grandChildren = @(Get-CimInstance Win32_Process -Filter ("ParentProcessId = " + [int]$selected.ProcessId) -ErrorAction SilentlyContinue)
                    if ($grandChildren.Count -eq 0) { return [int]$selected.ProcessId }
                    $next = Resolve-PreferredChildProcess -Children $grandChildren -Needle $Needle
                    if ($null -eq $next -or [int]$next.ProcessId -eq [int]$selected.ProcessId) {
                        return [int]$selected.ProcessId
                    }
                    $selected = $next
                }
            }
        } catch {
        }
        Start-Sleep -Milliseconds 200
    }
    return $null
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
        [string]$OutputRoot = "",
        [string]$Needle = "",
        [int]$ListenPort = 0
    )
    try {
        Start-Sleep -Milliseconds 250
        $listeningPid = if ($ListenPort -gt 0) { Get-ListeningPortProcessId -Port $ListenPort } else { $null }
        $childPid = if ($listeningPid) { $listeningPid } else { Get-ChildProcessId -ParentPid $Process.Id -Needle $Needle }
        $effectivePid = if ($listeningPid) { $listeningPid } elseif ($childPid) { $childPid } else { $Process.Id }
        $info = [ordered]@{
            suite = "main"
            component = $Component
            pid = $effectivePid
            wrapper_pid = $Process.Id
            child_pid = $childPid
            started_at = (Get-Date).ToString("o")
            headless = [bool]$Headless
            api_port = $MainApiPort
            backtest_port = 8001
            workdir = $WorkDir
            command = $Command
            repo_root = $RepoRoot
            project_dir = $ProjectDir
            reports_root = $ReportsRoot
            output_root = $OutputRoot
            python = $Py
        }
        $path = Join-Path $RuntimeDir ($Component.ToLowerInvariant() + ".json")
        $info | ConvertTo-Json -Depth 5 | Set-Content -Path $path -Encoding UTF8
    } catch {
        Write-Warning "PID info write failed for ${Component}: $($_.Exception.Message)"
    }
}

function Start-StackProcess {
    param(
        [string]$Title,
        [string]$WorkDir,
        [string]$Command,
        [string]$Component,
        [string]$OutputRoot = "",
        [string]$Needle = "",
        [int]$ListenPort = 0
    )
    $psCmd = "`$Host.UI.RawUI.WindowTitle = '$Title'; Set-Location '$WorkDir'; $Command"
    $args = if ($Headless) {
        @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", $psCmd)
    } else {
        @("-NoExit", "-ExecutionPolicy", "Bypass", "-Command", $psCmd)
    }
    $windowStyle = if ($Headless) { "Hidden" } else { "Normal" }
    $p = Start-Process -FilePath "powershell.exe" -ArgumentList $args -WorkingDirectory $WorkDir -WindowStyle $windowStyle -PassThru
    Write-PidInfo -Component $Component -Process $p -WorkDir $WorkDir -Command $Command -OutputRoot $OutputRoot -Needle $Needle -ListenPort $ListenPort
    return $p
}

Write-Host "=== AIInvest Bootstrap ===" -ForegroundColor Cyan
Write-Host "RepoRoot: $RepoRoot"
Write-Host "ProjectDir: $ProjectDir"

if ($CleanFirst) {
    $stopScript = Join-Path $ProjectDir "stop_aiinvest.ps1"
    if (Test-Path $stopScript) {
        Write-Host "Clean-first: running stop script..." -ForegroundColor Yellow
        & powershell.exe -ExecutionPolicy Bypass -File $stopScript | Out-Host
        Start-Sleep -Seconds 1
    }
}

if (-not (Test-Path $Py)) {
    throw "Python venv not found: $Py"
}
if (-not (Test-Path $NpmCmd)) {
    try {
        $npmResolved = (Get-Command npm.cmd -ErrorAction Stop).Source
        if ($npmResolved) { $NpmCmd = $npmResolved }
    } catch {
        throw "npm.cmd not found. Install Node.js or adjust path in start_aiinvest.ps1."
    }
}

if (-not (Test-TcpPort -HostName "127.0.0.1" -Port 27017)) {
    if (Test-Path $MongoD) {
        Write-Host "Starting MongoDB..." -ForegroundColor Yellow
        $mongoCmd = "& '$MongoD' --dbpath '$MongoData' --bind_ip 127.0.0.1 --port 27017"
        Start-StackProcess -Title "AIInvest MongoDB" -WorkDir $ProjectDir -Command $mongoCmd -Component "mongodb" -OutputRoot $DbRoot -Needle "mongod" | Out-Null
        Start-Sleep -Seconds 2
    } else {
        Write-Warning "MongoDB is not listening on 27017 and mongod.exe was not found."
    }
} else {
    Write-Host "MongoDB already running on 27017." -ForegroundColor Green
}

if (-not (Test-TcpPort -HostName "127.0.0.1" -Port $MainApiPort)) {
    Write-Host "Starting API ($MainApiPort)..." -ForegroundColor Yellow
    $apiCmd = "& '$Py' -m uvicorn app:app --host 127.0.0.1 --port $MainApiPort"
    Start-StackProcess -Title "AIInvest API :$MainApiPort" -WorkDir $PyCoreDir -Command $apiCmd -Component "api" -OutputRoot $ProjectDir -Needle "--port $MainApiPort" -ListenPort $MainApiPort | Out-Null
} else {
    Write-Host "API :$MainApiPort already running." -ForegroundColor Green
}

if (-not $NoBacktestApi) {
    if (-not (Test-TcpPort -HostName "127.0.0.1" -Port 8001)) {
        Write-Host "Starting Backtest API (8001)..." -ForegroundColor Yellow
        $btCmd = "& '$Py' -m uvicorn app:app --host 127.0.0.1 --port 8001"
        Start-StackProcess -Title "AIInvest API :8001 (Backtest)" -WorkDir $PyCoreDir -Command $btCmd -Component "backtest" -OutputRoot $ProjectDir -Needle "--port 8001" -ListenPort 8001 | Out-Null
    } else {
        Write-Host "Backtest API :8001 already running." -ForegroundColor Green
    }
}

if (-not $NoCollector) {
    $collectors = @(Get-CollectorProcesses)
    if ($collectors.Count -gt 1) {
        $preferred = @($collectors | Where-Object { (($_.CommandLine -as [string]) -like "*venv\\Scripts\\python.exe*") } | Select-Object -First 1)
        if ($preferred.Count -eq 0) {
            $preferred = @($collectors | Select-Object -First 1)
        }
        $preferredPid = if ($preferred.Count -gt 0) { [int]$preferred[0].ProcessId } else { 0 }
        $duplicates = @($collectors | Where-Object { [int]$_.ProcessId -ne $preferredPid })
        if ($duplicates.Count -gt 0) {
            Stop-Processes -Items $duplicates -Label "Collector"
        }
        $collectors = @(Get-CollectorProcesses)
    }
    if ($collectors.Count -eq 0) {
        Write-Host "Starting Data Collector..." -ForegroundColor Yellow
        $collectorCmd = "& '$Py' '$PyCoreDir\data_collector.py'"
        Start-StackProcess -Title "AIInvest Collector" -WorkDir $PyCoreDir -Command $collectorCmd -Component "collector" -OutputRoot $ReportsRoot -Needle "data_collector.py" | Out-Null
    } else {
        Write-Host "Data Collector already running." -ForegroundColor Green
    }
}

foreach ($workerName in @("market_data_worker.py", "market_intel_worker.py")) {
    $workers = @(Get-WorkerProcesses -ScriptName $workerName)
    if ($workers.Count -le 1) { continue }
    $preferred = @($workers | Where-Object { (($_.CommandLine -as [string]) -like "*venv\\Scripts\\python.exe*") } | Select-Object -First 1)
    if ($preferred.Count -eq 0) {
        $preferred = @($workers | Select-Object -First 1)
    }
    $preferredPid = if ($preferred.Count -gt 0) { [int]$preferred[0].ProcessId } else { 0 }
    $duplicates = @($workers | Where-Object { [int]$_.ProcessId -ne $preferredPid })
    if ($duplicates.Count -gt 0) {
        Stop-Processes -Items $duplicates -Label $workerName
    }
}

if (-not (Test-TcpPort -HostName "localhost" -Port 5173)) {
    Write-Host "Starting Dashboard..." -ForegroundColor Yellow
    $dashCmd = "`$env:VITE_API_BASE='http://127.0.0.1:$MainApiPort'; `$env:VITE_BACKTEST_API_BASE='http://127.0.0.1:8001'; & '$NpmCmd' run dev -- --host 127.0.0.1 --port 5173"
    Start-StackProcess -Title "AIInvest Dashboard" -WorkDir $DashboardDir -Command $dashCmd -Component "dashboard" -OutputRoot $ProjectDir -Needle "--port 5173" -ListenPort 5173 | Out-Null
} else {
    Write-Host "Dashboard already running on :5173." -ForegroundColor Green
}

if (-not $NoAutoBot) {
    Write-Host "Waiting for API to become ready..." -ForegroundColor Yellow
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        if (Test-TcpPort -HostName "127.0.0.1" -Port $MainApiPort) { break }
        Start-Sleep -Milliseconds 800
    }
    try {
        $cfg = @{
            MODE = "live"
            SHADOW_MODE_ENABLED = $true
            DEFAULT_BROKER = "kraken"
            INTERVAL_MINUTES = 5
            COLLECT_INTERVALS = "1,5,15,60"
            TRADING_BINANCE_ENABLED = $true
            TRADING_IBKR_ENABLED = $false
            CROSS_ASSET_SHADOW_ENABLED = $false
            CROSS_ASSET_PROVIDER = "stooq"
            INTEL_ENABLED = $false
            AUTO_TUNE_ENABLED = $false
            AUTO_TUNE_APPLY = $false
            DYNAMIC_ASSETS_ENABLED = $false
            EXPAND_UNIVERSE_FROM_RECOMMENDATIONS = $false
            PF_GUARD_ENABLED = $true
            TIME_EXIT_MINUTES = 1440
            SYMBOLS = "BTC/USDT,ETH/USDT"
            BINANCE_SYMBOLS = "SOL/USDT,BNB/USDT,XRP/USDT,DOGE/USDT,TRX/USDT"
            ALWAYS_ACTIVE_SYMBOLS = "BTC/USDT,ETH/USDT"
            SL_ATR_MULT = 1.2
            TP_ATR_MULT = 2.5
            FEE_AWARE_GATE_ENABLED = $true
            FEE_AWARE_MIN_EDGE_MULT = 1.05
            SIGNAL_QUALITY_ENABLED = $true
            SIGNAL_QUALITY_MIN_PROB = 0.55
            SIGNAL_QUALITY_THROTTLE_PROB = 0.62
            SIGNAL_QUALITY_SHADOW_HORIZON_MIN = 60
            NEWS_WORKER_ENABLED = $true
            MARKET_DATA_WORKER_ENABLED = $true
            RESUME_ON_START = $false
        } | ConvertTo-Json
        try {
            Invoke-RestMethod -Uri "http://127.0.0.1:$MainApiPort/bot/stop" -Method Post -TimeoutSec 12 | Out-Null
        } catch {}
        Invoke-RestMethod -Uri "http://127.0.0.1:$MainApiPort/bot/config" -Method Put -ContentType "application/json" -Body $cfg -TimeoutSec 20 | Out-Null
        Invoke-RestMethod -Uri "http://127.0.0.1:$MainApiPort/bot/start" -Method Post | Out-Null
        Write-Host "Bot start request sent." -ForegroundColor Green
    } catch {
        Write-Warning "Bot start failed: $($_.Exception.Message)"
    }
}

$dashDeadline = (Get-Date).AddSeconds(30)
$dashUp = $false
while ((Get-Date) -lt $dashDeadline) {
    if (Test-TcpPort -HostName "127.0.0.1" -Port 5173) {
        $dashUp = $true
        break
    }
    Start-Sleep -Milliseconds 800
}
if (-not $dashUp) {
    Write-Warning "Dashboard port 5173 is not up. Check the 'AIInvest Dashboard' console window for npm/vite error."
}

Write-Host ""
$dashUrl = Get-ReachableHttpUrl -Port 5173
$apiUrl = Get-ReachableHttpUrl -Port $MainApiPort
if ($dashUrl) {
    Write-Host "Dashboard: $dashUrl" -ForegroundColor Cyan
} else {
    Write-Host "Dashboard: not reachable on port 5173" -ForegroundColor Yellow
}
if ($apiUrl) {
    Write-Host "API: $apiUrl" -ForegroundColor Cyan
} else {
    Write-Host "API: not reachable on port $MainApiPort" -ForegroundColor Yellow
}
