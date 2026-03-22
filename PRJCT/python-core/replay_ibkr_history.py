import argparse
import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from trading.config import settings
from trading.engine import TradingEngine
from trading.mongo import get_db


def _parse_iso(src: str) -> datetime:
    s = str(src or "").strip()
    m = re.match(r"^(.*?\.)(\d+)([+-].*|Z)$", s)
    if m:
        s = f"{m.group(1)}{m.group(2)[:6]}{m.group(3)}"
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def _parse_syms(src: str):
    return [x.strip() for x in str(src or "").split(",") if x.strip()]


async def main():
    ap = argparse.ArgumentParser(description="Replay IBKR/cross-asset candles into TradingEngine for paper trades")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--from-iso", required=False, default="")
    ap.add_argument("--to-iso", required=False, default="")
    args = ap.parse_args()

    run_id = str(args.run_id).strip()
    if not run_id:
        raise SystemExit("run_id required")

    db = get_db()
    dt_from = _parse_iso(args.from_iso) if args.from_iso else None
    dt_to = _parse_iso(args.to_iso) if args.to_iso else datetime.now(timezone.utc)

    old_mode = settings.MODE
    old_shadow = bool(getattr(settings, "SHADOW_MODE_ENABLED", False))
    old_ibkr_enabled = bool(getattr(settings, "TRADING_IBKR_ENABLED", False))
    try:
        settings.MODE = "paper"
        settings.SHADOW_MODE_ENABLED = False
        settings.TRADING_IBKR_ENABLED = True

        engine = TradingEngine(run_id=run_id, interval=int(settings.INTERVAL_MINUTES))
        syms = _parse_syms(getattr(settings, "IBKR_SYMBOLS", ""))
        total = 0
        for sym in syms:
            q = {"symbol": sym, "tf": int(settings.INTERVAL_MINUTES)}
            if dt_from is not None:
                q["t"] = {"$gte": dt_from.replace(microsecond=0).isoformat().replace("+00:00", "Z")}
                if dt_to is not None:
                    q["t"]["$lte"] = dt_to.replace(microsecond=0).isoformat().replace("+00:00", "Z")
            elif dt_to is not None:
                q["t"] = {"$lte": dt_to.replace(microsecond=0).isoformat().replace("+00:00", "Z")}

            rows = list(
                db.market_candles.find(q, {"_id": 0, "t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1})
                .sort("t", 1)
            )
            for r in rows:
                await engine.on_candle(
                    sym,
                    int(settings.INTERVAL_MINUTES),
                    {
                        "symbol": sym,
                        "timestamp": r["t"],
                        "open": r["o"],
                        "high": r["h"],
                        "low": r["l"],
                        "close": r["c"],
                        "volume": r["v"],
                        "_no_persist": True,
                    },
                )
            total += len(rows)
            print(f"[IBKR-REPLAY] {sym}: candles={len(rows)}")

        closed = db.positions.count_documents({"run_id": run_id, "status": "CLOSED", "symbol": {"$in": syms}})
        signals = db.bot_signals.count_documents({"run_id": run_id, "symbol": {"$in": syms}})
        print(json.dumps({"ok": True, "run_id": run_id, "candles_replayed": total, "ibkr_closed_positions": closed, "ibkr_signals": signals}))
    finally:
        settings.MODE = old_mode
        settings.SHADOW_MODE_ENABLED = old_shadow
        settings.TRADING_IBKR_ENABLED = old_ibkr_enabled


if __name__ == "__main__":
    asyncio.run(main())

