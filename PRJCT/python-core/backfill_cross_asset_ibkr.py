from __future__ import annotations

import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Iterable, List

import requests
from ib_insync import Contract, ContFuture, Forex, Future, IB, Index, util

from trading.config import settings
from trading.ibkr_connection import connect_ibkr_with_fallback, normalize_gateway_trading_mode
from trading.mongo import get_db


def _parse_iso_utc(src: str) -> datetime:
    s = str(src or "").strip()
    if not s:
        raise ValueError("empty iso datetime")
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_list(src: str) -> List[str]:
    return [x.strip().upper() for x in str(src or "").split(",") if x.strip()]


def _default_symbols() -> List[str]:
    return sorted(
        set(_parse_list(settings.CROSS_ASSET_FX_SYMBOLS))
        | set(_parse_list(settings.CROSS_ASSET_COMMODITY_SYMBOLS))
        | set(_parse_list(settings.CROSS_ASSET_INDEX_SYMBOLS))
    )


YF_MAP = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "XAUUSD": "GC=F",
    "XAGUSD": "SI=F",
    "CL": "CL=F",
    "WTI": "CL=F",
    "BRENT": "BZ=F",
    "SPX": "^GSPC",
    "NDX": "^NDX",
    "DAX": "^GDAXI",
    "FTSE": "^FTSE",
}


def _bucket(symbol: str) -> str:
    fx = set(_parse_list(settings.CROSS_ASSET_FX_SYMBOLS))
    cmd = set(_parse_list(settings.CROSS_ASSET_COMMODITY_SYMBOLS))
    idx = set(_parse_list(settings.CROSS_ASSET_INDEX_SYMBOLS))
    s = symbol.upper()
    if s in fx:
        return "fx"
    if s in cmd:
        return "commodity"
    if s in idx:
        return "index"
    return "other"


def _contract_candidates(symbol: str):
    s = symbol.upper()
    if s in {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD"}:
        return [Forex(s)]
    if s in {"CL", "WTI"}:
        return [ContFuture("CL", "NYMEX")]
    if s == "BRENT":
        return [ContFuture("BZ", "ICEEU")]
    if s == "XAUUSD":
        return [
            "MATCH:XAUUSD",
        ]
    if s == "XAGUSD":
        return [
            "MATCH:XAGUSD",
        ]
    if s == "SPX":
        return [Index("SPX", "CBOE", "USD")]
    if s == "NDX":
        return [Index("NDX", "NASDAQ", "USD")]
    if s == "DAX":
        return [Index("DAX", "EUREX", "EUR")]
    if s == "FTSE":
        return [Index("Z", "ICEEU", "GBP")]
    return []


def _qualify_one(ib: IB, symbol: str):
    for c in _contract_candidates(symbol):
        try:
            if isinstance(c, str) and c.startswith("MATCH:"):
                needle = c.split(":", 1)[1]
                matches = ib.reqMatchingSymbols(needle)
                if matches:
                    q = ib.qualifyContracts(matches[0].contract)
                    if q:
                        return q[0]
                continue
            qualified = ib.qualifyContracts(c)
            if qualified:
                resolved = qualified[0]
                sec_type = str(getattr(resolved, "secType", "") or "").upper()
                if sec_type == "CONTFUT":
                    contract_month = str(getattr(resolved, "lastTradeDateOrContractMonth", "") or "").strip()
                    contract_month = contract_month[:6] if len(contract_month) >= 6 else contract_month
                    if contract_month:
                        fut = Future(
                            symbol=str(getattr(resolved, "symbol", "") or ""),
                            lastTradeDateOrContractMonth=contract_month,
                            exchange=str(getattr(resolved, "exchange", "") or ""),
                            currency=str(getattr(resolved, "currency", "") or "USD"),
                        )
                        multiplier = str(getattr(resolved, "multiplier", "") or "").strip()
                        if multiplier:
                            fut.multiplier = multiplier
                        fut_qualified = ib.qualifyContracts(fut)
                        if fut_qualified:
                            return fut_qualified[0]
                return resolved
        except Exception:
            continue
    return None


def _what_to_show(contract) -> str:
    sec = str(getattr(contract, "secType", "") or "").upper()
    symbol = str(getattr(contract, "symbol", "") or "").upper()
    if sec == "CASH":
        return "MIDPOINT"
    if sec == "CMDTY":
        return "MIDPOINT"
    if sec == "IND":
        return "TRADES"
    if sec == "CONTFUT":
        return "TRADES"
    if symbol in {"XAUUSD", "XAGUSD"}:
        return "MIDPOINT"
    return "TRADES"


def _bar_rows(ib: IB, contract, end_dt: datetime, duration: str = "30 D", bar_size: str = "1 hour"):
    bars = ib.reqHistoricalData(
        contract,
        endDateTime=end_dt,
        durationStr=duration,
        barSizeSetting=bar_size,
        whatToShow=_what_to_show(contract),
        useRTH=False,
        formatDate=2,
        keepUpToDate=False,
    )
    return list(bars or [])


def _fetch_yahoo_rows(symbol: str, start_dt: datetime, end_dt: datetime):
    ticker = YF_MAP.get(symbol.upper())
    if not ticker:
        raise ValueError(f"missing yahoo mapping for {symbol}")
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1": str(int(start_dt.timestamp())),
        "period2": str(int(end_dt.timestamp())),
        "interval": "60m",
        "events": "history",
        "includePrePost": "false",
    }
    r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    root = r.json().get("chart", {}).get("result", [])
    if not root:
        return []
    chart = root[0]
    ts = chart.get("timestamp") or []
    quotes = ((chart.get("indicators") or {}).get("quote") or [{}])[0]
    opens = quotes.get("open") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    closes = quotes.get("close") or []
    vols = quotes.get("volume") or []

    rows = []
    n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
    for i in range(n):
        o = opens[i]
        h = highs[i]
        l = lows[i]
        c = closes[i]
        if o is None or h is None or l is None or c is None:
            continue
        bar_ts = datetime.fromtimestamp(int(ts[i]), tz=timezone.utc).replace(minute=0, second=0, microsecond=0)
        v = float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0
        rows.append(
            {
                "timestamp": bar_ts,
                "open": float(o),
                "high": float(h),
                "low": float(l),
                "close": float(c),
                "volume": float(v),
            }
        )
    return rows


