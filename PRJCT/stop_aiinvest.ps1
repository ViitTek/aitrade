Set-StrictMode -Version Latest
$ErrorActionPreference = "Continue"
$MainApiPort = 8010

function Find-Procs {
    param(
        [string]$Name,
        [string]$Needle
    )
    $rows = Get-CimInstance Win32_Process -Filter ("Name='{0}'" -f $Name) -ErrorAction SilentlyContinue
    if (-not $rows) { return @() }
    return @($rows | Where-Object {
        $cl = ($_.CommandLine -as [string])
        $cl -and $cl.Contains($Needle)
    })
}

function Stop-Procs {
    param(
        [object[]]$Rows,
        [string]$Label
    )
    if (-not $Rows -or $Rows.Count -eq 0) {
        Write-Host "${Label}: no matching process." -ForegroundColor DarkGray
        return
    }
    foreach ($r in $Rows) {
        try {
            Stop-Process -Id $r.ProcessId -Force -ErrorAction Stop
            Write-Host "${Label}: stopped PID $($r.ProcessId)" -ForegroundColor Yellow
        } catch {
            Write-Host "${Label}: PID $($r.ProcessId) already gone." -ForegroundColor DarkGray
        }
    }
}

function Show-Remaining {
    Write-Host ""
    Write-Host "Remaining relevant processes:" -ForegroundColor Cyan
    $left = @()
    $left += Find-Procs -Name "python.exe" -Needle " -m uvicorn app:app"
    $left += Find-Procs -Name "python.exe" -Needle "data_collector.py"
    $left += Find-Procs -Name "python.exe" -Needle "hourly_shadow_report_job.py"
    $left += Find-Procs -Name "node.exe" -Needle "\vite\bin\vite.js"
    $left += Find-Procs -Name "cmd.exe" -Needle " /d /s /c vite"
    $left += Find-Procs -Name "powershell.exe" -Needle "start_hourly_shadow_reports.ps1"
    $left = @($left | Sort-Object ProcessId -Unique)
    if ($left.Count -eq 0) {
        Write-Host "none" -ForegroundColor Green
    } else {
        $left | Select-Object ProcessId,Name,CommandLine | Format-Table -AutoSize
    }
}

Write-Host "=== AIInvest Stop ===" -ForegroundColor Cyan

# 1) Graceful stop bota
try {
    Invoke-RestMethod -Uri "http://localhost:$MainApiPort/bot/stop?reason=manual_stop" -Method Post -TimeoutSec 3 | Out-Null
    Write-Host "Bot stop request sent." -ForegroundColor Green
} catch {
    Write-Host "Bot stop request skipped (API not reachable)." -ForegroundColor DarkGray
}

Start-Sleep -Milliseconds 250

# 2) Hard stop runtime stack
$api = @()
$api += Find-Procs -Name "python.exe" -Needle (" -m uvicorn app:app --host 127.0.0.1 --port {0}" -f $MainApiPort)
$api += Find-Procs -Name "python.exe" -Needle (" -m uvicorn app:app --reload --host 0.0.0.0 --port {0}" -f $MainApiPort)
$api += Find-Procs -Name "python.exe" -Needle (" -m uvicorn app:app --port {0}" -f $MainApiPort)
$api = @($api | Sort-Object ProcessId -Unique)
Stop-Procs -Rows $api -Label "Main API"

$bt = @()
$bt += Find-Procs -Name "python.exe" -Needle " -m uvicorn app:app --host 127.0.0.1 --port 8001"
$bt += Find-Procs -Name "python.exe" -Needle " -m uvicorn app:app --port 8001"
$bt = @($bt | Sort-Object ProcessId -Unique)
Stop-Procs -Rows $bt -Label "Backtest API"

$collector = Find-Procs -Name "python.exe" -Needle "data_collector.py"
Stop-Procs -Rows $collector -Label "Collector"

$reportPy = Find-Procs -Name "python.exe" -Needle "hourly_shadow_report_job.py"
Stop-Procs -Rows $reportPy -Label "Hourly report job"

$reportPs = Find-Procs -Name "powershell.exe" -Needle "start_hourly_shadow_reports.ps1"
Stop-Procs -Rows $reportPs -Label "Hourly report wrapper"

$viteNode = Find-Procs -Name "node.exe" -Needle "\vite\bin\vite.js"
Stop-Procs -Rows $viteNode -Label "Dashboard node(vite)"

$viteCmd = Find-Procs -Name "cmd.exe" -Needle " /d /s /c vite"
Stop-Procs -Rows $viteCmd -Label "Dashboard cmd(vite)"

Start-Sleep -Milliseconds 400
Show-Remaining

try {
    $s = Invoke-RestMethod -Uri "http://localhost:$MainApiPort/bot/status" -Method Get -TimeoutSec 2
    Write-Host ""
    Write-Host "API :$MainApiPort still responds (running=$($s.running), run_id=$($s.run_id))." -ForegroundColor Yellow
} catch {
    Write-Host ""
    Write-Host "API :$MainApiPort is down." -ForegroundColor Green
}

Write-Host ""
Write-Host "Stop sequence finished." -ForegroundColor Cyan
