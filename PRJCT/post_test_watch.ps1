param(
  [Parameter(Mandatory=$true)][string]$RunDir,
  [string]$ProjectRoot = 'C:\aiinvest',
  [int]$PollSec = 30
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$runLog = Join-Path $RunDir 'run.log'
while($true){
  if(Test-Path $runLog){
    $tail = Get-Content $runLog -Tail 5 -ErrorAction SilentlyContinue
    if(($tail -join "`n") -match '(^|\n)\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} END($|\n)'){
      & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ProjectRoot 'post_test_review.ps1') -RunDir $RunDir -ProjectRoot $ProjectRoot | Out-Null
      break
    }
  }
  Start-Sleep -Seconds $PollSec
}
