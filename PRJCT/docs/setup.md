# Instalace a spuštění

## Prerekvizity

| Software | Verze | Poznámka |
|----------|-------|----------|
| Python | 3.10+ | S async/await podporou |
| Node.js | 18+ LTS | Pro web dashboard |
| MongoDB | 6.0 | Lokální instance v `MongoDB/` |
| llama.cpp | — | Windows binárky v `llama/` |
| Mistral 7B | Q4_K_M | GGUF model v `models/` |

## Instalace závislostí

### Python (backend)

```bash
cd python-core
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Hlavní závislosti: `fastapi`, `uvicorn`, `pymongo`, `pydantic-settings`, `websockets`, `aiohttp`, `feedparser`

### Node.js (dashboard)

```bash
cd dashboard
npm install
```

Hlavní závislosti: `react`, `react-router-dom`, `lightweight-charts`, `recharts`, `tailwindcss`

## Spuštění služeb

### 1. MongoDB (musí běžet jako první)

```bash
MongoDB\server\6.0\bin\mongod --dbpath MongoDB\data --logpath MongoDB\log\mongod.log
```

### 2. FastAPI Server

```bash
cd python-core
venv\Scripts\activate
uvicorn app:app --reload
```

Server běží na `http://localhost:8000`.

### 3. Web Dashboard

```bash
cd dashboard
npm run dev
```

Dashboard běží na `http://localhost:5173`. API requesty jsou proxied na FastAPI server.

### 4. Workers (volitelné, běží na pozadí)

```bash
# Data Collector — 24/7 sběr H1 svíček z Kraken + Binance
python python-core/data_collector.py

# News Worker — RSS sentiment pipeline
python python-core/news_worker.py

# Market Intel Worker — hodinová LLM analýza trhu
python python-core/market_intel_worker.py
python python-core/market_intel_worker.py --once  # jednorázový run
```

## VS Code Tasks

Projekt obsahuje předdefinované VS Code úlohy v `.vscode/tasks.json`:

| Task | Popis |
|------|-------|
| Data Collector (Kraken + Binance) | Spustí data_collector.py |
| News Worker | Spustí news_worker.py |
| Market Intel Worker | Spustí market_intel_worker.py |
| FastAPI Server | Spustí uvicorn s --reload |
| Dashboard (Vite Dev) | Spustí npm run dev v dashboard/ |

Spuštění: `Ctrl+Shift+P` → `Tasks: Run Task` → vybrat úlohu.

## Backtesting

```bash
cd python-core
venv\Scripts\activate

# Backtest z Binance API (H1 výchozí interval)
python -m trading.backtest --source binance --symbol BTC/USDT --from 2026-02-01 --to 2026-02-15

# Backtest z Binance + uložení do MongoDB pro rychlejší reruns
python -m trading.backtest --source binance --symbol SOL/USDT --from 2026-02-01 --to 2026-02-15 --save-to-mongo

# Backtest z MongoDB (rychlé, pokud data už jsou uložená)
python -m trading.backtest --source mongo --symbol BTC/USDT --from 2026-02-01 --to 2026-02-15

# Vlastní interval (M5, M15)
python -m trading.backtest --source binance --symbol BTC/USDT --from 2026-02-01 --to 2026-02-15 --interval 5
```

Backtest lze také spustit z dashboardu na stránce `/backtest`.

## Struktura adresářů

```
c:\aiinvest\
├── python-core/          # Python backend (FastAPI + trading engine)
│   ├── venv/             # Python virtualenv
│   ├── app.py            # FastAPI entry point
│   ├── trading/          # Trading bot modul
│   ├── news_worker.py    # RSS sentiment worker
│   ├── data_collector.py # WebSocket → MongoDB collector
│   ├── market_intel_worker.py  # LLM market analysis
│   └── llama_wrapper.py  # llama.cpp wrapper
├── dashboard/            # React web dashboard
│   ├── src/
│   │   ├── pages/        # Stránky (6 routes)
│   │   ├── components/   # UI komponenty
│   │   ├── api/          # API klient
│   │   ├── hooks/        # Custom hooks
│   │   └── types/        # TypeScript typy
│   └── package.json
├── llama/                # llama.cpp binárky
├── models/               # GGUF model weights
├── MongoDB/              # Lokální MongoDB server + data
├── docs/                 # Dokumentace (tento adresář)
└── CLAUDE.md             # AI assistant instrukce
```
