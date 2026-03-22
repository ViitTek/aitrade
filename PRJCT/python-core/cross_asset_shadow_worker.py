import argparse
from datetime import datetime, timezone
from typing import Dict, List, Tuple

import requests
from ib_insync import ContFuture, Forex, Future, IB, Index

from trading.config import settings
from trading.ibkr_connection import connect_ibkr_with_fallback, normalize_gateway_trading_mode
from trading.mongo import get_db


STOOQ_MAP: Dict[str, str] = {
    # FX (USD quote)
    "EURUSD": "eurusd",
    "GBPUSD": "gbpusd",
    "USDJPY": "usdjpy",
    "AUDUSD": "audusd",
    # Commodities / proxies
    "XAUUSD": "xauusd",
    "XAGUSD": "xagusd",
    "CL": "cl.f",
    "WTI": "cl.f",
    "BRENT": "brn.f",
    # Indices / proxies
    "SPX": "^spx",
    "NDX": "^ndx",
    "DAX": "^dax",
    "FTSE": "^ukx",
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


def _stooq_url(ticker: str) -> str:
    return f"https://stooq.com/q/l/?s={ticker}&f=sd2t2ohlcv&h&e=csv"


def _fetch_stooq(symbol: str) -> Tuple[datetime, float, float, float, float, float]:
    ticker = STOOQ_MAP.get(symbol.upper())
    if not ticker:
        raise ValueError(f"unsupported symbol mapping: {symbol}")
    r = requests.get(_stooq_url(ticker), timeout=20)
    r.raise_for_status()
    lines = [ln.strip() for ln in r.text.splitlines() if ln.strip()]
    if len(lines) < 2:
        raise ValueError(f"no data rows for {symbol}")
    cols = [x.strip() for x in lines[1].split(",")]
    # Symbol,Date,Time,Open,High,Low,Close,Volume
    o = float(cols[3])
    h = float(cols[4])
    l = float(cols[5])
    c = float(cols[6])
    v = float(cols[7]) if len(cols) > 7 and cols[7] else 0.0
    ts = datetime.now(timezone.utc).replace(second=0, microsecond=0)
    return ts, o, h, l, c, v


def _contract_candidates(symbol: str):
    s = symbol.upper()
    if s in {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD"}:
        return [Forex(s)]
    if s in {"CL", "WTI"}:
        return [ContFuture("CL", "NYMEX")]
    if s == "BRENT":
        return [ContFuture("BZ", "ICEEU")]
    if s == "XAUUSD":
        return ["MATCH:XAUUSD"]
    if s == "XAGUSD":
        return ["MATCH:XAGUSD"]
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
                    qualified = ib.qualifyContracts(matches[0].contract)
                    if qualified:
                        return qualified[0]
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


def _fetch_ibkr(ib: IB, symbol: str) -> Tuple[datetime, float, float, float, float, float]:
    contract = _qualify_one(ib, symbol)
    if contract is None:
        raise ValueError(f"no qualified contract: {symbol}")
    bars = list(
        ib.reqHistoricalData(
            contract,
            endDateTime="",
            durationStr="3 D",
            barSizeSetting="1 hour",
            whatToShow=_what_to_show(contract),
            useRTH=False,
            formatDate=2,
            keepUpToDate=False,
        )
        or []
    )
    if not bars:
        raise ValueError(f"no ibkr bars: {symbol}")
    bar = max(bars, key=lambda b: b.date)
    ts = bar.date.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    return ts, float(bar.open), float(bar.high), float(bar.low), float(bar.close), float(getattr(bar, "volume", 0.0) or 0.0)


def run_once() -> Dict[str, int]:
    db = get_db()
    provider = (settings.CROSS_ASSET_PROVIDER or "stooq").strip().lower()
    if provider == "ibkr":
        symbols = _parse_list(getattr(settings, "IBKR_SYMBOLS", ""))
    else:
        symbols = (
            _parse_list(settings.CROSS_ASSET_FX_SYMBOLS)
            + _parse_list(settings.CROSS_ASSET_COMMODITY_SYMBOLS)
            + _parse_list(settings.CROSS_ASSET_INDEX_SYMBOLS)
        )
    ok = 0
    fail = 0
    ib = None
    now = datetime.now(timezone.utc)
    try:
        if provider == "ibkr":
            ib = IB()
            host = str(getattr(settings, "IBKR_TWS_HOST", "127.0.0.1") or "127.0.0.1").strip()
            port = int(getattr(settings, "IBKR_TWS_PORT", 7497) or 7497)
            client_id = int(getattr(settings, "IBKR_CLIENT_ID", 77) or 77) + 200
            connect_ibkr_with_fallback(
                ib,
                host=host,
                configured_port=port,
                client_id=client_id,
                readonly=True,
                timeout_sec=15,
                trading_mode=normalize_gateway_trading_mode(getattr(settings, "IBKR_GATEWAY_TRADING_MODE", "paper")),
            )

        for sym in symbols:
            try:
                if provider == "stooq":
                    ts, o, h, l, c, v = _fetch_stooq(sym)
                elif provider == "ibkr":
                    ts, o, h, l, c, v = _fetch_ibkr(ib, sym)
                else:
                    raise ValueError(f"provider not implemented in shadow worker: {provider}")
                doc = {
                    "symbol": sym,
                    "asset_class": _bucket(sym),
                    "provider": provider,
                    "timestamp": ts,
                    "o": o,
                    "h": h,
                    "l": l,
                    "c": c,
                    "v": v,
                    "source": "cross_asset_shadow_worker",
                }
                db.cross_asset_candles.update_one(
                    {"symbol": sym, "provider": provider, "timestamp": ts},
                    {"$set": doc},
                    upsert=True,
                )
                t_iso = ts.isoformat().replace("+00:00", "Z")
                db.market_candles.update_one(
                    {"symbol": sym, "tf": int(settings.INTERVAL_MINUTES), "t": t_iso},
                    {"$set": {"o": o, "h": h, "l": l, "c": c, "v": v}},
                    upsert=True,
                )
                ok += 1
            except Exception as e:
                db.bot_events.insert_one(
                    {
                        "run_id": "cross-asset-shadow",
                        "t": now.isoformat().replace("+00:00", "Z"),
                        "event": "cross_asset_fetch_error",
                        "symbol": sym,
                        "detail": str(e),
                    }
                )
                fail += 1
    finally:
        try:
            if ib is not None and ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
    return {"ok": ok, "fail": fail, "symbols": len(symbols)}


def main():
    parser = argparse.ArgumentParser(description="Cross-asset shadow data worker (no execution)")
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit")
    args = parser.parse_args()

    if not bool(getattr(settings, "CROSS_ASSET_SHADOW_ENABLED", False)):
        print("[XAS] CROSS_ASSET_SHADOW_ENABLED=false, worker exit.")
        return

    interval = max(30, int(getattr(settings, "CROSS_ASSET_POLL_SECONDS", 300)))
    if args.once:
        out = run_once()
        print(f"[XAS] once ok={out['ok']} fail={out['fail']} symbols={out['symbols']}")
        return

    print(f"[XAS] start provider={settings.CROSS_ASSET_PROVIDER} interval={interval}s")
    while True:
        out = run_once()
        print(f"[XAS] cycle ok={out['ok']} fail={out['fail']} symbols={out['symbols']}")
        try:
            import time

            time.sleep(interval)
        except KeyboardInterrupt:
            print("[XAS] stopped.")
            break


if __name__ == "__main__":
    main()