def _minimum_acceptable_rows(start_dt: datetime, end_dt: datetime) -> int:
    requested_hours = max(1.0, (end_dt - start_dt).total_seconds() / 3600.0)
    return max(24, int(requested_hours * 0.10))


def _upsert_symbol_rows(db, symbol: str, provider: str, bars: Iterable, source: str) -> int:
    tf_market = int(settings.INTERVAL_MINUTES)
    written = 0
    for bar in bars:
        if isinstance(bar, dict):
            ts = bar["timestamp"].astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            o = float(bar["open"])
            h = float(bar["high"])
            l = float(bar["low"])
            c = float(bar["close"])
            v = float(bar.get("volume", 0.0) or 0.0)
        else:
            ts = bar.date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
            o = float(bar.open)
            h = float(bar.high)
            l = float(bar.low)
            c = float(bar.close)
            v = float(getattr(bar, "volume", 0.0) or 0.0)
        t_iso = ts.isoformat().replace("+00:00", "Z")
        db.cross_asset_candles.update_one(
            {"symbol": symbol, "provider": provider, "timestamp": ts},
            {"$set": {
                "symbol": symbol,
                "asset_class": _bucket(symbol),
                "provider": provider,
                "timestamp": ts,
                "o": o, "h": h, "l": l, "c": c, "v": v,
                "source": source,
            }},
            upsert=True,
        )
        db.market_candles.update_one(
            {"symbol": symbol, "tf": tf_market, "t": t_iso},
            {"$set": {"o": o, "h": h, "l": l, "c": c, "v": v}},
            upsert=True,
        )
        written += 1
    return written


