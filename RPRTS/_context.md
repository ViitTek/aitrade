# _context.md

Strucny operacni kontext projektu `AIInvest` po reorganizaci. Posledni vecna aktualizace k `2026-03-28`.

## Update 2026-03-28

- puvodni weekly suite `weekly-suite-20260319-220522` uz je uzavreny:
  - `bin_krak`: `completed=true`
  - `ibkr`: `completed=true`
- pro IBKR cross-asset data probehl post-backfill a deduplikace posledni H1 vrstvy:
  - `EURUSD`: `522` H1 za 30 dni
  - `GBPUSD`: `522`
  - `USDJPY`: `521`
  - `XAUUSD`: `505`
  - `XAGUSD`: `505`
  - `CL`: `505`
- `CL` uz neni datovy blocker; dataset je ted mix `ibkr + yahoo + stooq/fallback`
- kombinovany post-backfill report nad starym suite ukazuje:
  - `h=60m`: `C_total=298.7040`, `C_pnl=-1.2960`
  - `h=240m`: `C_total=304.1233`, `C_pnl=+4.1233`
  - `h=1440m`: `C_total=304.7928`, `C_pnl=+4.7928`
  - IBKR eval count:
    - `60m = 6`
    - `240m = 4`
    - `1440m = 0`
- novy validacni suite je `weekly-suite-20260327-192855`
  - oba subsuite maji heartbeat ve stavu `loop`
  - tohle je aktualni priorita pro dalsi vyhodnocovani
- IBKR runtime byl srovnan na paper gateway port `4002`
- `start_ibkr_shadow_stack.ps1` ma pouzivat:
  - `IBKR_TWS_PORT=4002`
  - `RESUME_ON_START=true`
- watchdog/finalizace suite byly zpevneny:
  - runner ma explicitni `Finalize-Run`
  - watchdog umi expired run uzavrit bez nekonecne restart smycky
- API `data-coverage` bylo rozsireno i o cross-asset viditelnost:
  - `cross_asset_hours`
  - `cross_asset_provider`
  - `cross_asset_staleness_h`
- pomocny skript pro operacni hygienu dat:
  - `C:\aiinvest\PRJCT\python-core\dedup_market_candles.py`

## Co se zmenilo

- root `C:\aiinvest` byl rozdelen do peti hlavnich slozek:
  - `LLM`
  - `DTB`
  - `PRJCT`
  - `RPRTS`
  - `BCKP`
- aplikacni kod a runtime skripty jsou ted v `PRJCT`
- reporty, testy a kontextove soubory jsou v `RPRTS`
- databaze je v `DTB`
- LLM runtime a modely jsou v `LLM`

## Aktualni fyzicky layout

- `C:\aiinvest\PRJCT`
  - `csharp-ui`
  - `dashboard`
  - `data`
  - `docs`
  - `python-core`
  - `qa`
  - hlavni `.ps1` a `.cmd` skripty
  - `_runtime`
- `C:\aiinvest\RPRTS`
  - `_shadow_tests`
  - `_shadow-reports`
  - `_logs`
  - `_aiHistory`
  - `_Kontext`
  - `_llm_tests`
  - `chat_history*.md`
  - `0-current_status.md`
  - `_aii_current_truth.md`
  - `_claude.md`
  - `_context.md`
- `C:\aiinvest\DTB`
  - `MongoDB`
  - `mongo_dump`
- `C:\aiinvest\LLM`
  - `models`
  - `llama`
  - `skills`

## Aktualni runtime stav

Aktualne je stack spusteny, stary suite je uzavreny a novy weekly suite bezi.

Potvrzeno:

- launcher bezi v `Debug`
- `IBKR Gateway` bezi
- porty:
  - `5173 = UP`
  - `8001 = UP`
  - `8010 = UP`
  - `8101 = UP`
  - `8110 = UP`
  - `4002 = UP`
  - `27017 = UP`
- collector sbira data do MongoDB
- aktualni validacni suite `weekly-suite-20260327-192855` bezi v `RPRTS\_shadow_tests`
- puvodni suite `weekly-suite-20260319-220522` zustava jako post-backfill referencni artefakt

## Zachovane test artefakty

V `C:\aiinvest\RPRTS\_shadow_tests` zustaly ulozene predchozi suite vcetne:

- `shadow-suite-20260311-231744`
- `shadow-suite-ibkr`
- `weekly-suite-20260319-220522`
- `weekly-suite-20260327-192855`

V `C:\aiinvest\RPRTS\_shadow-reports` zustaly ulozene hlavni report roots:

- `bin_krak`
- `ibkr`
- `post_test_20260319`
- `reinterpreted_20260319`
- `weekly_20260319-220522`
- `weekly_postbackfill`
- `weekly_smoke`
- `weekly_test_prep_20260319`

## Co je po presunu kriticke

Nejdulezitejsi provozni fixy musi fungovat s rootem `C:\aiinvest`, ale realne odvozovat:

- projekt:
  - `C:\aiinvest\PRJCT`
- reporty:
  - `C:\aiinvest\RPRTS`
- databazi:
  - `C:\aiinvest\DTB`
- LLM:
  - `C:\aiinvest\LLM`

## Hlavni provozni soubory

- `C:\aiinvest\PRJCT\start_aiinvest.ps1`
- `C:\aiinvest\PRJCT\stop_aiinvest.ps1`
- `C:\aiinvest\PRJCT\start_ibkr_shadow_stack.ps1`
- `C:\aiinvest\PRJCT\shadow_trading_test_suite.ps1`
- `C:\aiinvest\PRJCT\shadow_trading_watchdog.ps1`
- `C:\aiinvest\PRJCT\start_hourly_shadow_reports.ps1`
- `C:\aiinvest\PRJCT\start_weekly_shadow_suite.ps1`
- `C:\aiinvest\PRJCT\schedule_weekly_shadow_reports.ps1`
- `C:\aiinvest\PRJCT\run_weekly_shadow_report.ps1`
- `C:\aiinvest\PRJCT\ensure_ibkr_gateway.ps1`
- `C:\aiinvest\PRJCT\csharp-ui\AIInvestLauncher\MainForm.cs`

## Launcher pravidla

- launcher root zustava `C:\aiinvest`
- launcher ma sahat do `PRJCT`, `RPRTS`, `DTB`, `LLM`
- pracovni build je pouze `Debug`
- po validaci se ma spoustet az novy `Debug` launcher

## MCP pracovni pravidlo

Pouzivat tenhle rozhodovaci ramec:

- UI zmena nebo dashboard:
  - `Figma` + `Playwright`
- incident, testy, rozhodnuti, roadmapa:
  - `Linear`
- runtime validace:
  - `Playwright` + lokalni logy
- backend, trading, config:
  - lokalni kod + runtime data + reporty

Prakticky dopad:

- `Figma` neni globalni zdroj pravdy pro backend
- `Linear` je vhodny projektovy kontext pro incidenty a rozhodnuti
- `Playwright` je preferovany nastroj pro overeni dashboardu a web flow
- kdyz `Notion` neni dostupny v aktualni session, nepouzivat ho jako povinny zdroj

## Dalsi krok

Udrzovat pri dalsich zasazich:

1. `Debug` only pro launcher
2. weekly suite a reporty zapisovat jen do `RPRTS`
3. pri runtime kontrole nejdriv health, bot status, heartbeat, watcher logy a cerstvost Mongo dat
4. MCP pouzivat podle pracovniho pravidla vyse, ne plosne
5. prioritu ma sledovani suite `weekly-suite-20260327-192855`, hlavne `60m`, `240m` a pozdeji `1440m`
