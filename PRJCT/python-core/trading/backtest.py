# trading/backtest.py
"""
Backtesting engine — přehraje historické svíčky přes TradingEngine.

Použití:
    python -m trading.backtest --source kraken --symbol BTC/USDT --from 2025-01-01 --to 2025-06-01
    python -m trading.backtest --source mongo --symbol BTC/USDT --with-sentiment
"""
import argparse
import asyncio
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional

import requests

from trading.config import settings
from trading.engine import TradingEngine
from trading.fees import get_fee_rate_per_side
from trading.mongo import get_db


# Kraken REST mapování symbolů
KRAKEN_PAIR_MAP = {
    "BTC/USDT": "XBTUSDT",
    "ETH/USDT": "ETHUSDT",
}

# Kraken interval mapování (minuty)
KRAKEN_INTERVAL_MAP = {
    1: 1, 5: 5, 15: 15, 30: 30, 60: 60, 240: 240, 1440: 1440,
}

# Binance REST mapování symbolů
BINANCE_PAIR_MAP = {
    "PAXG/USDT": "PAXGUSDT",
    "SOL/USDT": "SOLUSDT",
    "XRP/USDT": "XRPUSDT",
    "DOGE/USDT": "DOGEUSDT",
    "ADA/USDT": "ADAUSDT",
    "AVAX/USDT": "AVAXUSDT",
    "BTC/USDT": "BTCUSDT",
    "ETH/USDT": "ETHUSDT",
}

# Binance interval mapování
BINANCE_INTERVAL_MAP = {
    1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d",
}


@dataclass
class BacktestResult:
    run_id: str = ""
    symbol: str = ""
    source: str = ""
    total_candles: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    final_equity: float = 0.0

    def summary(self) -> str:
        lines = [
            f"{'='*50}",
            f"BACKTEST VÝSLEDKY — {self.run_id}",
            f"{'='*50}",
            f"Symbol:         {self.symbol}",
            f"Zdroj:          {self.source}",
            f"Svíček:         {self.total_candles}",
            f"Obchodů:        {self.total_trades}",
            f"Win rate:       {self.win_rate:.1%}",
            f"Total PnL:      {self.total_pnl:.2f} USDT",
            f"Max drawdown:   {self.max_drawdown:.2f} USDT",
            f"Profit factor:  {self.profit_factor:.2f}",
            f"Avg win:        {self.avg_win:.2f} USDT",
            f"Avg loss:       {self.avg_loss:.2f} USDT",
            f"Final equity:   {self.final_equity:.2f} USDT",
            f"{'='*50}",
        ]
        return "\n".join(lines)


def _fetch_kraken_ohlc(pair: str, interval: int, since: int) -> tuple:
    """Stáhne OHLC data z Kraken REST API. Vrací (candles, last_timestamp)."""
    url = "https://api.kraken.com/0/public/OHLC"
    params = {"pair": pair, "interval": interval, "since": since}

    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()

    if data.get("error"):
        raise RuntimeError(f"Kraken API error: {data['error']}")

    result = data.get("result", {})
    last = result.pop("last", since)

    # result obsahuje jeden klíč s daty (název páru)
    candles_raw = []
    for key, rows in result.items():
        if key == "last":
            continue
        for row in rows:
            # [time, open, high, low, close, vwap, volume, count]
            candles_raw.append({
                "t": datetime.fromtimestamp(row[0], tz=timezone.utc).isoformat(),
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[6]),
            })

    return candles_raw, int(last)


