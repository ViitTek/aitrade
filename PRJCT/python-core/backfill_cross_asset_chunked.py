import argparse
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Tuple

import requests

from trading.config import settings
from trading.mongo import get_db


YF_MAP: Dict[str, str] = {
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


def _parse_list(src: str) -> List[str]:
    return [x.strip().upper() for x in str(src or "").split(",") if x.strip()]


def _default_symbols() -> List[str]:
    return sorted(
        set(_parse_list(settings.CROSS_ASSET_FX_SYMBOLS))
        | set(_parse_list(settings.CROSS_ASSET_COMMODITY_SYMBOLS))
        | set(_parse_list(settings.CROSS_ASSET_INDEX_SYMBOLS))
    )


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


def _fetch_yf_hourly(
    ticker: str, start_epoch: int, end_epoch: int
) -> List[Tuple[datetime, float, float, float, float, float]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1": str(start_epoch),
        "period2": str(end_epoch),
        "interval": "60m",
        "events": "history",
        "includePrePost": "false",
    }
    r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    root = r.json().get("chart", {}).get("result", [])
    if not root:
        return []
    c = root[0]
    ts = c.get("timestamp") or []
    q = (c.get("indicators") or {}).get("quote") or [{}]
    q0 = q[0] if q else {}
    opens = q0.get("open") or []
    highs = q0.get("high") or []
    lows = q0.get("low") or []
    closes = q0.get("close") or []
    vols = q0.get("volume") or []
    out = []
    n = min(len(ts), len(opens), len(highs), len(lows), len(closes))
    for i in range(n):
        o = opens[i]
        h = highs[i]
        l = lows[i]
        cpx = closes[i]
        if o is None or h is None or l is None or cpx is None:
            continue
        v = float(vols[i]) if i < len(vols) and vols[i] is not None else 0.0
        dt = datetime.fromtimestamp(int(ts[i]), tz=timezone.utc)
        out.append((dt, float(o), float(h), float(l), float(cpx), v))
    return out


def _iter_chunks(dt_from: datetime, dt_to: datetime, days_per_chunk: int):
    cur = dt_from
    while cur < dt_to:
        nxt = min(cur + timedelta(days=days_per_chunk), dt_to)
        yield cur, nxt
        cur = nxt - timedelta(hours=1)


def _upsert_rows(db, symbol: str, rows: List[Tuple[datetime, float, float, float, float, float]]) -> int:
    tf_market = int(settings.INTERVAL_MINUTES)
    written = 0
    for ts, o, h, l, c, v in rows:
        db.cross_asset_candles.update_one(
            {"symbol": symbol, "provider": "yahoo", "timestamp": ts},
            {
                "$set": {
                    "symbol": symbol,
                    "asset_class": _bucket(symbol),
                    "provider": "yahoo",
                    "timestamp": ts,
                    "o": o,
                    "h": h,
                    "l": l,
                    "c": c,
                    "v": v,
                    "source": "cross_asset_backfill_chunked",
                }
            },
            upsert=True,
        )
        t_iso = ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        db.market_candles.update_one(
            {"symbol": symbol, "tf": tf_market, "t": t_iso},
            {"$set": {"o": o, "h": h, "l": l, "c": c, "v": v}},
            upsert=True,
        )
        written += 1
    return written


def main():
    ap = argparse.ArgumentParser(description="Chunked backfill of cross-asset hourly candles to MongoDB")
    ap.add_argument("--symbols", default="", help="Comma-separated symbols. Default: configured cross-asset symbols.")
    ap.add_argument("--years", type=int, default=1, help="How many years back from now to download.")
    ap.add_argument("--chunk-days", type=int, default=45, help="Chunk size in days for Yahoo hourly fetch.")
    ap.add_argument("--sleep-ms", type=int, default=250, help="Sleep between chunk requests.")
    args = ap.parse_args()

    if args.years < 1:
        raise ValueError("--years must be >= 1")
    if args.chunk_days < 7:
        raise ValueError("--chunk-days must be >= 7")

    symbols = _parse_list(args.symbols) if args.symbols else _default_symbols()
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)
    dt_from = now - timedelta(days=365 * args.years)
    db = get_db()

    ok = 0
    fail = 0
    total_rows = 0

    print(
        f"[XAS-CHUNKED] start years={args.years} chunk_days={args.chunk_days} "
        f"from={dt_from.isoformat()} to={now.isoformat()} symbols={','.join(symbols)}"
    )

    for sym in symbols:
        ticker = YF_MAP.get(sym.upper())
        if not ticker:
            print(f"[XAS-CHUNKED] {sym}: ERROR missing ticker mapping")
            fail += 1
            continue
        sym_rows = 0
        try:
            for chunk_from, chunk_to in _iter_chunks(dt_from, now, args.chunk_days):
                rows = _fetch_yf_hourly(ticker, int(chunk_from.timestamp()), int(chunk_to.timestamp()))
                wrote = _upsert_rows(db, sym, rows)
                sym_rows += wrote
                total_rows += wrote
                print(
                    f"[XAS-CHUNKED] {sym}: chunk {chunk_from.date()}..{chunk_to.date()} rows={len(rows)} upserts={wrote}"
                )
                time.sleep(max(0.0, args.sleep_ms / 1000.0))
            print(f"[XAS-CHUNKED] {sym}: DONE rows={sym_rows}")
            ok += 1
        except Exception as e:
            print(f"[XAS-CHUNKED] {sym}: ERROR {e}")
            fail += 1

    print(f"[XAS-CHUNKED] DONE ok={ok} fail={fail} rows={total_rows}")


if __name__ == "__main__":
    main()
