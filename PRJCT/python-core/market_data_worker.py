"""
Market Data Worker — sbírá 8 metrik pro dashboard Market Data stránku.

1. Funding Rate (Binance Futures)
2. Open Interest (Binance Futures)
3. Liquidations 24h (Binance Futures)
4. Order Book depth (Binance)
5. Exchange Flow / BTC balance (Blockchain.info)
6. BTC Dominance (CoinGecko)
7. Stablecoin supply (CoinGecko)
8. TradFi korelace — S&P 500 + DXY (Yahoo Finance)

Použití:
    python market_data_worker.py          # loop každých 5 min
    python market_data_worker.py --once   # jednorázový run
"""
import asyncio
import logging
import requests
import sys
import time
from datetime import datetime, timezone
from trading.config import settings
from trading.mongo import get_db, ensure_indexes
import cross_asset_shadow_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [MARKET-DATA] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

TIMEOUT = 15


def _get_with_backoff(url: str, *, params: dict | None = None, headers: dict | None = None, timeout: int = TIMEOUT, retries: int = 3):
    """HTTP GET with exponential backoff for transient errors (429/5xx)."""
    wait = 1.0
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                if i < retries - 1:
                    ra = r.headers.get("Retry-After")
                    try:
                        wait = max(wait, float(ra))
                    except Exception:
                        pass
                    time.sleep(wait)
                    wait = min(wait * 2.0, 20.0)
                    continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(wait)
                wait = min(wait * 2.0, 20.0)
    raise last_err if last_err else RuntimeError("request failed")


def _get_symbols() -> list[str]:
    """Sestaví unikátní seznam symbolů primárně z Mongo market_candles, fallback na config."""
    syms = set()
    try:
        db = get_db()
        tf = int(settings.INTERVAL_MINUTES)
        db_symbols = db.market_candles.distinct("symbol", {"tf": tf, "symbol": {"$ne": None}})
        for s in db_symbols:
            if isinstance(s, str) and s.strip():
                syms.add(s.strip())
    except Exception as e:
        log.warning(f"Načtení symbolů z Mongo selhalo, použiji config fallback: {e}")

    # Fallback/merge s config universe (užitečné při prázdné DB nebo novém nasazení)
    for src in (settings.SYMBOLS, settings.BINANCE_SYMBOLS, settings.ALWAYS_ACTIVE_SYMBOLS):
        for s in src.split(","):
            s = s.strip()
            if s:
                syms.add(s)
    return sorted(syms)


def _to_futures(symbol: str) -> str:
    """'BTC/USDT' → 'BTCUSDT'"""
    return symbol.replace("/", "").upper()


# ─── 1. Funding Rates ─────────────────────────────────────────

def fetch_funding_rates(symbols: list[str]) -> dict:
    result = {}
    for sym in symbols:
        try:
            r = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": _to_futures(sym)}, timeout=TIMEOUT,
            )
            if r.status_code == 200:
                d = r.json()
                result[sym] = float(d.get("lastFundingRate", 0))
        except Exception as e:
            log.warning(f"Funding rate {sym}: {e}")
    return result


# ─── 2. Open Interest ─────────────────────────────────────────

def fetch_open_interest(symbols: list[str]) -> dict:
    result = {}
    for sym in symbols:
        try:
            # Mark price pro přepočet na USDT
            r1 = requests.get(
                "https://fapi.binance.com/fapi/v1/premiumIndex",
                params={"symbol": _to_futures(sym)}, timeout=TIMEOUT,
            )
            mark_price = float(r1.json().get("markPrice", 0)) if r1.status_code == 200 else 0

            r2 = requests.get(
                "https://fapi.binance.com/fapi/v1/openInterest",
                params={"symbol": _to_futures(sym)}, timeout=TIMEOUT,
            )
            if r2.status_code == 200:
                oi = float(r2.json().get("openInterest", 0))
                result[sym] = {"oi": oi, "oi_usdt": round(oi * mark_price, 0)}
        except Exception as e:
            log.warning(f"Open interest {sym}: {e}")
    return result


# ─── 3. Long/Short Ratio ─────────────────────────────────────

