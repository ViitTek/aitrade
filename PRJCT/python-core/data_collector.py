"""
Standalone data collector — spustí KrakenWS + BinanceWS a ukládá svíčky do MongoDB.
Bez trading logiky. Určeno pro 24/7 běh a akumulaci historických dat pro backtest.

Podporuje dynamickou subscription na nové symboly dle LLM doporučení
(DYNAMIC_ASSETS_ENABLED=True).

Použití:
    python data_collector.py
"""
import asyncio
import logging
import requests
from datetime import datetime, timezone, timedelta
from trading.config import settings
from trading.mongo import get_db, ensure_indexes
from trading.kraken_ws import KrakenWS
from trading.binance_ws import BinanceWS

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [COLLECTOR] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

_count = 0
_inserted = 0
_updated = 0

# Binance interval mapování pro REST API backfill
_BINANCE_INTERVAL_MAP = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d"}


def _as_utc_aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


async def on_candle(symbol: str, tf: int, item: dict):
    """Callback: upsert svíčky do market_candles kolekce."""
    global _count, _inserted, _updated
    db = get_db()

    symbol = (symbol or "").strip()
    t = item.get("interval_begin") or item.get("timestamp")
    if not symbol or not t:
        return

    doc = {
        "t": t,
        "o": float(item["open"]),
        "h": float(item["high"]),
        "l": float(item["low"]),
        "c": float(item["close"]),
        "v": float(item["volume"]),
    }
    res = db.market_candles.update_one(
        {"symbol": symbol, "tf": tf, "t": t},
        {"$set": doc},
        upsert=True,
    )
    _count += 1
    if res.upserted_id is not None:
        _inserted += 1
    else:
        _updated += 1
    if _count % 10 == 0:
        log.info(
            f"Zpracováno {_count} upsert operací (new={_inserted}, update={_updated}) "
            f"| poslední: {symbol} @ {t}"
        )


def backfill_symbol(symbol: str, interval: int, count: int = 60):
    """Stáhne posledních N svíček z Binance REST API a uloží do MongoDB."""
    db = get_db()
    symbol = (symbol or "").strip()
    if not symbol:
        return
    pair = symbol.replace("/", "").upper()  # "SOL/USDT" → "SOLUSDT"
    binance_interval = _BINANCE_INTERVAL_MAP.get(interval, f"{interval}m")

    try:
        r = requests.get("https://api.binance.com/api/v3/klines", params={
            "symbol": pair,
            "interval": binance_interval,
            "limit": count,
        }, timeout=15)
        r.raise_for_status()
        rows = r.json()

        saved = 0
        for row in rows:
            t = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc).isoformat()
            doc = {
                "t": t,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }
            db.market_candles.update_one(
                {"symbol": symbol, "tf": interval, "t": t},
                {"$set": doc},
                upsert=True,
            )
            saved += 1

        log.info(f"Backfill {symbol}: {saved} svíček uloženo z Binance REST")
    except Exception as e:
        log.warning(f"Backfill {symbol} selhal: {e}")


def _to_futures_symbol(symbol: str) -> str:
    """'BTC/USDT' → 'BTCUSDT' pro Binance Futures API."""
    return symbol.replace("/", "").upper()


async def poll_funding_oi():
    """Každých N sekund stáhne funding rate + open interest z Binance Futures."""
    db = get_db()

    while True:
        await asyncio.sleep(settings.FUNDING_POLL_SECONDS)

        if not settings.FUNDING_ENABLED and not settings.OI_ENABLED:
            continue

        # Sestav seznam symbolů ze všech zdrojů
        all_symbols = set()
        for src in (settings.SYMBOLS, settings.BINANCE_SYMBOLS, settings.ALWAYS_ACTIVE_SYMBOLS):
            for s in src.split(","):
                s = s.strip()
                if s:
                    all_symbols.add(s)

        for symbol in all_symbols:
            futures_sym = _to_futures_symbol(symbol)
            try:
                doc = {"symbol": symbol, "timestamp": datetime.now(timezone.utc)}

                # Funding Rate + Mark Price
                r = requests.get(
                    "https://fapi.binance.com/fapi/v1/premiumIndex",
                    params={"symbol": futures_sym}, timeout=10,
                )
                if r.status_code == 200:
                    data = r.json()
                    doc["funding_rate"] = float(data.get("lastFundingRate", 0))
                    doc["mark_price"] = float(data.get("markPrice", 0))
                    doc["next_funding_time"] = datetime.fromtimestamp(
                        int(data.get("nextFundingTime", 0)) / 1000, tz=timezone.utc
                    ).isoformat()
                else:
                    log.warning(f"Funding fetch {symbol}: HTTP {r.status_code}")
                    continue

                # Open Interest
                r2 = requests.get(
                    "https://fapi.binance.com/fapi/v1/openInterest",
                    params={"symbol": futures_sym}, timeout=10,
                )
                if r2.status_code == 200:
                    oi_data = r2.json()
                    oi = float(oi_data.get("openInterest", 0))
                    doc["open_interest"] = oi
                    doc["open_interest_usdt"] = oi * doc.get("mark_price", 0)

                db.funding_oi.insert_one(doc)

            except Exception as e:
                log.warning(f"Funding/OI fetch pro {symbol} selhal: {e}")

        log.info(f"Funding/OI: uloženo pro {len(all_symbols)} symbolů")


