Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Assert-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw "Spusť prosím tento skript v PowerShellu jako Administrator."
    }
}

function Ensure-Winget {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        return
    }
    throw "winget nebyl nalezen. Nainstaluj App Installer z Microsoft Store a spusť skript znovu."
}

function Ensure-WingetPackage {
    param(
        [Parameter(Mandatory = $true)][string]$Id,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$Version = ""
    )

    Write-Step "Kontrola: $Label"

    $already = $false
    try {
        $listOut = winget list --id $Id --exact --accept-source-agreements 2>$null | Out-String
        if ($listOut -match [Regex]::Escape($Id)) {
            $already = $true
        }
    }
    catch {
        # pokračujeme na install
    }

    if ($already) {
        Write-Host "$Label je již nainstalováno." -ForegroundColor Green
        return
    }

    Write-Host "Instaluji $Label..." -ForegroundColor Yellow
    $args = @(
        "install",
        "--id", $Id,
        "--exact",
        "--accept-package-agreements",
        "--accept-source-agreements",
        "--scope", "machine",
        "--silent"
    )
    if ($Version -and $Version.Trim().Length -gt 0) {
        $args += @("--version", $Version)
    }

    & winget @args
    if ($LASTEXITCODE -ne 0) {
        throw "Instalace selhala: $Label ($Id), exit code $LASTEXITCODE"
    }

    Write-Host "$Label nainstalováno." -ForegroundColor Green
}

function Print-Version {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Command,
        [string[]]$Args = @("--version")
    )

    Write-Step "Verze: $Name"
    try {
        & $Command @Args
    }
    catch {
        Write-Host "Nelze zjistit verzi: $Name ($Command)" -ForegroundColor DarkYellow
    }
}

Assert-Admin
Ensure-Winget

Write-Step "Instalace požadovaných aplikací pro AIInvest"

# Doporučené minimum pro tento projekt.
Ensure-WingetPackage -Id "Python.Python.3.10" -Label "Python 3.10"
Ensure-WingetPackage -Id "OpenJS.NodeJS.LTS" -Label "Node.js LTS"
Ensure-WingetPackage -Id "Microsoft.DotNet.SDK.8" -Label ".NET SDK 8"
Ensure-WingetPackage -Id "MongoDB.Server" -Label "MongoDB Server"
Ensure-WingetPackage -Id "MongoDB.Shell" -Label "MongoDB Shell (mongosh)"
Ensure-WingetPackage -Id "Git.Git" -Label "Git"

Write-Step "Kontrola verzí"
Print-Version -Name "Python" -Command "python" -Args @("--version")
Print-Version -Name "Node" -Command "node" -Args @("--version")
Print-Version -Name "npm" -Command "npm" -Args @("--version")
Print-Version -Name ".NET" -Command "dotnet" -Args @("--version")
Print-Version -Name "mongod" -Command "mongod" -Args @("--version")
Print-Version -Name "mongosh" -Command "mongosh" -Args @("--version")
Print-Version -Name "git" -Command "git" -Args @("--version")

Write-Step "Hotovo"
Write-Host "Pokud některý příkaz hlásí 'nenalezen', zavři a znovu otevři PowerShell (PATH refresh)." -ForegroundColor Yellow
Write-Host "Další krok: připravíme skript pro setup projektu (venv, npm install, smoke test)." -ForegroundColor Cyan

