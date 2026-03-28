# _claude.md

Aktualni provozni a vyvojovy kontext projektu `AIInvest` po reorganizaci adresaru. Posledni vecna aktualizace k `2026-03-28`.

## Update 2026-03-28

Tohle je ted nejdulezitejsi operacni stav:

- referencni stary suite:
  - `C:\aiinvest\RPRTS\_shadow_tests\weekly-suite-20260319-220522`
  - `bin_krak.completed = true`
  - `ibkr.completed = true`
- aktivni validacni suite:
  - `C:\aiinvest\RPRTS\_shadow_tests\weekly-suite-20260327-192855`
  - oba subsuite heartbeat `state=loop`
- IBKR gateway paper API:
  - port `4002`
- hlavni API:
  - `8010`
- IBKR API:
  - `8110`

Datovy a reportovy stav po backfillu:

- IBKR cross-asset coverage byla zvednuta z velmi slabeho stavu na pouzitelne H1 pokryti
- po deduplikaci posledni H1 vrstvy zustava:
  - `EURUSD`: `522`
  - `GBPUSD`: `522`
  - `USDJPY`: `521`
  - `XAUUSD`: `505`
  - `XAGUSD`: `505`
  - `CL`: `505`
- `CL` uz neni blocker
- `market_candles` a `cross_asset_candles` jsou po zasahu mix:
  - historicke `ibkr`
  - fallback `yahoo`
  - live/fallback `stooq`

Post-backfill vyhodnoceni stareho suite:

- kombinovany report:
  - `C:\aiinvest\RPRTS\_shadow-reports\weekly_postbackfill\manual_weekly_shadow_20260319-postbackfill_20260327_221917.md`
- per-suite reporty:
  - `C:\aiinvest\RPRTS\_shadow-reports\bin_krak\report_all_h_bin_krak_b3c92f4a74f3_20260327_222346.md`
  - `C:\aiinvest\RPRTS\_shadow-reports\ibkr\report_all_h_ibkr_b3c92f4a74f3_20260327_222347.md`
- hlavni benchmark:
  - `60m`: combined `C_pnl=-1.2960`, `bin_krak eval=84`, `ibkr eval=6`
  - `240m`: combined `C_pnl=+4.1233`, `bin_krak eval=83`, `ibkr eval=4`
  - `1440m`: combined `C_pnl=+4.7928`, `bin_krak eval=15`, `ibkr eval=0`

Interpretacni pravidlo:

- stary suite po backfillu je uz lepe evaluovatelny na kratkych a strednich horizontech
- stale to ale neni silny dukaz pro IBKR edge, protoze IBKR cast ma malo signalu
- skutecna validacni priorita je novy suite `weekly-suite-20260327-192855`

## Root layout

V rootu `C:\aiinvest` maji po rozdeleni zustavat jen tyto polozky:

- `.git`
- `.gitignore`
- `LLM`
- `DTB`
- `PRJCT`
- `RPRTS`
- `BCKP`

Obsah:

- `LLM`
  - lokalni modely, `llama`, `skills`
- `DTB`
  - `MongoDB`, `mongo_dump`
- `PRJCT`
  - aplikace, launcher, dashboard, Python backend, QA, runtime skripty
- `RPRTS`
  - reporty, testovaci artefakty, kontextove a historicke soubory
- `BCKP`
  - zalohy a build verify artefakty

## Dulezite cesty

- launcher projekt:
  - `C:\aiinvest\PRJCT\csharp-ui\AIInvestLauncher`
- Python backend:
  - `C:\aiinvest\PRJCT\python-core`
- dashboard:
  - `C:\aiinvest\PRJCT\dashboard`
- runtime skripty:
  - `C:\aiinvest\PRJCT\start_aiinvest.ps1`
  - `C:\aiinvest\PRJCT\stop_aiinvest.ps1`
  - `C:\aiinvest\PRJCT\start_ibkr_shadow_stack.ps1`
  - `C:\aiinvest\PRJCT\shadow_trading_test_suite.ps1`
  - `C:\aiinvest\PRJCT\shadow_trading_watchdog.ps1`
  - `C:\aiinvest\PRJCT\start_weekly_shadow_suite.ps1`
  - `C:\aiinvest\PRJCT\start_hourly_shadow_reports.ps1`
  - `C:\aiinvest\PRJCT\schedule_weekly_shadow_reports.ps1`
  - `C:\aiinvest\PRJCT\run_weekly_shadow_report.ps1`
