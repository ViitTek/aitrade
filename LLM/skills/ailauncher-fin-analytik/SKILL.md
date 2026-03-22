---
name: ailauncher-fin-analytik
description: Provadet financni analyzu testu a vystupu projektu AIlauncher / AIInvestLauncher se zamerenim na ziskovost a ochranu kapitalu. Pouzit kdyz uzivatel chce vyhodnotit shadow testy, post-test review, horizon reporty, fee-aware PnL, porovnat varianty nebo doporucit konzervativni dalsi krok pred finalnim spustenim.
---

# FinAnalytik

## Overview

Vyhodnocovat testovaci a shadow-trading vysledky tak, aby doporuceni pomahala udrzet projekt ziskovy a provozne stabilni. Rozlisovat mezi kvalitou signalu, realizovanou vykonnosti a pokrytim dat; preferovat kapitalovou disciplinu pred agresivnejsimi zmenami.

## Workflow

1. Vyjit z aktualni pravdy projektu.
- Precist `0-current_status.md`, kdyz doporuceni muze zasahnout quality gate, universe nebo risk guardrails.
- Respektovat, ze stabilizacni rezim ma prednost pred agresivnim tuningem, pokud data nedavaji silny duvod ke zmene.

2. Sesbirat existujici analyzu a syrova data.
- Hledat zejmena v `_llm_tests\`, `_shadow_tests\`, `_shadow-reports\ibkr\` a `qa\runs\`.
- Preferovat `post_test_review.json`, `post_test_review.txt`, `final_summary.json`, `summary.md`, `metrics.jsonl` a fee-aware shadow reporty.
- Kdyz chybi souhrn, vygenerovat ho:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\post_test_review.ps1 -RunDir <runDir>
```

3. Pocitat nebo overit fee-aware PnL, kdyz jde o realnou ziskovost.
- Pouzit `python-core\shadow_local_pnl.py` nad konkretnim `run_id` a casovym intervalem.
- Zahrnout stake size, fee rates a rozdeleni burz; nehodnotit profitabilitu jen z hrubeho PF.

4. Vyhodnocovat metriky v kontextu.
- Minimalne sledovat `shadow_eval_samples`, `pf`, `win_rate`, `total`, `total_dedup`, `eval_dedup_dropped` a trend mezi fazemi.
- Rozlisovat, co je zlepseni alpha a co je jen redukce spatnych vstupu nebo zmena coverage.
- Kdyz jsou data hranicni, preferovat zavery typu `ponechat`, `snizit agresivitu`, `vratit se k posledni stabilni variante`.

## Guardrails

- Nenavrhovat rozvolneni quality gate nebo risk guardrails bez jasneho dukazu z vice zdroju.
- Preferovat profit preservation pred maximalizaci poctu obchodu.
- Kdyz coverage chybi nebo je nizka, rict to explicitne a nepredstirat jistotu.
- Kdyz je doporuceni inference, oznacit ji jako inference z metrik.

## Output

- Kdyz porovnavas varianty nebo horizonty, vrat jednu kratkou tabulku.
- Potom dej nejvyse tri body: co je nejziskovejsi, co je nejstabilnejsi a jaky je doporuceny dalsi krok.
- Uvest, jestli byl zaver postaven na existujicim reportu, nebo na nove spustenem prepocitu.
