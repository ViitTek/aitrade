$Host.UI.RawUI.WindowTitle = "AIInvest Data Collector (Kraken + Binance)"
Write-Host "=== AIInvest Data Collector ===" -ForegroundColor Cyan
Write-Host "Kraken:  BTC/USDT, ETH/USDT" -ForegroundColor Yellow
Write-Host "Binance: PAXG/USDT, SOL/USDT" -ForegroundColor Yellow
Write-Host "Ctrl+C pro ukonceni" -ForegroundColor Gray
Write-Host ""

& "$PSScriptRoot\python-core\venv\Scripts\python.exe" "$PSScriptRoot\python-core\data_collector.py"