- databaze:
  - `C:\aiinvest\DTB\MongoDB`
- reporty a suite artefakty:
  - `C:\aiinvest\RPRTS\_shadow_tests`
  - `C:\aiinvest\RPRTS\_shadow-reports`
- kontextove soubory:
  - `C:\aiinvest\RPRTS\_claude.md`
  - `C:\aiinvest\RPRTS\_context.md`

## Aktualni stav runtime

K tomuto update runtime bezi, stary suite je uzavreny a novy weekly suite aktivne pokracuje.

Potvrzeny stav:

- launcher bezi v `Debug`
- `IBKR Gateway` bezi a API posloucha na `4002`
- porty `5173`, `8001`, `8010`, `8101`, `8110`, `4002`, `27017` poslouchaji
- hlavni bot bezi na `8010`
- `IBKR` bot bezi na `8110`
- aktivni weekly suite je `weekly-suite-20260327-192855`
- puvodni `weekly-suite-20260319-220522` je post-backfill referencni artefakt
- collector sbira data do MongoDB

## Aktualni architektura

- `AIInvestLauncher` ma dal pouzivat root `C:\aiinvest`
- launcher a skripty musi po reorganizaci odvozovat:
  - projekt z `C:\aiinvest\PRJCT`
  - reporty z `C:\aiinvest\RPRTS`
  - databazi z `C:\aiinvest\DTB`
  - LLM runtime z `C:\aiinvest\LLM`
- `Debug` je jedina pracovni varianta launcheru; `Release` se nema pouzivat

## Pracovni pravidlo pro kontext

Pro projekt je nastavene tohle provozni pravidlo:

- UI zmena nebo dashboard:
  - pouzit `Figma` + `Playwright`
- incident, testy, rozhodnuti, roadmapa:
  - pouzit `Linear`
- runtime validace:
  - pouzit `Playwright` a lokalni logy
- backend, trading, config:
  - primarne pouzit lokalni kod, runtime data a reporty

Interpretace:

- `Figma` je selektivni kontext pro UI vrstvu, ne pro cely projekt
- `Linear` je preferovany zdroj projektoveho kontextu pro incidenty a rozhodnuti
- `Playwright` je preferovany pro realne overeni dashboardu a webovych flow
- pokud `Notion` neni v session dostupny, nepouziva se jako zavisly zdroj pravdy

## Start a stop po reorganizaci

Spoustet z `PRJCT`:

```powershell
Set-Location C:\aiinvest\PRJCT
powershell -ExecutionPolicy Bypass -File .\start_aiinvest.ps1
```

IBKR stack:

```powershell
Set-Location C:\aiinvest\PRJCT
powershell -ExecutionPolicy Bypass -File .\start_ibkr_shadow_stack.ps1
```

Stop:

```powershell
Set-Location C:\aiinvest\PRJCT
powershell -ExecutionPolicy Bypass -File .\stop_aiinvest.ps1
```

Weekly suite:

```powershell
Set-Location C:\aiinvest\PRJCT
powershell -ExecutionPolicy Bypass -File .\start_weekly_shadow_suite.ps1 -DurationDays 7 -DailyHour 15
```

## Validace po presunu

Po reorganizaci je treba overovat minimalne:

1. PowerShell parser pro provozni skripty v `PRJCT`
2. Python syntax pro `python-core`
3. `dotnet build` launcheru v `Debug`
4. ze launcher spravne vidi:
   - `RPRTS\_shadow_tests`
   - `RPRTS\_shadow-reports`
   - `PRJCT\python-core`
   - `DTB\MongoDB`
   - `LLM\models` a `LLM\llama`

## Poznamky pro dalsi praci

- pokud si nejsi jisty presunem souboru, ponechat ho na aktualnim miste
- reporty a historie se nemaji mazat
- vsechny nove provozni path fixy se maji vazat na novou strukturu, ne vracet root zpet do ploche varianty
- kontextove soubory `_claude.md` a `_context.md` se maji udrzovat aktualni pred dalsi vetsi zmenou nebo kompresi kontextu
- weekly `run_dir` se ma zapisovat a kontrolovat proti skutecne ceste v `RPRTS\_shadow_tests`
- `start_ibkr_shadow_stack.ps1` ma po aktualnich fixech zachovat:
  - `IBKR_TWS_PORT=4002`
  - `RESUME_ON_START=true`
- pri monitoringu po backfillu sledovat hlavne:
  - `60m` vs `240m`
  - heartbeat noveho suite
  - fresh/stale stav IBKR cross-asset dat
