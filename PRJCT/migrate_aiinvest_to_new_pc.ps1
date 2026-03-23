param(
    [ValidateSet("Export", "Import")]
    [string]$Mode = "Export",
    [string]$BundlePath = "",
    [switch]$IncludeDatabaseData,
    [switch]$IncludeMongoServer,
    [switch]$IncludeReports,
    [switch]$IncludeModels,
    [switch]$CreateZip,
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$layoutHelper = Join-Path $ScriptDir "resolve_aiinvest_layout.ps1"
if (-not (Test-Path $layoutHelper)) {
    throw "Missing layout helper: $layoutHelper"
}
. $layoutHelper

$layout = Get-AIInvestLayout -RepoRoot (Split-Path -Parent $ScriptDir)
$RepoRoot = $layout.RepoRoot
$ProjectDir = $layout.ProjectDir
$DatabaseDir = $layout.DatabaseDir
$ReportsDir = $layout.ReportsDir
$LlmDir = $layout.LlmDir

function New-CleanDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [switch]$AllowReplace
    )

    if (Test-Path $Path) {
        if (-not $AllowReplace) {
            throw "Path already exists: $Path"
        }
        Remove-Item -Path $Path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $Path -Force | Out-Null
}

function Ensure-ParentDirectory {
    param([Parameter(Mandatory = $true)][string]$Path)
    $parent = Split-Path -Parent $Path
    if (-not [string]::IsNullOrWhiteSpace($parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
}

function Copy-Tree {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (-not (Test-Path $Source)) {
        return $false
    }

    Ensure-ParentDirectory -Path $Destination
    Copy-Item -Path $Source -Destination $Destination -Recurse -Force
    return $true
}

function Copy-File {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (-not (Test-Path $Source)) {
        return $false
    }

    Ensure-ParentDirectory -Path $Destination
    Copy-Item -Path $Source -Destination $Destination -Force
    return $true
}

function Get-DefaultBundlePath {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    return Join-Path $RepoRoot ("_transfer\\aiinvest-transfer-" + $stamp)
}

function Get-GitValue {
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    try {
        $out = & git -C $RepoRoot @Arguments 2>$null
        if ($LASTEXITCODE -eq 0) {
            return ($out | Out-String).Trim()
        }
    } catch {
    }
    return ""
}

function Read-DotEnv {
    param([Parameter(Mandatory = $true)][string]$Path)

    $map = @{}
    if (-not (Test-Path $Path)) {
        return $map
    }

    foreach ($line in Get-Content -Path $Path) {
        $trimmed = [string]$line
        if ([string]::IsNullOrWhiteSpace($trimmed)) { continue }
        if ($trimmed.TrimStart().StartsWith("#")) { continue }
        $idx = $trimmed.IndexOf("=")
        if ($idx -lt 1) { continue }
        $key = $trimmed.Substring(0, $idx).Trim()
        $value = $trimmed.Substring($idx + 1)
        $map[$key] = $value
    }
    return $map
}

function Get-ModelCandidatesFromEnv {
    param([hashtable]$EnvMap)

    $paths = @()
    foreach ($key in @("LLAMA_MODEL_PATH", "LLAMA_CLI_PATH")) {
        if ($EnvMap.ContainsKey($key)) {
            $value = [string]$EnvMap[$key]
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $paths += $value.Trim()
            }
        }
    }
    return @($paths | Where-Object { -not [string]::IsNullOrWhiteSpace($_) } | Select-Object -Unique)
}

function Resolve-BundlePath {
    if ([string]::IsNullOrWhiteSpace($BundlePath)) {
        return Get-DefaultBundlePath
    }
    return $BundlePath
}

function Export-Bundle {
    $resolvedBundle = Resolve-BundlePath
    New-CleanDirectory -Path $resolvedBundle -AllowReplace:$Force

    $repoDir = Join-Path $resolvedBundle "repo"
    $secretsDir = Join-Path $resolvedBundle "secrets"
    $runtimeDir = Join-Path $resolvedBundle "runtime"
    New-Item -ItemType Directory -Path $repoDir, $secretsDir, $runtimeDir -Force | Out-Null

    $envPath = Join-Path $ProjectDir "python-core\\.env"
    $envMap = Read-DotEnv -Path $envPath

    $manifest = [ordered]@{
        exported_at = (Get-Date).ToString("o")
        repo_root = $RepoRoot
        branch = Get-GitValue -Arguments @("branch", "--show-current")
        commit = Get-GitValue -Arguments @("rev-parse", "HEAD")
        remote_origin = Get-GitValue -Arguments @("remote", "get-url", "origin")
        layout = [ordered]@{
            project_dir = $layout.ProjectDirName
            reports_dir = $layout.ReportsDirName
            database_dir = $layout.DatabaseDirName
            llm_dir = $layout.LlmDirName
            backup_dir = $layout.BackupDirName
        }
        included = [ordered]@{
            env = $false
            layout_json = $false
            database_data = $false
            mongo_server = $false
            reports = $false
            models = @()
        }
    }

    $manifest.included.layout_json = Copy-File -Source (Join-Path $RepoRoot "aiinvest.layout.json") -Destination (Join-Path $repoDir "aiinvest.layout.json")
    $manifest.included.env = Copy-File -Source $envPath -Destination (Join-Path $secretsDir "python-core\\.env")

    if ($IncludeDatabaseData) {
        $manifest.included.database_data = Copy-Tree -Source (Join-Path $DatabaseDir "MongoDB\\data") -Destination (Join-Path $runtimeDir "MongoDB\\data")
    }

    if ($IncludeMongoServer) {
        $manifest.included.mongo_server = Copy-Tree -Source (Join-Path $DatabaseDir "MongoDB\\server") -Destination (Join-Path $runtimeDir "MongoDB\\server")
    }

    if ($IncludeReports) {
        $manifest.included.reports = Copy-Tree -Source $ReportsDir -Destination (Join-Path $runtimeDir $layout.ReportsDirName)
    }

    if ($IncludeModels) {
        $modelTargets = New-Object System.Collections.Generic.List[string]
        foreach ($path in Get-ModelCandidatesFromEnv -EnvMap $envMap) {
            if (-not (Test-Path $path)) { continue }
            $leaf = Split-Path -Leaf $path
            $dest = Join-Path (Join-Path $runtimeDir "models") $leaf
            if (Copy-File -Source $path -Destination $dest) {
                $modelTargets.Add($leaf)
            }
        }

        $defaultModelsDir = Join-Path $LlmDir "models"
        if ((Test-Path $defaultModelsDir) -and $modelTargets.Count -eq 0) {
            if (Copy-Tree -Source $defaultModelsDir -Destination (Join-Path $runtimeDir "models")) {
                $modelTargets.Add("models\\")
            }
        }

        $manifest.included.models = @($modelTargets)
    }

    $readme = @"
AIInvest transfer bundle
========================

Created: $($manifest.exported_at)
Branch:  $($manifest.branch)
Commit:  $($manifest.commit)
Remote:  $($manifest.remote_origin)

This bundle contains local-only items that are not stored in GitHub.
Clone the repository fresh on the new PC, then restore items from this bundle.

Recommended next step on the new PC:
  powershell -ExecutionPolicy Bypass -File PRJCT\migrate_aiinvest_to_new_pc.ps1 -Mode Import -BundlePath "<this bundle path>"

Included:
  env:           $($manifest.included.env)
  layout_json:   $($manifest.included.layout_json)
  database_data: $($manifest.included.database_data)
  mongo_server:  $($manifest.included.mongo_server)
  reports:       $($manifest.included.reports)
  models:        $(([string]::Join(", ", @($manifest.included.models))))
"@
    Set-Content -Path (Join-Path $resolvedBundle "README.txt") -Value $readme -Encoding UTF8
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -Path (Join-Path $resolvedBundle "manifest.json") -Encoding UTF8

    if ($CreateZip) {
        $zipPath = $resolvedBundle.TrimEnd("\") + ".zip"
        if (Test-Path $zipPath) {
            if (-not $Force) {
                throw "Zip already exists: $zipPath"
            }
            Remove-Item -Path $zipPath -Force
        }
        try {
            Compress-Archive -Path (Join-Path $resolvedBundle "*") -DestinationPath $zipPath -Force
            Write-Host "Bundle zip created: $zipPath" -ForegroundColor Green
        } catch {
            Write-Warning "Zip creation failed. Full MongoDB bundles often exceed Compress-Archive limits on Windows."
            Write-Warning "The transfer directory itself is already usable: $resolvedBundle"
            Write-Warning "Recommended next step: copy the folder directly to an NTFS/exFAT disk, or create the archive with 7-Zip."
            throw
        }
    }

    Write-Host "Bundle created: $resolvedBundle" -ForegroundColor Green
}

function Restore-IfPresent {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,
        [Parameter(Mandatory = $true)]
        [string]$Destination
    )

    if (-not (Test-Path $Source)) {
        return $false
    }

    if (Test-Path $Destination) {
        if (-not $Force) {
            throw "Target already exists, use -Force to replace: $Destination"
        }
        Remove-Item -Path $Destination -Recurse -Force
    }

    Ensure-ParentDirectory -Path $Destination
    Copy-Item -Path $Source -Destination $Destination -Recurse -Force
    return $true
}

function Import-Bundle {
    $resolvedBundle = Resolve-BundlePath
    if (-not (Test-Path $resolvedBundle)) {
        throw "Bundle path not found: $resolvedBundle"
    }

    $restored = [ordered]@{
        env = $false
        layout_json = $false
        database_data = $false
        mongo_server = $false
        reports = $false
        models = $false
    }

    $restored.layout_json = Restore-IfPresent -Source (Join-Path $resolvedBundle "repo\\aiinvest.layout.json") -Destination (Join-Path $RepoRoot "aiinvest.layout.json")
    $restored.env = Restore-IfPresent -Source (Join-Path $resolvedBundle "secrets\\python-core\\.env") -Destination (Join-Path $ProjectDir "python-core\\.env")
    $restored.database_data = Restore-IfPresent -Source (Join-Path $resolvedBundle "runtime\\MongoDB\\data") -Destination (Join-Path $DatabaseDir "MongoDB\\data")
    $restored.mongo_server = Restore-IfPresent -Source (Join-Path $resolvedBundle "runtime\\MongoDB\\server") -Destination (Join-Path $DatabaseDir "MongoDB\\server")
    $restored.reports = Restore-IfPresent -Source (Join-Path $resolvedBundle ("runtime\\" + $layout.ReportsDirName)) -Destination $ReportsDir

    $modelsSource = Join-Path $resolvedBundle "runtime\\models"
    if (Test-Path $modelsSource) {
        $defaultModelsTarget = Join-Path $LlmDir "models"
        $restored.models = Restore-IfPresent -Source $modelsSource -Destination $defaultModelsTarget
    }

    $summary = $restored | ConvertTo-Json -Depth 4
    Write-Host "Import finished." -ForegroundColor Green
    Write-Host $summary
    Write-Host ""
    Write-Host "Next steps:" -ForegroundColor Cyan
    Write-Host "  1. Create python venv and install requirements."
    Write-Host "  2. Run npm install in PRJCT\\dashboard."
    Write-Host "  3. If needed, install MongoDB binaries or use imported DTB\\MongoDB\\server."
    Write-Host "  4. Start stack with PRJCT\\Start-AIInvest.cmd."
}

if ($Mode -eq "Export") {
    Export-Bundle
} else {
    Import-Bundle
}
