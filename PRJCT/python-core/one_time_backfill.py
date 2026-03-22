"""
One-time historical backfill for AIInvest.

What it can do:
1) Download OHLC candles from Binance Spot into MongoDB `market_candles`.
2) Compute market reaction to executed signals into `signal_outcomes`.
3) Optionally backfill historical news via NewsAPI and classify sentiment.

Examples:
    # Market candles + signal outcomes from 2025-01-01 to today
    venv\\Scripts\\python.exe one_time_backfill.py --from 2025-01-01 --interval 60

    # Include NewsAPI historical news (requires key)
    venv\\Scripts\\python.exe one_time_backfill.py --from 2025-01-01 --newsapi-key YOUR_KEY --with-llm
"""

from __future__ import annotations

import argparse
import hashlib
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, List, Optional
from xml.etree import ElementTree as ET

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from trading.config import settings
from trading.mongo import ensure_indexes, get_db


BINANCE_URL = "https://api.binance.com/api/v3/klines"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/fundingRate"
BINANCE_MARK_PRICE_KLINES_URL = "https://fapi.binance.com/fapi/v1/markPriceKlines"
NEWSAPI_URL = "https://newsapi.org/v2/everything"
INTERVAL_MAP = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d"}
DEFAULT_KURZY_RSS = [
    "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=ptEkonomika",
    "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=wzAkcieSvet",
    "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=ptKomodity",
    "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=wzMeny",
    "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=wzMakro",
]
DEFAULT_GLOBAL_CRYPTO_RSS = [
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
    "https://decrypt.co/feed",
    "https://news.bitcoin.com/feed/",
    "https://www.investing.com/rss/news_301.rss",
    "https://www.theblock.co/rss.xml",
]

# Symbol -> news query
NEWS_QUERY = {
    "BTC": "bitcoin OR BTC",
    "ETH": "ethereum OR ETH",
    "SOL": "solana OR SOL",
    "XRP": "ripple OR XRP",
    "DOGE": "dogecoin OR DOGE",
    "BNB": "binance coin OR BNB",
    "TRX": "tron OR TRX",
    "PAXG": "pax gold OR PAXG",
    "USDC": "usd coin OR USDC",
}


def _build_http_session() -> requests.Session:
    sess = requests.Session()
    retry = Retry(
        total=5,
        connect=5,
        read=5,
        backoff_factor=0.8,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry, pool_connections=32, pool_maxsize=32)
    sess.mount("https://", adapter)
    sess.mount("http://", adapter)
    sess.headers.update(
        {
            "User-Agent": "AIInvestBackfill/1.0",
            "Accept": "application/json,application/xml,text/xml,*/*",
            "Connection": "keep-alive",
        }
    )
    return sess


HTTP = _build_http_session()


def iso_to_dt(s: str) -> datetime:
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    d = datetime.fromisoformat(s)
    if d.tzinfo is None:
        return d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def dt_to_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def date_to_dt_utc(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)


