# Konfigurace

Všechny parametry jsou konfigurovatelné přes environment variables (Pydantic BaseSettings) a za runtime přes dashboard (`/config` stránka).

## Parametry

### Strategy — Parametry strategie

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `BREAKOUT_N` | 10 | int | Počet svíček pro breakout lookback — hledá N-candle high/low |
| `EMA_PERIOD` | 50 | int | Perioda EMA pro trend filtr (signály pouze ve směru trendu) |
| `VOL_FILTER` | True | bool | Volume confirmation — breakout musí být podpořen nadprůměrným objemem |
| `VOL_MULT` | 1.5 | float | Minimální objem svíčky = VOL_MULT × průměrný objem |
| `COOLDOWN_CANDLES` | 2 | int | Pauza po zavření pozice v počtu svíček (2 × H1 = 2h) |

### Risk — Řízení rizika

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `RISK_PER_TRADE` | 0.005 | float | Risk na obchod jako podíl equity (0.005 = 0.5%) |
| `DAILY_STOP` | 0.02 | float | Denní stop-loss limit / kill switch (0.02 = 2%) |
| `PROFIT_SPLIT_REINVEST` | 0.5 | float | Podíl zisku reinvestovaný (0.5 = 50%, zbytek do bufferu) |
| `ALLOC_PCT` | 0.10 | float | Procento equity alokované na pozici v paper mode |
| `MIN_USD_ORDER` | 10.0 | float | Minimální velikost objednávky v USDT |

### Exits — Výstupní pravidla

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `SL_ATR_MULT` | 1.5 | float | Stop loss = SL_ATR_MULT × ATR od vstupu |
| `TP_ATR_MULT` | 4.0 | float | Take profit = TP_ATR_MULT × ATR (R:R poměr) |
| `TIME_EXIT_MINUTES` | 720 | float | Časový exit v minutách (720 = 12h) |
| `TRAILING_STOP` | True | bool | Zapnout/vypnout trailing stop |
| `TRAIL_ATR_MULT` | 1.0 | float | Trailing distance = TRAIL_ATR_MULT × ATR |
| `TRAIL_ACTIVATION_ATR` | 2.0 | float | Trailing stop se aktivuje po pohybu ≥ N × ATR |

### Execution — Model exekuce

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `FEE_RATE` | 0.0008 | float | Poplatek za stranu (0.08%, aplikuje se na entry i exit) |
| `SPREAD_BPS` | 2.0 | float | Spread model v basis points (split na obě strany) |
| `SLIPPAGE_BPS_BASE` | 1.5 | float | Minimální slippage v basis points |
| `SLIPPAGE_ATR_MULT` | 50.0 | float | Dynamický přídavek slippage = (ATR/price) × multiplikátor |
| `SLIPPAGE_BPS_CAP` | 25.0 | float | Maximální slippage v basis points |
| `ATR_PERIOD` | 14 | int | Perioda ATR pro výpočet slippage a position sizingu |

### Market — Tržní nastavení

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `SYMBOLS` | BTC/USDT,ETH/USDT | str | Kraken trading páry (čárkou oddělené) |
| `BINANCE_SYMBOLS` | PAXG/USDT,SOL/USDT | str | Binance symboly pro sběr dat |
| `INTERVAL_MINUTES` | 60 | int | Interval svíček v minutách (60=H1, 5=M5, 15=M15) |
| `MODE` | paper | str | Režim: paper (simulace) nebo live (reálné obchody) |

### Sentiment — Sentiment filtr

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `SENTIMENT_ENABLED` | True | bool | Zapnout/vypnout sentiment filtr |
| `SENTIMENT_WINDOW_MINUTES` | 60 | int | Okno pro hledání sentimentu (minuty zpět) |
| `SENTIMENT_MIN_ARTICLES` | 1 | int | Minimum článků potřebných pro rozhodnutí |
| `SENTIMENT_NO_DATA_ACTION` | pass | str | "pass" = signál projde, "block" = zablokuje |

### Intel — Market Intelligence filtr

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `INTEL_ENABLED` | False | bool | Zapnout/vypnout market intelligence filtr |
| `INTEL_MAX_AGE_MINUTES` | 120 | int | Ignorovat intel starší než N minut |
| `INTEL_BLOCK_LOW_CONF` | False | bool | Blokovat obchody kde LLM confidence je LOW |

### Interní / DB

| Parametr | Výchozí | Typ | Popis |
|----------|---------|-----|-------|
| `MONGO_URI` | mongodb://127.0.0.1:27017 | str | MongoDB connection string |
| `MONGO_DB` | aiinvest | str | Název databáze |
| `KRAKEN_WS_URL` | wss://ws.kraken.com/v2 | str | Kraken WebSocket URL |
| `BINANCE_WS_URL` | wss://stream.binance.com:9443 | str | Binance WebSocket URL |

## Nastavení přes Environment Variables

Každý parametr lze nastavit jako environment variable se stejným názvem:

```bash
# Příklady
set BREAKOUT_N=15
set EMA_PERIOD=100
set SENTIMENT_ENABLED=false
set DAILY_STOP=0.03
```

Pydantic automaticky parsuje hodnoty na správný typ.

## Runtime změny přes Dashboard

Na stránce `/config` v dashboardu lze měnit parametry za běhu:
1. Změňte hodnotu v příslušném poli
2. Klikněte "Save Changes"
3. Parametry se okamžitě aplikují na běžící engine

**Omezení:** `KRAKEN_API_KEY` a `KRAKEN_API_SECRET` nelze měnit přes API (bezpečnost).

## Doporučené rozsahy

| Parametr | Min | Max | Poznámka |
|----------|-----|-----|----------|
| `BREAKOUT_N` | 5 | 30 | Nižší = více signálů, vyšší = méně falešných |
| `EMA_PERIOD` | 20 | 200 | 50 je standardní |
| `VOL_MULT` | 1.0 | 3.0 | 1.5 je rozumný kompromis |
| `SL_ATR_MULT` | 1.0 | 3.0 | Nižší = těsnější SL, více SL hitů |
| `TP_ATR_MULT` | 2.0 | 8.0 | Vyšší = větší zisky, méně hitů |
| `RISK_PER_TRADE` | 0.001 | 0.02 | Konzervativní: 0.5%, agresivní: 2% |
| `DAILY_STOP` | 0.01 | 0.05 | 2% je rozumný kill switch |
