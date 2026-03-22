# Obchodní strategie

## Přehled

AIInvest používá algoritmickou breakout strategii s EMA trend filtrem, volume confirmation a volitelným sentiment/intel filtrem. Obchoduje na H1 (hodinových) svíčkách v paper mode.

## Generování signálů (engine.py)

Signál vzniká při splnění všech podmínek:

### 1. Breakout detekce

- Engine udržuje rolling buffer 300 svíček pro každý symbol
- Hledá N-candle breakout (výchozí N=10):
  - **BUY:** Close > highest high za posledních N svíček
  - **SELL:** Close < lowest low za posledních N svíček

### 2. EMA Trend filtr

- Počítá EMA(50) z close cen
- **BUY** signál projde pouze když close > EMA (uptrend)
- **SELL** signál projde pouze když close < EMA (downtrend)

### 3. Volume filtr

- Pokud `VOL_FILTER=True`:
  - Svíčka musí mít objem > `VOL_MULT × průměrný objem` (výchozí 1.5×)
  - Filtruje falešné breakouty s nízkým objemem

### 4. Cooldown

- Po zavření pozice čeká `COOLDOWN_CANDLES` svíček (výchozí 2 = 2h na H1)
- Brání okamžitému znovuotevření pozice

### 5. Sentiment filtr (volitelný)

- Pokud `SENTIMENT_ENABLED=True`:
  - Dotazuje MongoDB na sentiment za posledních `SENTIMENT_WINDOW_MINUTES` minut
  - BUY blokován při Negative sentimentu
  - SELL blokován při Positive sentimentu
  - Vyžaduje minimálně `SENTIMENT_MIN_ARTICLES` článků

### 6. Market Intel filtr (volitelný)

- Pokud `INTEL_ENABLED=True`:
  - Používá LLM analýzu z `market_intel_worker.py`
  - RISK-OFF overall blokuje BUY signály
  - BEARISH outlook pro asset blokuje BUY (a naopak) na MEDIUM/HIGH confidence
  - Intel starší než `INTEL_MAX_AGE_MINUTES` se ignoruje

## Position Management (paper.py)

### Entry

- **Sizing:** `ALLOC_PCT × equity` (výchozí 10% equity)
- Minimální order: `MIN_USD_ORDER` (výchozí 10 USDT)
- Realistická exekuce: spread model + fee na entry

### Stop Loss

- SL = entry price ± `SL_ATR_MULT × ATR` (výchozí 1.5× ATR)
- Kontroluje se každou svíčku

### Take Profit

- TP = entry price ± `TP_ATR_MULT × ATR` (výchozí 4.0× ATR)
- R:R poměr při výchozích hodnotách: 1.5 : 4.0 = 1 : 2.67

### Trailing Stop

- Pokud `TRAILING_STOP=True`:
  - **Aktivace:** Po pohybu ≥ `TRAIL_ACTIVATION_ATR × ATR` (výchozí 2×) ve směru obchodu
  - **Distance:** `TRAIL_ATR_MULT × ATR` (výchozí 1× ATR) od nejvyšší/nejnižší dosažené ceny
  - Po aktivaci se trailing stop posouvá za cenou, nikdy nazpět

### Time Exit

- Pozice se automaticky zavře po `TIME_EXIT_MINUTES` minutách (výchozí 720 = 12h)
- Brání držení ztrátových pozic příliš dlouho

### Risk Management

- **Daily Stop:** Pokud denní ztráta překročí `DAILY_STOP` (výchozí 2%), trading se zastaví
- **No Flip:** Pozice musí být zavřena (SL/TP/trailing/time) před otevřením nové
- **Profit Split:** 50% zisku se reinvestuje, 50% jde do cash bufferu

### Execution Model

| Parametr | Výchozí | Popis |
|----------|---------|-------|
| `FEE_RATE` | 0.08% | Poplatek za stranu (entry + exit) |
| `SPREAD_BPS` | 2.0 bps | Spread model (split na obě strany) |
| `SLIPPAGE_BPS_BASE` | 1.5 bps | Minimální slippage |
| `SLIPPAGE_ATR_MULT` | 50.0 | Dynamický slippage přídavek |
| `SLIPPAGE_BPS_CAP` | 25.0 bps | Max slippage |

## Backtesting (backtest.py)

### Zdroje dat

| Zdroj | Popis | Limit |
|-------|-------|-------|
| `binance` | Binance REST API, 1000 svíček/batch | Mnoho historie |
| `kraken` | Kraken REST API, 720 svíček/batch | ~2.5 dní M5 dat |
| `mongo` | MongoDB `market_candles` | Data ze sběru nebo předchozích backtestů |

### Výstupní metriky

- **Total trades** — Celkový počet obchodů
- **Win rate** — Procento ziskových obchodů
- **Total PnL** — Celkový zisk/ztráta
- **Max drawdown** — Maximální pokles equity
- **Profit factor** — Gross profit / Gross loss
- **Average win/loss** — Průměrný zisk/ztráta na obchod
- **Final equity** — Konečná equity

### Příklady

```bash
# H1 backtest BTC na Binance
python -m trading.backtest --source binance --symbol BTC/USDT --from 2026-02-01 --to 2026-02-15

# M5 backtest s uložením do MongoDB
python -m trading.backtest --source binance --symbol BTC/USDT --from 2026-02-01 --to 2026-02-15 --interval 5 --save-to-mongo

# Rerun z MongoDB (rychlé)
python -m trading.backtest --source mongo --symbol BTC/USDT --from 2026-02-01 --to 2026-02-15
```