def to_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def parse_symbol_list(raw: str) -> List[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def discover_symbols(db, tf: int) -> List[str]:
    raw_symbols = db.market_candles.distinct("symbol", {"tf": tf})
    symbols = sorted(
        s.strip() for s in raw_symbols
        if isinstance(s, str) and s.strip()
    )
    if symbols:
        return symbols
    seen = set()
    out: List[str] = []
    for src in (settings.SYMBOLS, settings.BINANCE_SYMBOLS, settings.ALWAYS_ACTIVE_SYMBOLS):
        for s in parse_symbol_list(src):
            if s not in seen:
                seen.add(s)
                out.append(s)
    return out


def fetch_binance_klines(symbol: str, interval_min: int, dt_from: datetime, dt_to: datetime) -> Iterable[dict]:
    pair = symbol.replace("/", "").upper()
    interval = INTERVAL_MAP.get(interval_min)
    if not interval:
        raise ValueError(f"Unsupported interval: {interval_min}")

    start_ms = to_ms(dt_from)
    end_ms = to_ms(dt_to)
    while start_ms < end_ms:
        r = HTTP.get(
            BINANCE_URL,
            params={
                "symbol": pair,
                "interval": interval,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=20,
        )
        r.raise_for_status()
        rows = r.json()
        if not rows:
            break

        for row in rows:
            t = datetime.fromtimestamp(row[0] / 1000, tz=timezone.utc)
            yield {
                "t": dt_to_iso(t),
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }

        last_open_ms = int(rows[-1][0])
        # Move forward by one interval candle to avoid duplicate page.
        start_ms = last_open_ms + interval_min * 60 * 1000
        time.sleep(0.12)


def backfill_candles(db, symbols: List[str], interval_min: int, dt_from: datetime, dt_to: datetime) -> None:
    print(f"[BACKFILL] Candles from {dt_from.date()} to {dt_to.date()} | tf={interval_min}m")
    for sym in symbols:
        saved = 0
        try:
            for doc in fetch_binance_klines(sym, interval_min, dt_from, dt_to):
                db.market_candles.update_one(
                    {"symbol": sym, "tf": interval_min, "t": doc["t"]},
                    {"$set": doc},
                    upsert=True,
                )
                saved += 1
            print(f"  {sym}: upserted {saved}")
        except Exception as e:
            print(f"  {sym}: FAILED ({e})")


def find_close_at_or_after(db, symbol: str, tf: int, target_time: datetime) -> Optional[float]:
    row = db.market_candles.find_one(
        {"symbol": symbol, "tf": tf, "t": {"$gte": dt_to_iso(target_time)}},
        sort=[("t", 1)],
    )
    if not row:
        return None
    return float(row["c"])


def compute_signal_outcomes(db, tf: int, horizons_min: List[int]) -> None:
    print(f"[BACKFILL] Signal outcomes | tf={tf}m | horizons={horizons_min}")
    q = {"action": "executed"}
    signals = list(db.bot_signals.find(q).sort("t", 1))
    if not signals:
        print("  No executed signals found in bot_signals.")
        return

    done = 0
    for s in signals:
        try:
            t0 = iso_to_dt(str(s["t"]))
            symbol = str(s["symbol"])
            side = str(s["side"])
            p0 = float(s["price"])
            direction = 1.0 if side == "BUY" else -1.0

            payload = {
                "run_id": s.get("run_id"),
                "signal_t": dt_to_iso(t0),
                "symbol": symbol,
                "side": side,
                "entry_price": p0,
                "source": "backfill_v1",
                "updated_at": datetime.now(timezone.utc),
            }

            for h in horizons_min:
                px = find_close_at_or_after(db, symbol, tf, t0 + timedelta(minutes=h))
                key_px = f"px_{h}m"
                key_ret = f"ret_{h}m"
                payload[key_px] = px
                if px is None:
                    payload[key_ret] = None
                else:
                    ret = ((px - p0) / p0) * direction
                    payload[key_ret] = round(ret, 6)

            db.signal_outcomes.update_one(
                {
                    "run_id": payload["run_id"],
                    "signal_t": payload["signal_t"],
                    "symbol": payload["symbol"],
                    "side": payload["side"],
                },
                {"$set": payload},
                upsert=True,
            )
            done += 1
        except Exception:
            continue

    print(f"  Upserted outcomes: {done}")


def stable_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def _symbol_keywords(symbol: str) -> List[str]:
    base = symbol.split("/")[0].upper()
    words = {
        "BTC": ["bitcoin", "btc", "krypto", "kryptomena"],
        "ETH": ["ethereum", "eth"],
        "SOL": ["solana", "sol"],
        "XRP": ["xrp", "ripple"],
        "DOGE": ["dogecoin", "doge"],
        "BNB": ["bnb", "binance coin"],
        "TRX": ["trx", "tron"],
        "PAXG": ["paxg", "gold", "zlato"],
        "USDC": ["usdc", "stablecoin"],
    }
    return words.get(base, [base.lower()])


def _match_symbols_from_text(symbols: List[str], text: str) -> List[str]:
    txt = (text or "").lower()
    out: List[str] = []
    for sym in symbols:
        for kw in _symbol_keywords(sym):
            if kw and kw in txt:
                out.append(sym.split("/")[0].upper())
                break
    return sorted(set(out))


def _parse_rss_pubdate(raw: str) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _safe_parse_xml(content: bytes):
    """Parse RSS XML with a light sanitation fallback for malformed entities."""
    try:
        return ET.fromstring(content)
    except Exception:
        text = content.decode("utf-8", errors="replace")
        text = re.sub(r"&(?!amp;|lt;|gt;|quot;|apos;|#\d+;|#x[0-9a-fA-F]+;)", "&amp;", text)
        text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", text)
        return ET.fromstring(text.encode("utf-8"))


def _heuristic_sentiment(text: str) -> str:
    t = (text or "").lower()
    pos_words = [
        "surge", "rally", "breakout", "adoption", "approval", "bull", "bullish", "gain", "up",
        "inflow", "record high", "strong demand", "pozitiv", "rust", "růst", "zisk",
    ]
    neg_words = [
        "crash", "selloff", "hack", "ban", "lawsuit", "bear", "bearish", "drop", "down",
        "outflow", "liquidation", "risk-off", "negativ", "pokles", "ztrata", "ztráta",
    ]
    p = sum(1 for w in pos_words if w in t)
    n = sum(1 for w in neg_words if w in t)
    if p > n:
        return "Positive"
    if n > p:
        return "Negative"
    return "Neutral"


def classify_sentiment(text: str) -> str:
    from llama_wrapper import run_llama_oneword

    prompt = (
        "Return exactly ONE word: Positive, Neutral, or Negative.\n"
        f"Text: {text}\n"
        "Answer:"
    )
    try:
        raw = str(run_llama_oneword(prompt, timeout_sec=30) or "").strip().lower()
        if raw.startswith("pos"):
            return "Positive"
        if raw.startswith("neg"):
            return "Negative"
        if raw.startswith("neu"):
            return "Neutral"
    except Exception:
        pass
    return _heuristic_sentiment(text)


def classify_sentiment_mode(text: str, use_llm: bool) -> str:
    """Classify sentiment with explicit mode.

    - use_llm=True: try local LLM, fallback to heuristic.
    - use_llm=False: heuristic only (fast, deterministic).
    """
    if use_llm:
        return classify_sentiment(text)
    return _heuristic_sentiment(text)


def backfill_news(
    db,
    symbols: List[str],
    dt_from: datetime,
    dt_to: datetime,
    api_key: str,
    with_llm: bool,
    max_pages: int,
) -> None:
    print("[BACKFILL] NewsAPI historical news")
    total_news = 0
    total_sent = 0

    for pair in symbols:
        base = pair.split("/")[0].upper()
        query = NEWS_QUERY.get(base, base)
        for page in range(1, max_pages + 1):
            r = HTTP.get(
                NEWSAPI_URL,
                params={
                    "q": query,
                    "from": dt_from.date().isoformat(),
                    "to": dt_to.date().isoformat(),
                    "language": "en",
                    "sortBy": "publishedAt",
                    "pageSize": 100,
                    "page": page,
                    "apiKey": api_key,
                },
                timeout=20,
            )
            if r.status_code != 200:
                break
            body = r.json()
            articles = body.get("articles", [])
            if not articles:
                break

            for a in articles:
                url = (a.get("url") or "").strip()
                title = (a.get("title") or "").strip()
                published = a.get("publishedAt")
                if not url or not title or not published:
                    continue

                nid = stable_id(url)
                try:
                    pub_dt = iso_to_dt(published.replace("Z", "+00:00"))
                except Exception:
                    pub_dt = datetime.now(timezone.utc)

                news_doc = {
                    "_id": nid,
                    "title": title,
                    "url": url,
                    "source": "newsapi_backfill",
                    "feed_url": "newsapi",
                    "symbols": [base],
                    "published_at": pub_dt,
                    "created_at": datetime.now(timezone.utc),
                }
                db.news.update_one({"_id": nid}, {"$setOnInsert": news_doc}, upsert=True)
                total_news += 1

                already = db.sentiments.find_one({"news_id": nid, "source": "newsapi_backfill"})
                if already:
                    continue
                sent = classify_sentiment_mode(title, use_llm=with_llm)
                db.sentiments.insert_one(
                    {
                        "news_id": nid,
                        "text": title,
                        "sentiment": sent,
                        "symbols": [base],
                        "created_at": datetime.now(timezone.utc),
                        "source": "newsapi_backfill",
                        "url": url,
                    }
                )
                total_sent += 1

            time.sleep(0.15)

    print(f"  News upserts: {total_news}, sentiment inserts: {total_sent}")


def backfill_rss_news(
    db,
    symbols: List[str],
    dt_from: datetime,
    dt_to: datetime,
    rss_urls: List[str],
    with_llm: bool,
) -> None:
    print("[BACKFILL] RSS historical/news ingestion")
    total_news = 0
    total_sent = 0
    from_utc = dt_from.astimezone(timezone.utc)
    to_utc = dt_to.astimezone(timezone.utc)

    for url in rss_urls:
        try:
            r = HTTP.get(url, timeout=20)
            if r.status_code != 200:
                print(f"  RSS skip {url}: HTTP {r.status_code}")
                continue
            root = _safe_parse_xml(r.content)
            items = root.findall(".//item")
            if not items:
                print(f"  RSS empty: {url}")
                continue

            for it in items:
                title = (it.findtext("title") or "").strip()
                link = (it.findtext("link") or "").strip()
                desc = (it.findtext("description") or "").strip()
                pub_raw = (it.findtext("pubDate") or "").strip()
                if not title or not link:
                    continue

                pub_dt = _parse_rss_pubdate(pub_raw) or datetime.now(timezone.utc)
                if pub_dt < from_utc or pub_dt > to_utc:
                    continue

                matched = _match_symbols_from_text(symbols, f"{title} {desc}")
                if not matched:
                    continue

                nid = stable_id(link)
                news_doc = {
                    "_id": nid,
                    "title": title,
                    "url": link,
                    "source": "kurzy_rss_backfill" if "kurzy.cz" in url else "rss_backfill",
                    "feed_url": url,
                    "symbols": matched,
                    "published_at": pub_dt,
                    "created_at": datetime.now(timezone.utc),
                }
                db.news.update_one({"_id": nid}, {"$setOnInsert": news_doc}, upsert=True)
                total_news += 1

                # Keep source stable for dedupe across RSS origins.
                sent_source = "rss_backfill"
                already = db.sentiments.find_one({"news_id": nid, "source": sent_source})
                if already:
                    continue
                try:
                    sent = classify_sentiment_mode(f"{title}\n{desc}", use_llm=with_llm)
                    db.sentiments.insert_one(
                        {
                            "news_id": nid,
                            "text": f"{title}\n{desc}".strip(),
                            "sentiment": sent,
                            "symbols": matched,
                            "created_at": datetime.now(timezone.utc),
                            "source": sent_source,
                            "url": link,
                        }
                    )
                    total_sent += 1
                except Exception as e:
                    # Do not fail whole feed on one bad article/timeout.
                    print(f"  RSS sentiment skip {link[:80]}...: {e}")

            time.sleep(0.15)
        except Exception as e:
            print(f"  RSS FAILED {url}: {e}")

    print(f"  RSS news upserts: {total_news}, sentiment inserts: {total_sent}")


def parse_horizons(raw: str) -> List[int]:
    out: List[int] = []
    for x in raw.split(","):
        x = x.strip()
        if not x:
            continue
        v = int(x)
        if v > 0:
            out.append(v)
    return sorted(set(out))


def _fetch_funding_rates(symbol: str, dt_from: datetime, dt_to: datetime) -> List[dict]:
    pair = symbol.replace("/", "").upper()
    start_ms = to_ms(dt_from)
    end_ms = to_ms(dt_to)
    out: List[dict] = []
    while start_ms < end_ms:
        r = HTTP.get(
            BINANCE_FUNDING_URL,
            params={
                "symbol": pair,
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1000,
            },
            timeout=20,
        )
        if r.status_code != 200:
            break
        rows = r.json()
        if not rows:
            break
        for row in rows:
            ts = datetime.fromtimestamp(int(row["fundingTime"]) / 1000, tz=timezone.utc)
            out.append(
                {
                    "timestamp": ts,
                    "funding_rate": float(row.get("fundingRate", 0.0)),
                    "mark_price": float(row.get("markPrice", 0.0)),
                }
            )
        last_ms = int(rows[-1]["fundingTime"])
        start_ms = last_ms + 1
        time.sleep(0.1)
    return out


def _fetch_mark_prices_hourly(symbol: str, dt_from: datetime, dt_to: datetime) -> dict:
    pair = symbol.replace("/", "").upper()
    start_ms = to_ms(dt_from)
    end_ms = to_ms(dt_to)
    out = {}
    while start_ms < end_ms:
        r = HTTP.get(
            BINANCE_MARK_PRICE_KLINES_URL,
            params={
                "symbol": pair,
                "interval": "1h",
                "startTime": start_ms,
                "endTime": end_ms,
                "limit": 1500,
            },
            timeout=20,
        )
        if r.status_code != 200:
            break
        rows = r.json()
        if not rows:
            break
        for row in rows:
            ts = datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc)
            out[int(ts.timestamp())] = float(row[4])  # close
        last_open_ms = int(rows[-1][0])
        start_ms = last_open_ms + 60 * 60 * 1000
        time.sleep(0.1)
    return out


def backfill_funding_oi(db, symbols: List[str], dt_from: datetime, dt_to: datetime) -> None:
    print("[BACKFILL] Funding/OI history (Binance Futures)")
    total = 0
    hard_block = False
    for sym in symbols:
        if hard_block:
            print(f"  {sym}: SKIPPED (remote endpoint appears blocked)")
            continue
        ok = False
        last_err = None
        for attempt in range(1, 4):
            try:
                rates = _fetch_funding_rates(sym, dt_from, dt_to)
                mark_by_hour = _fetch_mark_prices_hourly(sym, dt_from, dt_to)
                up = 0
                for row in rates:
                    ts = row["timestamp"]
                    hkey = int(ts.replace(minute=0, second=0, microsecond=0).timestamp())
                    doc = {
                        "symbol": sym,
                        "timestamp": ts,
                        "funding_rate": row["funding_rate"],
                        "mark_price": row["mark_price"] or mark_by_hour.get(hkey),
                        "open_interest": None,
                        "open_interest_usdt": None,
                        "source": "binance_futures_backfill",
                    }
                    db.funding_oi.update_one(
                        {"symbol": sym, "timestamp": ts},
                        {"$set": doc},
                        upsert=True,
                    )
                    up += 1
                total += up
                print(f"  {sym}: upserted {up}")
                ok = True
                break
            except Exception as e:
                last_err = e
                if "ConnectionResetError(10054" in repr(e) or "Max retries exceeded" in repr(e):
                    # Endpoint likely blocked/reset by remote side; stop hammering.
                    hard_block = True
                time.sleep(0.6 * attempt)
        if not ok:
            print(f"  {sym}: FAILED ({last_err})")
    print(f"  Funding/OI upserts total: {total}")


def _build_symbol_candle_cache(db, symbols: List[str], tf: int, dt_from: datetime, dt_to: datetime) -> dict:
    out = {}
    t_from = dt_to_iso(dt_from - timedelta(days=2))
    t_to = dt_to_iso(dt_to)
    for sym in symbols:
        rows = list(
            db.market_candles.find(
                {"symbol": sym, "tf": tf, "t": {"$gte": t_from, "$lte": t_to}},
                {"_id": 0, "t": 1, "c": 1, "v": 1},
            ).sort("t", 1)
        )
        parsed = []
        for r in rows:
            try:
                parsed.append(
                    {
                        "t": iso_to_dt(str(r["t"])),
                        "c": float(r["c"]),
                        "v": float(r.get("v", 0.0)),
                    }
                )
            except Exception:
                continue
        out[sym] = parsed
    return out


def backfill_market_intel_synthetic(
    db,
    symbols: List[str],
    tf: int,
    dt_from: datetime,
    dt_to: datetime,
    step_minutes: int = 60,
) -> None:
    print(f"[BACKFILL] Synthetic market_intel | tf={tf}m | step={step_minutes}m")
    cache = _build_symbol_candle_cache(db, symbols, tf, dt_from, dt_to)
    idx = {s: 0 for s in symbols}
    cur = dt_from.replace(minute=0, second=0, microsecond=0)
    upserts = 0

    while cur <= dt_to:
        assets = {}
        changes = []
        for sym in symbols:
            base = sym.split("/")[0].upper()
            rows = cache.get(sym, [])
            if not rows:
                assets[base] = {"outlook": "NEUTRAL", "confidence": "LOW", "reason": "No candle data"}
                continue

            i = idx[sym]
            while i + 1 < len(rows) and rows[i + 1]["t"] <= cur:
                i += 1
            idx[sym] = i

            if i <= 0 or rows[i]["t"] > cur:
                assets[base] = {"outlook": "NEUTRAL", "confidence": "LOW", "reason": "No candle at time"}
                continue

            c0 = rows[i]["c"]
            c1 = rows[i - 1]["c"]
            ret = (c0 - c1) / c1 if c1 else 0.0
            changes.append(ret)
            if ret > 0.003:
                outlook = "BULLISH"
                conf = "MEDIUM"
            elif ret < -0.003:
                outlook = "BEARISH"
                conf = "MEDIUM"
            else:
                outlook = "NEUTRAL"
                conf = "LOW"
            assets[base] = {
                "outlook": outlook,
                "confidence": conf,
                "reason": f"Synthetic from {tf}m candle return {ret*100:+.2f}%",
            }

        avg_ret = (sum(changes) / len(changes)) if changes else 0.0
        if avg_ret > 0.002:
            overall = "RISK-ON"
        elif avg_ret < -0.002:
            overall = "RISK-OFF"
        else:
            overall = "NEUTRAL"

        doc = {
            "created_at": cur,
            "assets": assets,
            "overall": overall,
            "raw": "SYNTHETIC_BACKFILL",
            "market_data": {},
            "source": "market_intel_backfill_synthetic",
        }
        db.market_intel.update_one(
            {"source": doc["source"], "created_at": cur},
            {"$set": doc},
            upsert=True,
        )
        upserts += 1
        cur += timedelta(minutes=step_minutes)

    print(f"  market_intel synthetic upserts: {upserts}")


def run_audit(db, dt_from: datetime, dt_to: datetime) -> dict:
    """Audit duplicate keys in critical collections for selected time range."""
    from_pat = dt_from.strftime("%Y-%m-%d")
    to_pat = dt_to.strftime("%Y-%m-%d")

    match_range = {
        "t": {
            "$gte": from_pat,
            "$lte": to_pat + "T99:99:99",
        }
    }
    market_total = db.market_candles.count_documents(match_range)
    market_dupes = list(db.market_candles.aggregate(
        [
            {"$match": match_range},
            {"$group": {"_id": {"symbol": "$symbol", "tf": "$tf", "t": "$t"}, "c": {"$sum": 1}}},
            {"$match": {"c": {"$gt": 1}}},
            {"$count": "n"},
        ]
    ))

    outcomes_dupes = list(db.signal_outcomes.aggregate(
        [
            {"$group": {"_id": {"run_id": "$run_id", "signal_t": "$signal_t", "symbol": "$symbol", "side": "$side"}, "c": {"$sum": 1}}},
            {"$match": {"c": {"$gt": 1}}},
            {"$count": "n"},
        ]
    ))

    news_dupes = list(db.news.aggregate(
        [
            {"$group": {"_id": "$url", "c": {"$sum": 1}}},
            {"$match": {"c": {"$gt": 1}}},
            {"$count": "n"},
        ]
    ))

    sentiments_news_dupes = list(db.sentiments.aggregate(
        [
            {
                "$match": {
                    "source": {"$in": ["news_worker", "newsapi_backfill", "rss_backfill", "kurzy_rss_backfill"]},
                    "news_id": {"$exists": True, "$ne": None},
                    "created_at": {"$gte": dt_from, "$lte": dt_to},
                }
            },
            {"$group": {"_id": {"news_id": "$news_id", "source": "$source"}, "c": {"$sum": 1}}},
            {"$match": {"c": {"$gt": 1}}},
            {"$count": "n"},
        ]
    ))

    report = {
        "range_from": dt_from.date().isoformat(),
        "range_to": dt_to.date().isoformat(),
        "market_candles_total": market_total,
        "market_candles_dupe_keys": market_dupes[0]["n"] if market_dupes else 0,
        "signal_outcomes_dupe_keys": outcomes_dupes[0]["n"] if outcomes_dupes else 0,
        "funding_oi_total": db.funding_oi.count_documents({"timestamp": {"$gte": dt_from, "$lte": dt_to}}),
        "market_intel_total": db.market_intel.count_documents({"created_at": {"$gte": dt_from, "$lte": dt_to}}),
        "news_dupe_urls": news_dupes[0]["n"] if news_dupes else 0,
        "news_sentiment_dupe_keys": sentiments_news_dupes[0]["n"] if sentiments_news_dupes else 0,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="One-time backfill to MongoDB")
    parser.add_argument("--from", dest="dt_from", default="2025-01-01", help="YYYY-MM-DD")
    parser.add_argument("--to", dest="dt_to", default=datetime.now().strftime("%Y-%m-%d"), help="YYYY-MM-DD")
    parser.add_argument("--interval", type=int, default=60, help="Candle interval in minutes")
    parser.add_argument(
        "--symbols",
        default="",
        help="Comma-separated symbols, default: auto-discover from Mongo/config",
    )
    parser.add_argument(
        "--horizons",
        default="15,60,240,1440",
        help="Signal outcome horizons in minutes",
    )
    parser.add_argument("--skip-candles", action="store_true", help="Skip candle backfill")
    parser.add_argument("--skip-outcomes", action="store_true", help="Skip signal outcomes backfill")
    parser.add_argument("--with-funding-oi", action="store_true", help="Backfill historical funding rate/mark price into funding_oi")
    parser.add_argument("--with-intel-synthetic", action="store_true", help="Backfill synthetic historical market_intel from candle data")
    parser.add_argument("--intel-step-minutes", type=int, default=60, help="Step for synthetic intel backfill (minutes)")
    parser.add_argument("--audit-only", action="store_true", help="Only run duplicate audit, no backfill")
    parser.add_argument("--newsapi-key", default="", help="Optional NewsAPI key for historical news")
    parser.add_argument("--news-max-pages", type=int, default=3, help="Max pages per symbol for NewsAPI")
    parser.add_argument(
        "--rss-url",
        action="append",
        default=[],
        help="Optional RSS feed URL (repeat for multiple feeds).",
    )
    parser.add_argument(
        "--kurzy-rss",
        action="store_true",
        help="Include default Kurzy.cz RSS feed set.",
    )
    parser.add_argument(
        "--global-rss",
        action="store_true",
        help="Include global crypto RSS sources (CoinDesk/Cointelegraph).",
    )
    parser.add_argument("--with-llm", action="store_true", help="Classify historical news sentiment with local LLM")
    args = parser.parse_args()

    ensure_indexes()
    db = get_db()

    dt_from = date_to_dt_utc(args.dt_from)
    dt_to = date_to_dt_utc(args.dt_to) + timedelta(days=1) - timedelta(seconds=1)
    if dt_to <= dt_from:
        raise ValueError("--to must be >= --from")

    symbols = parse_symbol_list(args.symbols) if args.symbols else discover_symbols(db, args.interval)
    if not symbols:
        print("No symbols discovered. Provide --symbols explicitly.")
        return 1

    print(f"Symbols: {symbols}")
    print(f"Range: {args.dt_from} .. {args.dt_to}")

    if args.audit_only:
        report = run_audit(db, dt_from, dt_to)
        print("[AUDIT]", report)
        return 0

    if not args.skip_candles:
        backfill_candles(db, symbols, args.interval, dt_from, dt_to)

    if not args.skip_outcomes:
        horizons = parse_horizons(args.horizons)
        compute_signal_outcomes(db, args.interval, horizons)

    if args.with_funding_oi:
        try:
            backfill_funding_oi(db, symbols, dt_from, dt_to)
        except Exception as e:
            print(f"[WARN] Funding/OI backfill failed globally: {e}")
    else:
        print("[INFO] Funding/OI backfill skipped (use --with-funding-oi).")

    if args.with_intel_synthetic:
        try:
            step = max(5, int(args.intel_step_minutes))
            backfill_market_intel_synthetic(db, symbols, args.interval, dt_from, dt_to, step_minutes=step)
        except Exception as e:
            print(f"[WARN] Synthetic market_intel backfill failed: {e}")
    else:
        print("[INFO] Synthetic market_intel backfill skipped (use --with-intel-synthetic).")

    if args.newsapi_key:
        try:
            backfill_news(
                db=db,
                symbols=symbols,
                dt_from=dt_from,
                dt_to=dt_to,
                api_key=args.newsapi_key,
                with_llm=args.with_llm,
                max_pages=max(1, args.news_max_pages),
            )
        except Exception as e:
            print(f"[WARN] NewsAPI backfill failed: {e}")
    else:
        print("[INFO] News backfill skipped (no --newsapi-key provided).")

    rss_urls = list(args.rss_url or [])
    if args.kurzy_rss:
        rss_urls.extend(DEFAULT_KURZY_RSS)
    if args.global_rss:
        rss_urls.extend(DEFAULT_GLOBAL_CRYPTO_RSS)
    rss_urls = sorted(set(u.strip() for u in rss_urls if u and u.strip()))
    if rss_urls:
        try:
            backfill_rss_news(
                db=db,
                symbols=symbols,
                dt_from=dt_from,
                dt_to=dt_to,
                rss_urls=rss_urls,
                with_llm=args.with_llm,
            )
        except Exception as e:
            print(f"[WARN] RSS backfill failed globally: {e}")
    else:
        print("[INFO] RSS backfill skipped (no --rss-url / --kurzy-rss provided).")

    report = run_audit(db, dt_from, dt_to)
    print("[AUDIT]", report)
    print("DONE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
