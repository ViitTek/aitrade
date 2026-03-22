---
name: ailauncher-runtime-guard
description: Hlidat provozni stabilitu AIlauncher / AIInvestLauncher. Pouzit kdyz je potreba analyzovat watchdogy, self-heal, recovery, port conflicts, orphan procesy, logy a spolehlivost start-stop skriptu pred finalnim spustenim.
---

# RuntimeGuard

## Overview

Zamereni na to, jestli se `AIlauncher` umi stabilne rozbehnout, zastavit a zotavit z problemu bez manualniho chaosu. Tahle role hlida operacni spolehlivost, ne kvalitu trading signalu.

## Workflow

1. Zkontrolovat provozni obraz.
- Precist `0-current_status.md`, kdyz ma doporuceni zasah do guardrails nebo stabilizacniho rezimu.
- Sesbirat launcher logy, watchdog logy, port state a seznam bezicich procesu.

2. Hledat provozni slabiny.
- Overit watchdogy, self-heal, restart flow, hard stop a cleanup po padu.
- Zkontrolovat `shadow_trading_watchdog.ps1`, `qa\aiinvest_watchdog.ps1`, `start_aiinvest.ps1`, `stop_aiinvest.ps1` a `Start-AIInvest.cmd`.
- Vsimat si orphan procesu, port conflicts, cooldown problemu a opakovanych DOWN/UP prechodu.

3. Navrhnout hardening.
- Preferovat male zmeny s vysokym dopadem na spolehlivost.
- Kdyz problem neni potvrzen, doporucit kratky dry-run nebo stability run misto velke upravy.

## Output

- Jedna sekce `Operational risks`.
- Jedna sekce `Recovery gaps`.
- Jedna sekce `Hardening actions` s kratkou prioritou.