async def poll_recommendations(binance_ws: BinanceWS):
    """Každých 5 minut kontroluje asset_recommendations a upravuje subscription."""
    db = get_db()
    current_symbols = set(binance_ws.symbols)

    # Symboly které nikdy neodebíráme
    always = set(s.strip() for s in settings.ALWAYS_ACTIVE_SYMBOLS.split(",") if s.strip())
    kraken = set(s.strip() for s in settings.SYMBOLS.split(",") if s.strip())
    base_binance = set(s.strip() for s in settings.BINANCE_SYMBOLS.split(",") if s.strip())
    keep = always | kraken | base_binance

    while True:
        await asyncio.sleep(300)  # 5 minut

        if not settings.DYNAMIC_ASSETS_ENABLED:
            continue

        try:
            latest = db.asset_recommendations.find_one(sort=[("created_at", -1)])
            if not latest:
                continue

            created_at = _as_utc_aware(latest["created_at"])
            age = (datetime.now(timezone.utc) - created_at).total_seconds() / 60
            if age > settings.RECOMMENDATION_MAX_AGE_MINUTES:
                continue

            recommended = set(latest.get("symbols", []))

            to_add = recommended - current_symbols
            to_remove = current_symbols - recommended - keep

            if to_add:
                # Backfill historických svíček pro nové symboly
                loop = asyncio.get_event_loop()
                for sym in to_add:
                    await loop.run_in_executor(
                        None, backfill_symbol, sym, settings.INTERVAL_MINUTES, 60
                    )

                await binance_ws.subscribe(list(to_add))
                current_symbols |= to_add
                log.info(f"Nové symboly přidány: {to_add}")

            if to_remove:
                await binance_ws.unsubscribe(list(to_remove))
                current_symbols -= to_remove
                log.info(f"Symboly odebrány: {to_remove}")

        except Exception as e:
            log.warning(f"Poll recommendations chyba: {e}")


async def main():
    ensure_indexes()
    intervals = []
    for x in str(settings.COLLECT_INTERVALS).split(","):
        x = x.strip()
        if not x:
            continue
        try:
            iv = int(x)
        except Exception:
            continue
        if iv > 0:
            intervals.append(iv)
    if not intervals:
        intervals = [int(settings.INTERVAL_MINUTES)]
    intervals = sorted(set(intervals))

    # Kraken (all configured intervals)
    kraken_symbols = [s.strip() for s in settings.SYMBOLS.split(",") if s.strip()]
    kraken_ws_list = []
    for iv in intervals:
        log.info(f"Kraken symboly: {kraken_symbols}, interval={iv}m")
        ws = KrakenWS(on_candle=on_candle, interval=iv, symbols=kraken_symbols)
        await ws.start()
        kraken_ws_list.append(ws)

    # Binance (all configured intervals)
    binance_symbols = [s.strip() for s in settings.BINANCE_SYMBOLS.split(",") if s.strip()]
    binance_ws_list = []
    if binance_symbols:
        for iv in intervals:
            log.info(f"Binance symboly: {binance_symbols}, interval={iv}m")
            ws = BinanceWS(on_candle=on_candle, symbols=binance_symbols, interval=iv)
            await ws.start()
            binance_ws_list.append(ws)
    # Primary Binance WS for dynamic subscriptions/backfill follows trading interval.
    primary_binance_ws = next((w for w in binance_ws_list if w.interval == settings.INTERVAL_MINUTES), None)

    # Funding Rate + Open Interest polling
    if settings.FUNDING_ENABLED or settings.OI_ENABLED:
        log.info("Funding/OI polling zapnuto")
        asyncio.create_task(poll_funding_oi())

    # Dynamic asset polling (pokud je zapnuto)
    if primary_binance_ws and settings.DYNAMIC_ASSETS_ENABLED:
        log.info("Dynamic assets zapnuto — startuji recommendation polling")
        asyncio.create_task(poll_recommendations(primary_binance_ws))

    try:
        while True:
            await asyncio.sleep(60)
    except KeyboardInterrupt:
        log.info("Ukončuji...")
        for ws in kraken_ws_list:
            await ws.stop()
        for ws in binance_ws_list:
            await ws.stop()


if __name__ == "__main__":
    asyncio.run(main())
