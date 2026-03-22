---
name: trader
description: Operate and evaluate the AIInvest trading stack on Windows. Use this skill when running production checks, verifying API/bot/test health, collecting shadow-report metrics, extending horizon backfills, and producing fee-aware shadow account reports from MongoDB-backed test data.
---

# Trader

Execute repeatable operations for AIInvest trading monitoring and shadow evaluation.

## Workflow

1. Confirm stack health.
2. Confirm test runner health.
3. Collect shadow-report metrics for requested horizons.
4. Backfill missing horizons when eval samples are zero or missing.
5. Compute fee-aware local shadow accounts from test data.
6. Return a concise table and short interpretation.

## Health Check Commands

Run these checks in PowerShell from `C:\aiinvest`:

```powershell
netstat -ano | findstr LISTENING | findstr /R ":8010 :8001 :5173"
Invoke-RestMethod "http://localhost:8010/health"
Invoke-RestMethod "http://localhost:8010/bot/status"
Get-Content .\_shadow_tests\shadow-suite-20260305-152706\state.json -Raw
Get-Content .\_shadow_tests\shadow-suite-20260305-152706\heartbeat.json -Raw
```

If API `:8010` is down, start stack using `Start-AIInvest.cmd` before continuing.

## Shadow Report Collection

Use endpoint:

`/bot/signal-quality/shadow-report?lookback_hours=<N>&horizon_min=<H>&limit=10000&actions=shadow,policy,executed`

Always report at minimum:
- `shadow_eval_samples`
- `shadow_profit_factor_h`
- `shadow_win_rate_h`
- `counts.total`, `counts.total_dedup`, `counts.shadow`, `counts.policy`, `counts.blocked`

## Backfill Rule

When a requested horizon has missing/low eval coverage, backfill:

`POST /bot/signal-quality/shadow-backfill?lookback_days=30&horizon_min=<H>&limit=200000&actions=shadow,policy,executed`

Retry per horizon when API reconnects after crash. Prefer smaller batches if API becomes unstable.

## Local Shadow Account Calculation

Use:

`python-core\shadow_local_pnl.py`

Required inputs:
- `--run-id`
- `--from-iso`, `--to-iso`
- `--horizon-min`
- `--actions shadow,executed`
- `--kraken-usd 100 --binance-usd 100`
- `--stake-pct 0.10`
- `--binance-fee-rate 0.001 --kraken-fee-rate 0.0025`
- `--kraken-bases BTC,ETH`

Report:
- `equity`
- `cash_buffer`
- `total = equity + cash_buffer`
- `pnl_vs_200 = total - 200`

## Output Format

Return one flat Markdown table with columns:

`h | eval | PF | WR | trades | equity | buffer | total | PnL vs 200`

Then provide at most three bullets:
- Best horizon by `total`
- Horizon band with strongest PF stability
- Any operational issue (API restart, missing coverage, watchdog event)
