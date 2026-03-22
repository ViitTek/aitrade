---
name: ailauncher-tester
description: Provadet testovani projektu AIlauncher / AIInvestLauncher ve workspace `c:\aiinvest`. Pouzit kdyz uzivatel chce otestovat launcher, overit start-stop-restart flow, spustit smoke nebo stabilitni testy, zreprodukovat chybu, proverit regresi v C# UI, API nebo dashboardu, nebo vratit findings-first report pred finalnim spustenim.
---

# Tester

## Overview

Testovat Windows launcher `csharp-ui\AIInvestLauncher` a navazujici AIInvest stack bez zbytecne destruktivnich zasahu. Preferovat nejlevnejsi test, ktery dava jistotu: build, potom smoke, potom cilena reprodukce, a az nakonec delsi stability run.

## Workflow

1. Ujasnit si rozsah a zkontrolovat aktualni pravdu projektu.
- Precist `0-current_status.md`, kdyz zavisi doporuceni na runtime guardrails nebo aktivni konfiguraci.
- Rozlisit, jestli jde o build check, smoke test, regresi, nebo stability run.
- Nerevertovat cizi zmeny v worktree.

2. Spustit nejmensi uzitecny test jako prvni.
- Build launcheru:
```powershell
dotnet build csharp-ui\AIInvestLauncher\AIInvestLauncher.csproj
```
- Smoke test GUI/dashboard flow:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\qa\gui_tab_switch_sim.ps1 -DurationMinutes 5
```
- Stabilita start-restart-watchdog flow:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\qa\aiinvest_stability_suite.ps1 -DurationMinutes 30 -RestartCycles 1
```

3. Vyhodnotit dukazy, ne dojmy.
- Cist `qa\runs\...\summary.md`, `summary.json`, `events.log` a `samples.jsonl`.
- Pri problemech launcheru zkontrolovat `csharp-ui\AIInvestLauncher\bin\Debug\net8.0-windows\logs\launcher\`.
- Pri stack problemech pouzit `Start-AIInvest.cmd`, `start_aiinvest.ps1` a `stop_aiinvest.ps1`.

4. Reportovat nalezy podle zavaznosti.
- Pouzivat `critical`, `high`, `medium`, `low`.
- Ke kazdemu nalezu uvadet reprodukci, dopad a nejlepsi dukazovy soubor nebo prikaz.
- Kdyz se problem nepotvrdi, rict to explicitne a vypsat coverage gap.

## Focus Areas

- Start, stop, restart a hard-stop sekvence launcheru.
- WebView2, tab switching a dashboard reconnect.
- API health na `:8010` a dashboard health na `:5173`.
- Watchdog, unlock recovery a launcher file logs.
- Regrese v `csharp-ui\AIInvestLauncher\MainForm.cs` a souvisejicich start-stop skriptech.

## Output

- Zacit findings-first reportem.
- Uvest, co bylo spusteno, co bylo jen precteno a co zustalo neotestovane.
- Kdyz nejsou nalezy, rict `No findings` a dopsat zbyvajici riziko nebo coverage gap.
