Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$script:ResolveAiInvestLayoutBaseDir = if ($PSScriptRoot) { $PSScriptRoot } elseif ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { (Get-Location).Path }

function Get-AIInvestLayout {
    param(
        [string]$RepoRoot = ""
    )

    $scriptDir = $script:ResolveAiInvestLayoutBaseDir
    $resolvedRoot = if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
        Split-Path -Parent $scriptDir
    } else {
        $RepoRoot
    }

    $layoutPath = Join-Path $resolvedRoot "aiinvest.layout.json"
    $defaults = [ordered]@{
        project_dir = "PRJCT"
        reports_dir = "RPRTS"
        database_dir = "DTB"
        llm_dir = "LLM"
        backup_dir = "BCKP"
    }

    $layout = $null
    if (Test-Path $layoutPath) {
        try {
            $layout = Get-Content $layoutPath -Raw | ConvertFrom-Json
        } catch {
            $layout = $null
        }
    }

    function Get-LayoutValue([object]$Source, [string]$PropertyName, [string]$Fallback) {
        if ($null -eq $Source) { return $Fallback }
        try {
            $prop = $Source.PSObject.Properties[$PropertyName]
            if ($null -ne $prop) {
                $value = [string]$prop.Value
                if (-not [string]::IsNullOrWhiteSpace($value)) {
                    return $value
                }
            }
        } catch {
        }
        return $Fallback
    }

    $projectDirName = Get-LayoutValue -Source $layout -PropertyName "project_dir" -Fallback ([string]$defaults.project_dir)
    $reportsDirName = Get-LayoutValue -Source $layout -PropertyName "reports_dir" -Fallback ([string]$defaults.reports_dir)
    $databaseDirName = Get-LayoutValue -Source $layout -PropertyName "database_dir" -Fallback ([string]$defaults.database_dir)
    $llmDirName = Get-LayoutValue -Source $layout -PropertyName "llm_dir" -Fallback ([string]$defaults.llm_dir)
    $backupDirName = Get-LayoutValue -Source $layout -PropertyName "backup_dir" -Fallback ([string]$defaults.backup_dir)

    return [pscustomobject]@{
        RepoRoot = $resolvedRoot
        LayoutPath = $layoutPath
        ProjectDirName = $projectDirName
        ReportsDirName = $reportsDirName
        DatabaseDirName = $databaseDirName
        LlmDirName = $llmDirName
        BackupDirName = $backupDirName
        ProjectDir = Join-Path $resolvedRoot $projectDirName
        ReportsDir = Join-Path $resolvedRoot $reportsDirName
        DatabaseDir = Join-Path $resolvedRoot $databaseDirName
        LlmDir = Join-Path $resolvedRoot $llmDirName
        BackupDir = Join-Path $resolvedRoot $backupDirName
    }
}