def fetch_kraken_candles(
    symbol: str, interval: int, dt_from: datetime, dt_to: datetime,
    save_to_mongo: bool = False,
) -> List[dict]:
    """Stáhne všechny svíčky z Kraken REST API s explicitní paginací."""
    pair = KRAKEN_PAIR_MAP.get(symbol)
    if not pair:
        raise ValueError(f"Neznámý symbol pro Kraken: {symbol}. Podporované: {list(KRAKEN_PAIR_MAP.keys())}")

    kraken_interval = KRAKEN_INTERVAL_MAP.get(interval, interval)
    since = int(dt_from.timestamp())
    end_ts = int(dt_to.timestamp())

    all_candles = []
    batch_num = 0

    while since < end_ts:
        batch_num += 1
        print(f"  Kraken fetch #{batch_num}: since={datetime.fromtimestamp(since, tz=timezone.utc).isoformat()}")
        candles, _last = _fetch_kraken_ohlc(pair, kraken_interval, since)

        if not candles:
            break

        # Detekce: Kraken vrátil data mimo požadovaný rozsah
        first_ts = datetime.fromisoformat(candles[0]["t"]).timestamp()
        if batch_num == 1 and first_ts > end_ts:
            earliest = datetime.fromtimestamp(first_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            req_from = dt_from.strftime("%Y-%m-%d")
            print(f"\n  WARN: Kraken nemá data pro {symbol} od {req_from}. "
                  f"Nejstarší dostupná: {earliest}.")
            print(f"  Tip: Pro delší historii použij --source mongo s daty uloženými z WebSocket feedu.\n")
            break

        # Filtruj jen svíčky v požadovaném rozmezí
        filtered = []
        for c in candles:
            c_ts = datetime.fromisoformat(c["t"]).timestamp()
            if int(dt_from.timestamp()) <= c_ts < end_ts:
                filtered.append(c)

        all_candles.extend(filtered)

        if not filtered:
            # Všechny svíčky mimo rozsah — konec
            break

        # Explicitní paginace: since = timestamp poslední svíčky + 1
        last_candle_ts = int(datetime.fromisoformat(candles[-1]["t"]).timestamp())
        next_since = last_candle_ts + 1

        if next_since <= since:
            break  # ochrana proti nekonečné smyčce
        since = next_since

        time.sleep(1)  # rate limiting

    # Deduplikace podle timestamp
    seen = set()
    unique = []
    for c in all_candles:
        if c["t"] not in seen:
            seen.add(c["t"])
            unique.append(c)

    unique.sort(key=lambda x: x["t"])
    print(f"  Kraken: celkem {len(unique)} svíček")

    # Uložení do MongoDB
    if save_to_mongo and unique:
        db = get_db()
        saved = 0
        for c in unique:
            doc = {"symbol": symbol, "tf": interval, "t": c["t"],
                   "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
            db.market_candles.update_one(
                {"symbol": symbol, "tf": interval, "t": c["t"]},
                {"$set": doc}, upsert=True,
            )
            saved += 1
        print(f"  MongoDB: uloženo {saved} svíček do market_candles")

    return unique


def fetch_binance_candles(
    symbol: str, interval: int, dt_from: datetime, dt_to: datetime,
    save_to_mongo: bool = False,
) -> List[dict]:
    """Stáhne svíčky z Binance REST API s paginací (max 1000/request)."""
    pair = BINANCE_PAIR_MAP.get(symbol)
    if not pair:
        raise ValueError(f"Neznámý symbol pro Binance: {symbol}. Podporované: {list(BINANCE_PAIR_MAP.keys())}")

    binance_interval = BINANCE_INTERVAL_MAP.get(interval, f"{interval}m")
    start_ms = int(dt_from.timestamp() * 1000)
    end_ms = int(dt_to.timestamp() * 1000)

    all_candles = []
    batch_num = 0

    while start_ms < end_ms:
        batch_num += 1
        print(f"  Binance fetch #{batch_num}: since={datetime.fromtimestamp(start_ms / 1000, tz=timezone.utc).isoformat()}")

        url = "https://api.binance.com/api/v3/klines"
        params = {
            "symbol": pair,
            "interval": binance_interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "limit": 1000,
        }

        r = requests.get(url, params=params, timeout=15)
        r.raise_for_status()
        rows = r.json()

        if not rows:
            break

        for row in rows:
            # [open_time, open, high, low, close, volume, close_time, ...]
            t = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat()
            all_candles.append({
                "t": t,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            })

        # Paginace: start od close_time poslední svíčky + 1
        last_close_time = rows[-1][6]
        next_start = last_close_time + 1
        if next_start <= start_ms:
            break
        start_ms = next_start

        time.sleep(0.5)  # rate limiting

    # Deduplikace
    seen = set()
    unique = []
    for c in all_candles:
        if c["t"] not in seen:
            seen.add(c["t"])
            unique.append(c)

    unique.sort(key=lambda x: x["t"])
    print(f"  Binance: celkem {len(unique)} svíček")

    if save_to_mongo and unique:
        db = get_db()
        saved = 0
        for c in unique:
            doc = {"symbol": symbol, "tf": interval, "t": c["t"],
                   "o": c["o"], "h": c["h"], "l": c["l"], "c": c["c"], "v": c["v"]}
            db.market_candles.update_one(
                {"symbol": symbol, "tf": interval, "t": c["t"]},
                {"$set": doc}, upsert=True,
            )
            saved += 1
        print(f"  MongoDB: uloženo {saved} svíček do market_candles")

    return unique


def fetch_mongo_candles(db, symbol: str, interval: int, dt_from: datetime, dt_to: datetime) -> List[dict]:
    """Načte svíčky z MongoDB kolekce market_candles."""
    t_from = dt_from.isoformat()
    t_to = dt_to.isoformat()

    rows = list(db.market_candles.find(
        {"symbol": symbol, "tf": interval, "t": {"$gte": t_from, "$lt": t_to}},
    ).sort("t", 1))

    candles = [
        {"t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]}
        for r in rows
    ]
    print(f"  MongoDB: celkem {len(candles)} svíček pro {symbol}")
    return candles


class BacktestRunner:
    def __init__(
        self,
        source: str = "mongo",
        symbol: str = "BTC/USDT",
        dt_from: Optional[datetime] = None,
        dt_to: Optional[datetime] = None,
        initial_equity: float = 1000.0,
        interval: int = 5,
        with_sentiment: bool = False,
        save_to_mongo: bool = False,
        mode: str = "exact",
    ):
        self.source = source
        self.symbol = symbol
        self.dt_from = dt_from
        self.dt_to = dt_to or datetime.now(timezone.utc)
        self.initial_equity = max(1.0, float(initial_equity or 1000.0))
        self.interval = interval
        self.with_sentiment = with_sentiment
        self.save_to_mongo = save_to_mongo
        self.mode = (mode or "exact").strip().lower()
        self.run_id = f"bt-{uuid.uuid4().hex[:8]}"

    async def run(self) -> BacktestResult:
        db = get_db()

        # Dočasně přepneme SYMBOLS na backtest symbol
        original_symbols = settings.SYMBOLS
        original_sentiment = settings.SENTIMENT_ENABLED
        settings.SYMBOLS = self.symbol
        settings.SENTIMENT_ENABLED = self.with_sentiment

        try:
            # Načtení svíček
            print(f"\n>>> BACKTEST START: {self.run_id} <<<")
            print(f"    Symbol: {self.symbol}, Zdroj: {self.source}")
            print(f"    Od: {self.dt_from}, Do: {self.dt_to}")
            print(f"    Initial equity: {self.initial_equity:.2f}")
            print(f"    Sentiment: {'ON' if self.with_sentiment else 'OFF'}")
            print(f"    Mode: {self.mode}\n")

            if self.source == "kraken":
                candles = fetch_kraken_candles(
                    self.symbol, self.interval, self.dt_from, self.dt_to,
                    save_to_mongo=self.save_to_mongo,
                )
            elif self.source == "binance":
                candles = fetch_binance_candles(
                    self.symbol, self.interval, self.dt_from, self.dt_to,
                    save_to_mongo=self.save_to_mongo,
                )
            elif self.source == "mongo":
                candles = fetch_mongo_candles(db, self.symbol, self.interval, self.dt_from, self.dt_to)
            else:
                raise ValueError(f"Neznámý zdroj: {self.source}")

            if not candles:
                print("  Žádné svíčky k přehrání!")
                return BacktestResult(run_id=self.run_id, symbol=self.symbol, source=self.source)

            # Fast approximate mode (vectorized indicators + simplified execution loop).
            if self.mode == "vectorized_fast":
                return self._run_vectorized_fast(candles)

            # seed_before = první svíčka backtestu (aby engine naplnil buffer jen z dat před backtest oknem)
            seed_before = candles[0]["t"]
            db.portfolio.update_one(
                {"run_id": self.run_id},
                {"$set": {"run_id": self.run_id, "equity": self.initial_equity, "cash_buffer": 0.0, "initial_equity": self.initial_equity}},
                upsert=True,
            )

            # Škálování time exit podle intervalu (60 min na M5 = 12 svíček → zachovat ~12 svíček)
            original_time_exit = settings.TIME_EXIT_MINUTES
            if self.interval != 5:
                settings.TIME_EXIT_MINUTES = original_time_exit * (self.interval / 5.0)

            # Inicializace engine
            engine = TradingEngine(
                run_id=self.run_id,
                seed_before=seed_before,
                interval=self.interval,
                persist_candles=False,
                persist_runtime_state=False,
                persist_signals=False,
                backtest_historical_time=True,
            )

            # Přehrání svíček
            for i, c in enumerate(candles):
                item = {
                    "symbol": self.symbol,
                    "interval_begin": c["t"],
                    "open": str(c["o"]),
                    "high": str(c["h"]),
                    "low": str(c["l"]),
                    "close": str(c["c"]),
                    "volume": str(c["v"]),
                }
                await engine.on_candle(self.symbol, self.interval, item)

                if (i + 1) % 500 == 0:
                    print(f"  Přehráno {i + 1}/{len(candles)} svíček...")

            print(f"  Přehrávání dokončeno: {len(candles)} svíček")

            # Výpočet metrik
            result = self._compute_metrics(db, len(candles))
            return result

        finally:
            settings.SYMBOLS = original_symbols
            settings.SENTIMENT_ENABLED = original_sentiment
            if self.interval != 5:
                settings.TIME_EXIT_MINUTES = original_time_exit

    def _compute_metrics(self, db, total_candles: int) -> BacktestResult:
        """Spočítá metriky z uzavřených pozic v MongoDB."""
        positions = list(db.positions.find(
            {"run_id": self.run_id, "status": "CLOSED"},
        ))

        portfolio = db.portfolio.find_one({"run_id": self.run_id})
        final_equity = float(portfolio["equity"]) if portfolio else self.initial_equity

        if not positions:
            return BacktestResult(
                run_id=self.run_id,
                symbol=self.symbol,
                source=self.source,
                total_candles=total_candles,
                total_trades=0,
                final_equity=final_equity,
            )

        pnls = [float(p.get("pnl", 0)) for p in positions]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]

        total_trades = len(pnls)
        win_rate = len(wins) / total_trades if total_trades > 0 else 0.0
        total_pnl = sum(pnls)
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf") if gross_profit > 0 else 0.0

        # Max drawdown z equity křivky (kumulativní PnL)
        equity = self.initial_equity
        peak = equity
        max_dd = 0.0
        for pnl in pnls:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        return BacktestResult(
            run_id=self.run_id,
            symbol=self.symbol,
            source=self.source,
            total_candles=total_candles,
            total_trades=total_trades,
            win_rate=win_rate,
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 2),
            profit_factor=round(profit_factor, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            final_equity=round(final_equity, 2),
        )

    def _run_vectorized_fast(self, candles: List[dict]) -> BacktestResult:
        import pandas as pd

        if not candles:
            return BacktestResult(run_id=self.run_id, symbol=self.symbol, source=self.source)

        df = pd.DataFrame(candles)
        for col in ("o", "h", "l", "c", "v"):
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["o", "h", "l", "c", "v"]).reset_index(drop=True)
        if df.empty:
            return BacktestResult(run_id=self.run_id, symbol=self.symbol, source=self.source, total_candles=0)

        n = int(settings.BREAKOUT_N)
        ema_period = int(settings.EMA_PERIOD)
        vol_mult = float(settings.VOL_MULT)
        vol_filter = bool(settings.VOL_FILTER)

        df["ema"] = df["c"].ewm(span=ema_period, adjust=False).mean()
        df["level_high"] = df["h"].rolling(window=n).max().shift(1)
        df["level_low"] = df["l"].rolling(window=n).min().shift(1)
        df["avg_vol"] = df["v"].rolling(window=n).mean().shift(1)
        df["atr"] = (df["h"] - df["l"]).rolling(window=14).mean().fillna((df["h"] - df["l"]).expanding().mean())

        fee_rate = get_fee_rate_per_side(settings, self.symbol)
        spread_bps = float(settings.SPREAD_BPS)
        sl_mult = float(settings.SL_ATR_MULT)
        tp_mult = float(settings.TP_ATR_MULT)
        time_exit_steps = max(1, int(float(settings.TIME_EXIT_MINUTES) / max(1, self.interval)))
        cooldown_steps = max(0, int(settings.COOLDOWN_CANDLES))

        equity = self.initial_equity
        cash_buffer = 0.0
        pnls: List[float] = []
        position = None
        cooldown = 0

        for i, row in df.iterrows():
            if cooldown > 0:
                cooldown -= 1

            close_px = float(row["c"])
            high_px = float(row["h"])
            low_px = float(row["l"])
            atr = max(float(row["atr"]) if pd.notna(row["atr"]) else close_px * 0.01, close_px * 0.001)

            if position is not None:
                age = i - position["entry_i"]
                side = position["side"]
                sl = position["sl"]
                tp = position["tp"]
                exit_now = False
                exit_mid = close_px

                if side == "BUY":
                    if low_px <= sl:
                        exit_now = True
                        exit_mid = sl
                    elif high_px >= tp:
                        exit_now = True
                        exit_mid = tp
                else:
                    if high_px >= sl:
                        exit_now = True
                        exit_mid = sl
                    elif low_px <= tp:
                        exit_now = True
                        exit_mid = tp

                if not exit_now and age >= time_exit_steps:
                    exit_now = True
                    exit_mid = close_px

                if exit_now:
                    spread = (spread_bps / 10000.0) * exit_mid
                    exit_px = exit_mid - spread if side == "BUY" else exit_mid + spread
                    qty = position["qty"]
                    gross = (exit_px - position["entry_px"]) * qty * (1 if side == "BUY" else -1)
                    fee_exit = exit_px * qty * fee_rate
                    net = round(gross - position["fee_entry"] - fee_exit, 2)
                    pnls.append(net)
                    if net > 0:
                        reinvest = net * float(settings.PROFIT_SPLIT_REINVEST)
                        equity += reinvest
                        cash_buffer += (net - reinvest)
                    else:
                        equity += net
                    position = None
                    cooldown = cooldown_steps
                    continue

            if position is not None or cooldown > 0:
                continue

            if pd.isna(row["level_high"]) or pd.isna(row["level_low"]) or pd.isna(row["ema"]):
                continue
            if vol_filter and pd.notna(row["avg_vol"]) and float(row["avg_vol"]) > 0:
                if float(row["v"]) < float(row["avg_vol"]) * vol_mult:
                    continue

            side = None
            if close_px > float(row["level_high"]) and close_px > float(row["ema"]):
                side = "BUY"
            elif close_px < float(row["level_low"]) and close_px < float(row["ema"]):
                side = "SELL"
            if side is None:
                continue

            risk = float(settings.RISK_PER_TRADE)
            qty = (equity * risk) / max(atr * sl_mult, close_px * 0.002)
            if qty * close_px < 10.0:
                qty = 10.0 / close_px

            spread = (spread_bps / 10000.0) * close_px
            entry_px = close_px + spread if side == "BUY" else close_px - spread
            fee_entry = entry_px * qty * fee_rate
            sl = entry_px - atr * sl_mult if side == "BUY" else entry_px + atr * sl_mult
            tp = entry_px + atr * tp_mult if side == "BUY" else entry_px - atr * tp_mult
            position = {
                "side": side,
                "entry_i": i,
                "entry_px": entry_px,
                "qty": qty,
                "fee_entry": fee_entry,
                "sl": sl,
                "tp": tp,
            }

        total_trades = len(pnls)
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_pnl = sum(pnls)
        win_rate = (len(wins) / total_trades) if total_trades else 0.0
        avg_win = sum(wins) / len(wins) if wins else 0.0
        avg_loss = sum(losses) / len(losses) if losses else 0.0
        gp = sum(wins) if wins else 0.0
        gl = abs(sum(losses)) if losses else 0.0
        pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)

        eq = self.initial_equity
        peak = eq
        max_dd = 0.0
        for p in pnls:
            eq += p
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        return BacktestResult(
            run_id=self.run_id,
            symbol=self.symbol,
            source=f"{self.source}:{self.mode}",
            total_candles=int(len(df)),
            total_trades=total_trades,
            win_rate=win_rate,
            total_pnl=round(total_pnl, 2),
            max_drawdown=round(max_dd, 2),
            profit_factor=round(pf, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            final_equity=round(eq, 2),
        )


@dataclass
class MultiBacktestResult:
    """Výsledky multi-symbol backtestu se sdíleným portfoliem."""
    run_id: str = ""
    source: str = ""
    symbols: List[str] = field(default_factory=list)
    per_symbol: dict = field(default_factory=dict)  # symbol → {total_candles, total_trades, win_rate, ...}
    total_candles: int = 0
    total_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    max_drawdown: float = 0.0
    profit_factor: float = 0.0
    avg_win: float = 0.0
    avg_loss: float = 0.0
    final_equity: float = 0.0
    cash_buffer: float = 0.0


class MultiBacktestRunner:
    """Backtest pro více symbolů se sdíleným portfoliem (1000 USDT celkem)."""

    def __init__(
        self,
        source: str = "mongo",
        symbols: List[str] = None,
        dt_from: Optional[datetime] = None,
        dt_to: Optional[datetime] = None,
        initial_equity: float = 1000.0,
        interval: int = 60,
        with_sentiment: bool = False,
        overrides: Optional[dict] = None,
        mode: str = "exact",
    ):
        self.source = source
        self.symbols = symbols or []
        self.dt_from = dt_from
        self.dt_to = dt_to or datetime.now(timezone.utc)
        self.initial_equity = max(1.0, float(initial_equity or 1000.0))
        self.interval = interval
        self.with_sentiment = with_sentiment
        self.overrides = overrides or {}
        self.mode = (mode or "exact").strip().lower()
        self.run_id = f"bt-{uuid.uuid4().hex[:8]}"

    async def run(self) -> MultiBacktestResult:
        db = get_db()

        # Uložit a přepsat settings
        saved = {}
        saved["SYMBOLS"] = settings.SYMBOLS
        saved["SENTIMENT_ENABLED"] = settings.SENTIMENT_ENABLED
        saved["TIME_EXIT_MINUTES"] = settings.TIME_EXIT_MINUTES

        settings.SYMBOLS = ",".join(self.symbols)
        settings.SENTIMENT_ENABLED = self.with_sentiment

        # Aplikuj overrides
        for key, value in self.overrides.items():
            key_upper = key.upper()
            if hasattr(settings, key_upper):
                saved[key_upper] = getattr(settings, key_upper)
                setattr(settings, key_upper, value)

        try:
            print(f"\n>>> MULTI-BACKTEST START: {self.run_id} <<<")
            print(f"    Symbols: {self.symbols}, Zdroj: {self.source}")
            print(f"    Od: {self.dt_from}, Do: {self.dt_to}")
            print(f"    Initial equity: {self.initial_equity:.2f}")
            print(f"    Interval: {self.interval}m, Sentiment: {'ON' if self.with_sentiment else 'OFF'}")
            print(f"    Mode: {self.mode}")
            if self.overrides:
                print(f"    Overrides: {self.overrides}")
            print()

            if self.mode == "vectorized_fast":
                return await self._run_vectorized_fast(db)

            # 1. Stáhni svíčky pro každý symbol
            all_candles = []  # [(symbol, candle_dict), ...]
            candle_counts = {}

            for sym in self.symbols:
                if self.source == "kraken":
                    candles = fetch_kraken_candles(sym, self.interval, self.dt_from, self.dt_to)
                elif self.source == "binance":
                    candles = fetch_binance_candles(sym, self.interval, self.dt_from, self.dt_to)
                elif self.source == "mongo":
                    candles = fetch_mongo_candles(db, sym, self.interval, self.dt_from, self.dt_to)
                else:
                    raise ValueError(f"Neznámý zdroj: {self.source}")

                candle_counts[sym] = len(candles)
                for c in candles:
                    all_candles.append((sym, c))

            if not all_candles:
                print("  Žádné svíčky k přehrání!")
                return MultiBacktestResult(
                    run_id=self.run_id, source=self.source, symbols=self.symbols
                )

            # 2. Seřaď chronologicky
            all_candles.sort(key=lambda x: x[1]["t"])

            # 3. Seed before = první svíčka
            seed_before = all_candles[0][1]["t"]
            db.portfolio.update_one(
                {"run_id": self.run_id},
                {"$set": {"run_id": self.run_id, "equity": self.initial_equity, "cash_buffer": 0.0, "initial_equity": self.initial_equity}},
                upsert=True,
            )

            # Škálování time exit
            if self.interval != 5:
                settings.TIME_EXIT_MINUTES = saved["TIME_EXIT_MINUTES"] * (self.interval / 5.0)

            # 4. Jeden engine, jedno portfolio
            engine = TradingEngine(
                run_id=self.run_id,
                seed_before=seed_before,
                interval=self.interval,
                persist_candles=False,
                persist_runtime_state=False,
                persist_signals=False,
                backtest_historical_time=True,
            )

            # 5. Přehrání svíček
            for i, (sym, c) in enumerate(all_candles):
                item = {
                    "symbol": sym,
                    "interval_begin": c["t"],
                    "open": str(c["o"]),
                    "high": str(c["h"]),
                    "low": str(c["l"]),
                    "close": str(c["c"]),
                    "volume": str(c["v"]),
                }
                await engine.on_candle(sym, self.interval, item)

                if (i + 1) % 1000 == 0:
                    print(f"  Přehráno {i + 1}/{len(all_candles)} svíček...")

            print(f"  Přehrávání dokončeno: {len(all_candles)} svíček ({len(self.symbols)} symbolů)")

            # 6. Výpočet metrik
            return self._compute_metrics(db, candle_counts)

        finally:
            for key, value in saved.items():
                setattr(settings, key, value)

    async def _run_vectorized_fast(self, db) -> MultiBacktestResult:
        per_symbol = {}
        total_candles = 0
        total_trades = 0
        pnls_all: List[float] = []
        final_equity = self.initial_equity
        total_buffer = 0.0
        total_wins = 0
        total_losses = 0
        gross_profit = 0.0
        gross_loss = 0.0

        for sym in self.symbols:
            if self.source == "kraken":
                candles = fetch_kraken_candles(sym, self.interval, self.dt_from, self.dt_to)
            elif self.source == "binance":
                candles = fetch_binance_candles(sym, self.interval, self.dt_from, self.dt_to)
            else:
                candles = fetch_mongo_candles(db, sym, self.interval, self.dt_from, self.dt_to)

            runner = BacktestRunner(
                source=self.source,
                symbol=sym,
                dt_from=self.dt_from,
                dt_to=self.dt_to,
                initial_equity=self.initial_equity,
                interval=self.interval,
                with_sentiment=False,
                mode="vectorized_fast",
            )
            r = runner._run_vectorized_fast(candles)
            per_symbol[sym] = {
                "total_candles": r.total_candles,
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "total_pnl": r.total_pnl,
                "avg_win": r.avg_win,
                "avg_loss": r.avg_loss,
                "profit_factor": r.profit_factor,
            }
            total_candles += r.total_candles
            total_trades += r.total_trades
            # Approximate aggregation across symbols (fast mode).
            sym_pnl = float(r.total_pnl)
            pnls_all.append(sym_pnl)
            wins_i = int(round(float(r.win_rate) * int(r.total_trades))) if int(r.total_trades) > 0 else 0
            wins_i = max(0, min(int(r.total_trades), wins_i))
            losses_i = int(r.total_trades) - wins_i
            total_wins += wins_i
            total_losses += losses_i
            gross_profit += float(r.avg_win) * wins_i if wins_i > 0 else 0.0
            gross_loss += abs(float(r.avg_loss)) * losses_i if losses_i > 0 else 0.0
            if sym_pnl > 0:
                reinvest = sym_pnl * float(settings.PROFIT_SPLIT_REINVEST)
                final_equity += reinvest
                total_buffer += (sym_pnl - reinvest)
            else:
                final_equity += sym_pnl

        pf = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
        win_rate = (total_wins / total_trades) if total_trades > 0 else 0.0
        avg_win = (gross_profit / total_wins) if total_wins > 0 else 0.0
        avg_loss = -(gross_loss / total_losses) if total_losses > 0 else 0.0

        eq = self.initial_equity
        peak = eq
        max_dd = 0.0
        for p in pnls_all:
            eq += p
            if eq > peak:
                peak = eq
            dd = peak - eq
            if dd > max_dd:
                max_dd = dd

        return MultiBacktestResult(
            run_id=self.run_id,
            source=f"{self.source}:{self.mode}",
            symbols=self.symbols,
            per_symbol=per_symbol,
            total_candles=total_candles,
            total_trades=total_trades,
            win_rate=win_rate,
            total_pnl=round(sum(pnls_all), 2),
            max_drawdown=round(max_dd, 2),
            profit_factor=round(pf, 2),
            avg_win=round(avg_win, 2),
            avg_loss=round(avg_loss, 2),
            final_equity=round(final_equity, 2),
            cash_buffer=round(total_buffer, 2),
        )

    def _compute_metrics(self, db, candle_counts: dict) -> MultiBacktestResult:
        """Spočítá celkové + per-symbol metriky."""
        all_positions = list(db.positions.find(
            {"run_id": self.run_id, "status": "CLOSED"},
        ).sort("exit_time", 1))

        portfolio = db.portfolio.find_one({"run_id": self.run_id})
        final_equity = float(portfolio["equity"]) if portfolio else self.initial_equity
        cash_buffer = float(portfolio.get("cash_buffer", 0.0)) if portfolio else 0.0

        # Per-symbol metriky
        per_symbol = {}
        for sym in self.symbols:
            sym_positions = [p for p in all_positions if p.get("symbol") == sym]
            sym_pnls = [float(p.get("pnl", 0)) for p in sym_positions]
            sym_wins = [p for p in sym_pnls if p > 0]
            sym_losses = [p for p in sym_pnls if p < 0]
            sym_trades = len(sym_pnls)

            gross_p = sum(sym_wins) if sym_wins else 0.0
            gross_l = abs(sum(sym_losses)) if sym_losses else 0.0

            per_symbol[sym] = {
                "total_candles": candle_counts.get(sym, 0),
                "total_trades": sym_trades,
                "win_rate": len(sym_wins) / sym_trades if sym_trades > 0 else 0.0,
                "total_pnl": round(sum(sym_pnls), 2),
                "avg_win": round(sum(sym_wins) / len(sym_wins), 2) if sym_wins else 0.0,
                "avg_loss": round(sum(sym_losses) / len(sym_losses), 2) if sym_losses else 0.0,
                "profit_factor": round(gross_p / gross_l, 2) if gross_l > 0 else (float("inf") if gross_p > 0 else 0.0),
            }

        # Celkové metriky
        pnls = [float(p.get("pnl", 0)) for p in all_positions]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p < 0]
        total_trades = len(pnls)

        gross_profit = sum(wins) if wins else 0.0
        gross_loss = abs(sum(losses)) if losses else 0.0

        # Max drawdown
        equity = self.initial_equity
        peak = equity
        max_dd = 0.0
        for pnl in pnls:
            equity += pnl
            if equity > peak:
                peak = equity
            dd = peak - equity
            if dd > max_dd:
                max_dd = dd

        return MultiBacktestResult(
            run_id=self.run_id,
            source=self.source,
            symbols=self.symbols,
            per_symbol=per_symbol,
            total_candles=sum(candle_counts.values()),
            total_trades=total_trades,
            win_rate=len(wins) / total_trades if total_trades > 0 else 0.0,
            total_pnl=round(sum(pnls), 2),
            max_drawdown=round(max_dd, 2),
            profit_factor=round(gross_profit / gross_loss, 2) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0),
            avg_win=round(sum(wins) / len(wins), 2) if wins else 0.0,
            avg_loss=round(sum(losses) / len(losses), 2) if losses else 0.0,
            final_equity=round(final_equity, 2),
            cash_buffer=round(cash_buffer, 2),
        )