def fetch_long_short_ratio(symbols: list[str]) -> dict:
    """Stáhne long/short account ratio z Binance Futures (free, bez API klíče)."""
    result = {}
    for sym in symbols:
        try:
            r = requests.get(
                "https://fapi.binance.com/futures/data/globalLongShortAccountRatio",
                params={"symbol": _to_futures(sym), "period": "1h", "limit": 1},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                if data:
                    d = data[0]
                    result[sym] = {
                        "long_pct": round(float(d.get("longAccount", 0)) * 100, 1),
                        "short_pct": round(float(d.get("shortAccount", 0)) * 100, 1),
                        "ratio": round(float(d.get("longShortRatio", 1)), 2),
                    }
        except Exception as e:
            log.warning(f"Long/Short ratio {sym}: {e}")
    return result


# ─── 4. Order Book ────────────────────────────────────────────

def fetch_order_book(symbols: list[str]) -> dict:
    result = {}
    for sym in symbols:
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/depth",
                params={"symbol": _to_futures(sym), "limit": 10},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                bid_vol = sum(float(b[1]) for b in data.get("bids", []))
                ask_vol = sum(float(a[1]) for a in data.get("asks", []))
                imbalance = round(bid_vol / ask_vol, 2) if ask_vol > 0 else 0
                result[sym] = {
                    "bid_vol": round(bid_vol, 4),
                    "ask_vol": round(ask_vol, 4),
                    "imbalance": imbalance,
                }
        except Exception as e:
            log.warning(f"Order book {sym}: {e}")
    return result


# ─── 5. Exchange Flow (BTC) ───────────────────────────────────

def fetch_exchange_flow() -> dict:
    """BTC exchange balance z blockchain.info."""
    try:
        r = requests.get("https://blockchain.info/q/totalbc", timeout=TIMEOUT)
        if r.status_code == 200:
            total_satoshi = int(r.text.strip())
            return {"btc_total_supply": total_satoshi / 1e8}
    except Exception as e:
        log.warning(f"Exchange flow: {e}")
    return {"btc_total_supply": None}


# ─── 6. BTC Dominance ─────────────────────────────────────────

def fetch_btc_dominance() -> float | None:
    try:
        r = _get_with_backoff("https://api.coingecko.com/api/v3/global", timeout=TIMEOUT, retries=4)
        data = r.json().get("data", {})
        return round(data.get("market_cap_percentage", {}).get("btc", 0), 2)
    except Exception as e:
        log.warning(f"BTC dominance: {e}")
    return None


# ─── 7. Stablecoin Supply ─────────────────────────────────────

def fetch_stablecoin_supply() -> dict:
    result = {}
    for coin_id, label in [("tether", "USDT"), ("usd-coin", "USDC")]:
        try:
            r = _get_with_backoff(
                f"https://api.coingecko.com/api/v3/coins/{coin_id}",
                params={"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"},
                timeout=TIMEOUT,
                retries=4,
            )
            mcap = r.json().get("market_data", {}).get("market_cap", {}).get("usd")
            result[label] = mcap
        except Exception as e:
            log.warning(f"Stablecoin {label}: {e}")
    result["total"] = sum(v for v in result.values() if v)
    return result


# ─── 8. TradFi (S&P 500 + DXY) ───────────────────────────────

def fetch_tradfi() -> dict:
    result = {}
    for ticker, label in [("^GSPC", "sp500"), ("DX-Y.NYB", "dxy")]:
        try:
            r = requests.get(
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
                params={"interval": "1d", "range": "2d"},
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=TIMEOUT,
            )
            if r.status_code == 200:
                chart = r.json().get("chart", {}).get("result", [{}])[0]
                closes = chart.get("indicators", {}).get("quote", [{}])[0].get("close", [])
                closes = [c for c in closes if c is not None]
                if len(closes) >= 2:
                    result[label] = round(closes[-1], 2)
                    result[f"{label}_change"] = round((closes[-1] - closes[-2]) / closes[-2] * 100, 2)
                elif closes:
                    result[label] = round(closes[-1], 2)
                    result[f"{label}_change"] = 0
        except Exception as e:
            log.warning(f"TradFi {label}: {e}")
    return result


# ─── Main ─────────────────────────────────────────────────────

def run_once():
    db = get_db()
    symbols = _get_symbols()
    log.info(f"Sbírám market data pro {len(symbols)} symbolů: {symbols}")

    doc = {"timestamp": datetime.now(timezone.utc)}

    doc["funding_rates"] = fetch_funding_rates(symbols)
    log.info(f"  Funding rates: {len(doc['funding_rates'])} symbolů")

    doc["open_interest"] = fetch_open_interest(symbols)
    log.info(f"  Open interest: {len(doc['open_interest'])} symbolů")

    doc["long_short_ratio"] = fetch_long_short_ratio(symbols)
    log.info(f"  Long/Short ratio: {len(doc['long_short_ratio'])} symbolů")

    doc["order_book"] = fetch_order_book(symbols)
    log.info(f"  Order book: {len(doc['order_book'])} symbolů")

    doc["exchange_flow"] = fetch_exchange_flow()
    doc["btc_dominance"] = fetch_btc_dominance()
    doc["stablecoin_mcap"] = fetch_stablecoin_supply()
    doc["tradfi"] = fetch_tradfi()

    db.market_metrics.insert_one(doc)
    log.info("Market data uložena do MongoDB")

    # Optional cross-asset shadow collection (FX/commodities/indices) for IBKR expansion.
    try:
        if bool(getattr(settings, "CROSS_ASSET_SHADOW_ENABLED", False)):
            xa = cross_asset_shadow_worker.run_once()
            log.info(f"  Cross-asset shadow: ok={xa.get('ok',0)} fail={xa.get('fail',0)} symbols={xa.get('symbols',0)}")
    except Exception as e:
        log.warning(f"Cross-asset shadow run failed: {e}")


async def main():
    ensure_indexes()
    once = "--once" in sys.argv

    if once:
        run_once()
        return

    log.info(f"Startuji market data worker (interval={settings.MARKET_DATA_POLL_SECONDS}s)")
    while True:
        try:
            run_once()
        except Exception as e:
            log.error(f"Chyba v run_once: {e}")
        await asyncio.sleep(settings.MARKET_DATA_POLL_SECONDS)


if __name__ == "__main__":
    asyncio.run(main())
