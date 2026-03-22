param(
    [string]$ProjectRoot = "C:\aiinvest",
    [switch]$SkipPipInstall,
    [switch]$SkipNpmInstall
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Path {
    param([string]$PathToCheck, [string]$Label)
    if (-not (Test-Path $PathToCheck)) {
        throw "$Label nebyl nalezen: $PathToCheck"
    }
}

function Run-InDir {
    param(
        [Parameter(Mandatory = $true)][string]$Dir,
        [Parameter(Mandatory = $true)][scriptblock]$Script
    )
    Push-Location $Dir
    try { & $Script }
    finally { Pop-Location }
}

function Try-CopyTemplateEnv {
    param([string]$CoreDir)
    $envPath = Join-Path $CoreDir ".env"
    if (Test-Path $envPath) {
        Write-Host ".env existuje, ponechávám." -ForegroundColor Green
        return
    }

    $candidates = @(
        (Join-Path $CoreDir ".env.example"),
        (Join-Path $CoreDir ".env.template"),
        (Join-Path $CoreDir ".env.sample")
    )
    foreach ($c in $candidates) {
        if (Test-Path $c) {
            Copy-Item $c $envPath -Force
            Write-Host "Vytvořen .env z template: $c" -ForegroundColor Yellow
            return
        }
    }

    Write-Host "Nenalezen template .env. Vytvoř prosím ručně: $envPath" -ForegroundColor DarkYellow
}

function Ensure-PythonVenv {
    param(
        [string]$CoreDir,
        [bool]$DoPipInstall = $true
    )

    $venvPython = Join-Path $CoreDir "venv\Scripts\python.exe"
    if (-not (Test-Path $venvPython)) {
        Write-Step "Vytvářím Python venv"
        Run-InDir -Dir $CoreDir -Script {
            python -m venv venv
            if ($LASTEXITCODE -ne 0) { throw "python -m venv selhalo." }
        }
    } else {
        Write-Host "venv už existuje." -ForegroundColor Green
    }

    Write-Step "Upgrade pip/setuptools/wheel"
    & $venvPython -m pip install --upgrade pip setuptools wheel
    if ($LASTEXITCODE -ne 0) { throw "Upgrade pip selhal." }

    if ($DoPipInstall) {
        $req = Join-Path $CoreDir "requirements.txt"
        if (Test-Path $req) {
            Write-Step "Instaluji Python dependencies"
            & $venvPython -m pip install -r $req
            if ($LASTEXITCODE -ne 0) { throw "pip install -r requirements.txt selhal." }
        } else {
            Write-Host "requirements.txt nenalezen, pip install přeskočen." -ForegroundColor DarkYellow
        }
    } else {
        Write-Host "Přeskakuji pip install (--SkipPipInstall)." -ForegroundColor DarkYellow
    }
}

function Ensure-DashboardNodeModules {
    param(
        [string]$DashboardDir,
        [bool]$DoNpmInstall = $true
    )
    if (-not $DoNpmInstall) {
        Write-Host "Přeskakuji npm install (--SkipNpmInstall)." -ForegroundColor DarkYellow
        return
    }
    Write-Step "Instaluji dashboard dependencies (npm install)"
    Run-InDir -Dir $DashboardDir -Script {
        npm install
        if ($LASTEXITCODE -ne 0) { throw "npm install selhal." }
    }
}

function Smoke-CompilePython {
    param([string]$CoreDir)
    $venvPython = Join-Path $CoreDir "venv\Scripts\python.exe"
    Write-Step "Smoke test: Python compile"
    & $venvPython -m py_compile (Join-Path $CoreDir "trading\api.py")
    if ($LASTEXITCODE -ne 0) { throw "py_compile trading/api.py selhal." }
    & $venvPython -m py_compile (Join-Path $CoreDir "trading\config.py")
    if ($LASTEXITCODE -ne 0) { throw "py_compile trading/config.py selhal." }
}

function Print-NextSteps {
    param([string]$Root, [string]$CoreDir)
    $venvPython = Join-Path $CoreDir "venv\Scripts\python.exe"
    Write-Step "Další kroky"
    Write-Host "1) Doplň API klíče a runtime nastavení do: $CoreDir\.env" -ForegroundColor White
    Write-Host "2) Spuštění projektu:" -ForegroundColor White
    Write-Host "   powershell -ExecutionPolicy Bypass -File `"$Root\start_aiinvest.ps1`"" -ForegroundColor Gray
    Write-Host "3) Dashboard: http://localhost:5173" -ForegroundColor White
    Write-Host "4) API: http://localhost:8010" -ForegroundColor White
    Write-Host "5) Pro ruční test API (volitelné):" -ForegroundColor White
    Write-Host "   `"$venvPython`" -m uvicorn app:app --host 127.0.0.1 --port 8010" -ForegroundColor Gray
}

Write-Step "Kontrola struktury projektu"
Assert-Path -PathToCheck $ProjectRoot -Label "Project root"

$coreDir = Join-Path $ProjectRoot "python-core"
$dashDir = Join-Path $ProjectRoot "dashboard"
Assert-Path -PathToCheck $coreDir -Label "python-core"
Assert-Path -PathToCheck $dashDir -Label "dashboard"

Write-Step "Příprava .env"
Try-CopyTemplateEnv -CoreDir $coreDir

$doPipInstall = -not [bool]$SkipPipInstall
$doNpmInstall = -not [bool]$SkipNpmInstall

Ensure-PythonVenv -CoreDir $coreDir -DoPipInstall:$doPipInstall
Ensure-DashboardNodeModules -DashboardDir $dashDir -DoNpmInstall:$doNpmInstall
Smoke-CompilePython -CoreDir $coreDir
Print-NextSteps -Root $ProjectRoot -CoreDir $coreDir

Write-Step "Setup dokončen"
