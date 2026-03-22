# AIInvest Stability Test Harness

This folder contains repeatable operational tests focused on:

- startup/restart reliability
- API/dashboard uptime and latency
- recovery from forced process crashes

## Files

- `qa\aiinvest_stability_suite.ps1`: main test runner (creates timestamped report)
- `qa\aiinvest_watchdog.ps1`: optional self-heal watchdog (port + API health based)

## Quick Start

Run a 2-hour stability suite with recovery checks:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\qa\aiinvest_stability_suite.ps1 -DurationMinutes 120 -RestartCycles 3 -CrashDrill
```

Run watchdog continuously (recommended in separate window):

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\qa\aiinvest_watchdog.ps1 -LoopSeconds 20
```

## Output

Each suite run creates:

- `qa\runs\stability-YYYYMMDD-HHMMSS\events.log`
- `qa\runs\stability-YYYYMMDD-HHMMSS\samples.jsonl`
- `qa\runs\stability-YYYYMMDD-HHMMSS\summary.json`
- `qa\runs\stability-YYYYMMDD-HHMMSS\summary.md`

## Notes

- The suite uses existing project scripts:
  - `start_aiinvest.ps1`
  - `stop_aiinvest.ps1`
- It never deletes trading data.
- It can force-kill API process only when `-CrashDrill` is enabled.
