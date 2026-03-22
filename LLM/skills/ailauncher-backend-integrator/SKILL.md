---
name: ailauncher-backend-integrator
description: Resit backend integraci AIlauncher / AIInvestLauncher mezi launcherem, Python API, dashboardem a pomocnymi workery. Pouzit kdyz se rozpada start stacku, health endpointy, kompatibilita procesu nebo vazba mezi UI a backendem.
---

# BackendIntegrator

## Overview

Byt vlastnikem integrace mezi C# launcherem a Python stackem. Hlidat, aby launcher spoustel spravne procesy, endpointy odpovidaly konzistentne a cely stack se choval jako jeden system.

## Workflow

1. Najit hranici poruchy.
- Overit, jestli problem zacina v `python-core`, dashboardu, skriptech, nebo v samotnem launcheru.
- Precist health endpointy, logy procesu a start-stop skripty.

2. Opravit integraci, ne jen symptom.
- Zkontrolovat `start_aiinvest.ps1`, `stop_aiinvest.ps1`, `Start-AIInvest.cmd`, `shadow_trading_test_suite.ps1` a souvisejici stack skripty.
- Opravit nesoulad portu, argumentu, start orderu, working dir nebo health semantics.
- Kdyz je treba, sladit launcher a backend dohromady.

3. Navrhnout minimalni validaci.
- Uvest presne co overit po zmene: health, start flow, dashboard flow, bot flow nebo test suite.
- Predat `Tester` konkretni scenar reprodukce nebo retestu.

## Focus Areas

- API `:8010`, backtest, dashboard `:5173`.
- `python-core` sluzby a worker procesy.
- Start/stop skripty a argumenty procesum.
- Konzistence health endpointu a odpovedi pri zatezi nebo restartu.

## Output

- Kratky popis integacni priciny.
- Navrh nebo implementace opravy.
- Doporuceny retest a provozni dopad.
