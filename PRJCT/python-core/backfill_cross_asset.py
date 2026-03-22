import argparse
from datetime import datetime, timezone
from typing import Dict, List, Tuple
import re

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


def _symbols() -> List[str]:
    return (
        _parse_list(settings.CROSS_ASSET_FX_SYMBOLS)
        + _parse_list(settings.CROSS_ASSET_COMMODITY_SYMBOLS)
        + _parse_list(settings.CROSS_ASSET_INDEX_SYMBOLS)
    )


def _fetch_yf_hourly(ticker: str, start_epoch: int, end_epoch: int) -> List[Tuple[datetime, float, float, float, float, float]]:
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "period1": str(start_epoch),
        "period2": str(end_epoch),
        "interval": "60m",
        "events": "history",
        "includePrePost": "false",
    }
    r = requests.get(url, params=params, headers={"User-Agent": "Mozilla/5.0"}, timeout=25)
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


def main():
    ap = argparse.ArgumentParser(description="Backfill cross-asset candles to MongoDB")
    ap.add_argument("--from-iso", required=True, help="UTC/ISO lower bound")
    ap.add_argument("--to-iso", default="", help="UTC/ISO upper bound (default now)")
    args = ap.parse_args()

    def _parse_iso(src: str) -> datetime:
        s = str(src or "").strip()
        m = re.match(r"^(.*?\.)(\d+)([+-].*|Z)$", s)
        if m:
            s = f"{m.group(1)}{m.group(2)[:6]}{m.group(3)}"
        return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)

    dt_from = _parse_iso(args.from_iso)
    dt_to = datetime.now(timezone.utc) if not args.to_iso else _parse_iso(args.to_iso)
    p1 = int(dt_from.timestamp())
    p2 = int(dt_to.timestamp())

    db = get_db()
    syms = _symbols()
    ok = 0
    fail = 0
    rows = 0

    for sym in syms:
        ticker = YF_MAP.get(sym.upper())
        if not ticker:
            fail += 1
            continue
        try:
            candles = _fetch_yf_hourly(ticker, p1, p2)
            for ts, o, h, l, c, v in candles:
                db.cross_asset_candles.update_one(
                    {"symbol": sym, "provider": "yahoo", "timestamp": ts},
                    {"$set": {
                        "symbol": sym,
                        "asset_class": _bucket(sym),
                        "provider": "yahoo",
                        "timestamp": ts,
                        "o": o, "h": h, "l": l, "c": c, "v": v,
                        "source": "cross_asset_backfill",
                    }},
                    upsert=True,
                )
                t_iso = ts.replace(microsecond=0).isoformat().replace("+00:00", "Z")
                db.market_candles.update_one(
                    {"symbol": sym, "tf": int(settings.INTERVAL_MINUTES), "t": t_iso},
                    {"$set": {"o": o, "h": h, "l": l, "c": c, "v": v}},
                    upsert=True,
                )
                rows += 1
            ok += 1
            print(f"[XAS-BACKFILL] {sym}: {len(candles)} rows")
        except Exception as e:
            fail += 1
            print(f"[XAS-BACKFILL] {sym}: ERROR {e}")

    print(f"[XAS-BACKFILL] DONE ok={ok} fail={fail} rows={rows} from={dt_from.isoformat()} to={dt_to.isoformat()}")


if __name__ == "__main__":
    main()
