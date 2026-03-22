# Architektura systému

## Přehled

AIInvest je AI-powered kryptoměnový trading bot skládající se z pěti hlavních komponent:

```
┌─────────────────┐     ┌──────────────────┐     ┌──────────────┐
│  Web Dashboard   │────▶│  FastAPI Server   │────▶│   MongoDB    │
│  (React/Vite)    │◀────│  (python-core)    │◀────│   (lokální)  │
│  port 5173       │     │  port 8000        │     │  port 27017  │
└─────────────────┘     └──────────────────┘     └──────────────┘
                              │      ▲
                              │      │
                    ┌─────────┘      └─────────┐
                    ▼                          ▼
          ┌──────────────────┐     ┌──────────────────┐
          │  Kraken/Binance  │     │  llama.cpp       │
          │  WebSocket       │     │  (Mistral 7B)    │
          │  (tržní data)    │     │  (sentiment/intel)│
          └──────────────────┘     └──────────────────┘
```

## Komponenty

### 1. FastAPI Server (`python-core/app.py`)

Hlavní API server. Obsluhuje:
- REST endpointy pro dashboard (portfolio, pozice, grafy, config)
- Bot control (start/stop/status)
- Sentiment analýzu (LLM one-word classification)
- Novinky (RSS feed data)
- Backtest runner

### 2. Trading Engine (`python-core/trading/`)

| Soubor | Účel |
|--------|------|
| `engine.py` | Generování signálů: breakout + EMA + volume + sentiment + intel filtry |
| `paper.py` | Paper trading executor s ATR sizingem, SL/TP, trailing stopem |
| `backtest.py` | Backtesting engine s Kraken/Binance paginated fetch a MongoDB cache |
| `kraken_ws.py` | Async WebSocket klient pro Kraken OHLC data |
| `binance_ws.py` | Async WebSocket klient pro Binance kline data |
| `api.py` | REST endpointy pro bot control a dashboard data |
| `config.py` | Pydantic BaseSettings (konfigurace přes env vars) |
| `mongo.py` | MongoDB připojení, indexy, sentiment/intel helpery |

### 3. Workers (běží samostatně)

| Worker | Soubor | Popis |
|--------|--------|-------|
| Data Collector | `data_collector.py` | 24/7 sběr H1 svíček z Kraken + Binance do MongoDB |
| News Worker | `news_worker.py` | RSS feeds → LLM sentiment classification → MongoDB |
| Market Intel | `market_intel_worker.py` | CoinGecko + Fear & Greed → LLM analýza → MongoDB |

### 4. LLM Inference (`llama_wrapper.py`)

Wrapper pro lokální llama.cpp (Mistral 7B Q4_K_M):
- `run_llama_oneword()` — Jednosložkový sentiment (Positive/Neutral/Negative), 4 tokeny
- `run_llama_structured()` — Strukturovaná market analýza, 150 tokenů

### 5. Web Dashboard (`dashboard/`)

React 18 + Vite + TypeScript + Tailwind CSS:
- Lightweight Charts (TradingView) pro svíčkové grafy
- Recharts pro equity křivky
- Polling-based real-time updates (5-10s interval)

## Data Flow

```
1. Market Data:
   Kraken/Binance WS ──▶ kraken_ws.py / binance_ws.py ──▶ H1 OHLC svíčky ──▶ engine.py

2. Data Collection (24/7):
   data_collector.py ──▶ KrakenWS + BinanceWS ──▶ MongoDB [market_candles]

3. Trading Signals:
   Engine (300-candle buffer) ──▶ Breakout(N=10) + EMA(50) + Volume + Cooldown
   ──▶ Sentiment filtr ──▶ Intel filtr ──▶ PaperExecutor (ATR SL/TP + trailing)

4. Sentiment Pipeline:
   RSS feeds ──▶ news_worker.py ──▶ llama_wrapper (one-word) ──▶ MongoDB [sentiments]

5. Market Intelligence:
   CoinGecko + Fear & Greed ──▶ market_intel_worker.py ──▶ llama_wrapper (structured)
   ──▶ MongoDB [market_intel]

6. API Layer:
   FastAPI ──▶ dashboard data, bot control, sentiment queries, backtest

7. Web Dashboard:
   React (port 5173) ──proxy──▶ FastAPI (port 8000) ──▶ MongoDB
```

## MongoDB Kolekce

| Kolekce | Popis | Klíčové pole |
|---------|-------|-------------|
| `market_candles` | OHLCV svíčky (H1) | `symbol`, `tf`, `t`, `o`, `h`, `l`, `c`, `v` |
| `news` | RSS novinky | `title`, `url`, `published_at` |
| `sentiments` | Sentiment klasifikace | `symbols`, `sentiment`, `created_at`, `news_id` |
| `market_intel` | LLM market analýza | `overall`, `assets`, `created_at` |
| `positions` | Otevřené/uzavřené pozice | `run_id`, `symbol`, `side`, `status`, `entry_price`, `exit_price`, `pnl` |
| `portfolio` | Equity snapshot | `run_id`, `equity`, `cash_buffer` |
| `bot_events` | Bot log (start/stop/error) | `run_id`, `t`, `level`, `msg` |
| `bot_signals` | Signal log (executed/blocked) | `run_id`, `t`, `symbol`, `side`, `action`, `reason` |
| `trades` | Trade log | `run_id`, `t_exit` |
| `equity` | Equity snapshoty | `run_id`, `t` |

### Indexy (definované v `trading/mongo.py`)

```python
market_candles: (symbol, tf, t) UNIQUE
bot_events:     (run_id, t)
equity:         (run_id, t)
trades:         (run_id, t_exit)
sentiments:     (symbols, created_at)
market_intel:   (created_at)
bot_signals:    (run_id, t)
```

## API Endpointy

### Bot Control (`/bot`)

| Method | Path | Popis |
|--------|------|-------|
| POST | `/bot/start` | Spustit trading bot |
| POST | `/bot/stop` | Zastavit trading bot |
| GET | `/bot/status` | Stav bota (running, run_id) |
| POST | `/bot/backtest` | Spustit backtest |

### Dashboard Data (`/bot`)

| Method | Path | Popis |
|--------|------|-------|
| GET | `/bot/portfolio` | Equity, cash buffer, daily PnL |
| GET | `/bot/positions/open` | Otevřené pozice s unrealized PnL |
| GET | `/bot/positions/closed` | Uzavřené pozice (trade history) |
| GET | `/bot/equity-curve` | Equity křivka z uzavřených pozic |
| GET | `/bot/events` | Bot eventy (start, stop, errors) |
| GET | `/bot/signals` | Signal log (executed/blocked) |
| GET | `/bot/runs` | Seznam všech run_id s metadaty |
| GET | `/bot/config` | Aktuální konfigurace |
| PUT | `/bot/config` | Runtime update parametrů |

### Market Data (`/market`)

| Method | Path | Popis |
|--------|------|-------|
| GET | `/market/candles` | OHLCV data z MongoDB |

### Sentiment (`/sentiment`)

| Method | Path | Popis |
|--------|------|-------|
| GET | `/sentiment/recent` | Poslední sentimenty pro symbol |
| GET | `/sentiment/summary` | Agregace positive/neutral/negative |
| GET | `/sentiment/intel` | Poslední market intel |

### Legacy Endpointy

| Method | Path | Popis |
|--------|------|-------|
| GET | `/health` | Health check |
| POST | `/sentiment` | Ad-hoc sentiment analýza textu |
| GET | `/news/latest` | Poslední novinky se sentimentem |