def parse_args():
    parser = argparse.ArgumentParser(description="AIInvest Backtesting Engine")
    parser.add_argument("--source", choices=["mongo", "kraken", "binance"], default="mongo", help="Zdroj dat")
    parser.add_argument("--symbol", default="BTC/USDT", help="Trading pár (např. BTC/USDT)")
    parser.add_argument("--from", dest="dt_from", required=True, help="Začátek (YYYY-MM-DD)")
    parser.add_argument("--to", dest="dt_to", default=None, help="Konec (YYYY-MM-DD, default: now)")
    parser.add_argument("--interval", type=int, default=5, help="Interval v minutách (default: 5)")
    parser.add_argument("--with-sentiment", action="store_true", help="Zapnout sentiment filtr")
    parser.add_argument("--save-to-mongo", action="store_true", help="Uložit stažené svíčky do MongoDB pro pozdější použití s --source mongo")
    return parser.parse_args()


def main():
    args = parse_args()

    dt_from = datetime.strptime(args.dt_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    dt_to = (
        datetime.strptime(args.dt_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if args.dt_to
        else datetime.now(timezone.utc)
    )

    runner = BacktestRunner(
        source=args.source,
        symbol=args.symbol,
        dt_from=dt_from,
        dt_to=dt_to,
        interval=args.interval,
        with_sentiment=args.with_sentiment,
        save_to_mongo=args.save_to_mongo,
    )

    result = asyncio.run(runner.run())
    print(result.summary())


if __name__ == "__main__":
    main()