def main() -> int:
    ap = argparse.ArgumentParser(description="Backfill cross-asset hourly candles from IBKR Gateway/TWS")
    ap.add_argument("--symbols", default="", help="Comma-separated symbols.")
    ap.add_argument("--years", type=int, default=1)
    ap.add_argument("--chunk-days", type=int, default=30)
    ap.add_argument("--sleep-ms", type=int, default=300)
    ap.add_argument("--from-iso", default="", help="Optional UTC ISO start, inclusive.")
    ap.add_argument("--to-iso", default="", help="Optional UTC ISO end, inclusive.")
    args = ap.parse_args()

    symbols = _parse_list(args.symbols) if args.symbols else _default_symbols()
    now = _parse_iso_utc(args.to_iso).replace(minute=0, second=0, microsecond=0) if args.to_iso else datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = _parse_iso_utc(args.from_iso).replace(minute=0, second=0, microsecond=0) if args.from_iso else (now - timedelta(days=365 * max(1, args.years)))
    if start > now:
        raise SystemExit("--from-iso must be <= --to-iso")

    ib = IB()
    host = str(getattr(settings, "IBKR_TWS_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = int(getattr(settings, "IBKR_TWS_PORT", 7497) or 7497)
    client_id = int(getattr(settings, "IBKR_CLIENT_ID", 77) or 77) + 100
    db = get_db()

    print(f"[IBKR-BACKFILL] connect host={host} port={port} client_id={client_id}")
    connected_host, connected_port = connect_ibkr_with_fallback(
        ib,
        host=host,
        configured_port=port,
        client_id=client_id,
        readonly=True,
        timeout_sec=15,
        trading_mode=normalize_gateway_trading_mode(getattr(settings, "IBKR_GATEWAY_TRADING_MODE", "paper")),
    )
    print(f"[IBKR-BACKFILL] using host={connected_host} port={connected_port}")
    util.startLoop = lambda *a, **k: None  # no-op safety

    ok = 0
    fail = 0
    total = 0
    try:
        for sym in symbols:
            try:
                contract = _qualify_one(ib, sym)
                sym_total = 0
                if contract is None:
                    raise ValueError("no_qualified_contract")
                cursor_end = now
                while cursor_end > start:
                    bars = _bar_rows(ib, contract, cursor_end, duration=f"{max(1, args.chunk_days)} D", bar_size="1 hour")
                    if not bars:
                        break
                    wrote = _upsert_symbol_rows(db, sym, "ibkr", bars, "cross_asset_backfill_ibkr")
                    sym_total += wrote
                    total += wrote
                    first_bar = min(b.date.astimezone(timezone.utc) for b in bars)
                    last_bar = max(b.date.astimezone(timezone.utc) for b in bars)
                    print(
                        f"[IBKR-BACKFILL] {sym}: chunk first={first_bar.isoformat()} last={last_bar.isoformat()} rows={len(bars)} upserts={wrote}"
                    )
                    next_end = first_bar - timedelta(hours=1)
                    if next_end >= cursor_end:
                        break
                    cursor_end = next_end
                    if first_bar <= start:
                        break
                    time.sleep(max(0.0, args.sleep_ms / 1000.0))
                minimum_rows = _minimum_acceptable_rows(start, now)
                if sym_total < minimum_rows:
                    raise ValueError(f"ibkr_rows_below_threshold rows={sym_total} minimum={minimum_rows}")
                print(f"[IBKR-BACKFILL] {sym}: DONE rows={sym_total}", flush=True)
                ok += 1
            except Exception as e:
                print(f"[IBKR-BACKFILL] {sym}: WARN ibkr_failed={e} -> yahoo_fallback", flush=True)
                try:
                    fallback_rows = _fetch_yahoo_rows(sym, start, now)
                    wrote = _upsert_symbol_rows(db, sym, "yahoo", fallback_rows, "cross_asset_backfill_ibkr_fallback")
                    total += wrote
                    print(f"[IBKR-BACKFILL] {sym}: FALLBACK_YAHOO rows={wrote}", flush=True)
                    ok += 1
                except Exception as fallback_error:
                    print(f"[IBKR-BACKFILL] {sym}: ERROR fallback_failed={fallback_error}", flush=True)
                    fail += 1
    finally:
        try:
            ib.disconnect()
        except Exception:
            pass

    print(f"[IBKR-BACKFILL] DONE ok={ok} fail={fail} rows={total}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
