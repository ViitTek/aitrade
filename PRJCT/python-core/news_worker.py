import argparse
import time
from datetime import datetime, timezone
from typing import List, Dict, Optional
import hashlib
import requests
import feedparser
from pymongo import MongoClient
from dateutil import parser as dtparser

from llama_wrapper import run_llama_oneword
from trading.config import settings

MONGO_URI = settings.MONGO_URI
DB_NAME = settings.MONGO_DB

# Krypto RSS feedy mapované na symboly
CRYPTO_FEEDS = {
    "BTC": [
        "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=ptEkonomika",
        "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=ptKomodity",
        "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=wzMeny",
        "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=wzMakro",
    ],
    "ETH": [
        "https://cointelegraph.com/rss/tag/ethereum",
    ],
    "SOL": [
        "https://www.kurzy.cz/zpravy/util/forext.dat?type=rss&col=wzAkcieSvet",
    ],
}

# Keyword mapping pro detekci symbolů z titulku
SYMBOL_KEYWORDS = {
    "BTC": ["bitcoin", "btc", "xbt", "krypto", "kryptomena", "kryptomeny"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana", "sol"],
    "XRP": ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],
    "BNB": ["bnb", "binance coin"],
    "TRX": ["trx", "tron"],
    "PAXG": ["paxg", "zlato", "gold"],
    "USDC": ["usdc", "stablecoin"],
}

HTTP_TIMEOUT_SEC = 8
MAX_ITEMS_PER_FEED = 8
SENTIMENT_TIMEOUT_SEC = 25
SENTIMENT_MAX_NEW = 10
POLL_INTERVAL_SEC = 300


def stable_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()


def parse_datetime(entry) -> datetime:
    if getattr(entry, "published_parsed", None):
        return datetime(*entry.published_parsed[:6], tzinfo=timezone.utc)
    published = entry.get("published") or entry.get("updated")
    if published:
        try:
            dt = dtparser.parse(published)
            return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return datetime.now(timezone.utc)


def detect_symbols(title: str, feed_symbol: str) -> List[str]:
    """Detekuje symboly z titulku článku. Feed symbol je vždy zahrnut."""
    symbols = {feed_symbol}
    title_lower = title.lower()
    for sym, keywords in SYMBOL_KEYWORDS.items():
        if any(kw in title_lower for kw in keywords):
            symbols.add(sym)
    return sorted(symbols)


def fetch_feed(feed_url: str) -> feedparser.FeedParserDict:
    r = requests.get(feed_url, timeout=HTTP_TIMEOUT_SEC, headers={"User-Agent": "AIInvest/0.1"})
    r.raise_for_status()
    return feedparser.parse(r.content)


def classify_sentiment(title: str) -> str:
    prompt = (
        "Return exactly ONE word: Positive, Neutral, or Negative.\n"
        f"Text: {title}\n"
        "Answer:"
    )
    return run_llama_oneword(prompt, timeout_sec=SENTIMENT_TIMEOUT_SEC)


def run_once():
    cycle_started = datetime.now(timezone.utc)
    print(f"[NEWS] cycle start {cycle_started.isoformat()}")
    mongo = MongoClient(MONGO_URI, serverSelectionTimeoutMS=2000)
    db = mongo[DB_NAME]
    news = db["news"]
    sentiments = db["sentiments"]

    inserted = 0
    analyzed = 0
    inserted_by_source: Dict[str, int] = {}
    analyzed_by_source: Dict[str, int] = {}

    # 1) sesbírat nové zprávy
    new_items: List[Dict] = []

    for feed_symbol, feed_urls in CRYPTO_FEEDS.items():
        for feed_url in feed_urls:
            try:
                d = fetch_feed(feed_url)
            except Exception as e:
                print(f"[WARN] Feed fetch failed: {feed_url} -> {e}")
                continue

            source = d.feed.get("title", "rss")

            for entry in d.entries[:MAX_ITEMS_PER_FEED]:
                url = (entry.get("link") or "").strip()
                title = (entry.get("title") or "").strip()
                title = title.encode("latin1", "ignore").decode("utf-8", "ignore") if "\u00e2" in title else title
                if not url or not title:
                    continue

                _id = stable_id(url)
                symbols = detect_symbols(title, feed_symbol)
                item = {
                    "_id": _id,
                    "title": title,
                    "url": url,
                    "source": source,
                    "feed_url": feed_url,
                    "symbols": symbols,
                    "published_at": parse_datetime(entry),
                    "created_at": datetime.now(timezone.utc),
                }

                res = news.update_one({"_id": _id}, {"$setOnInsert": item}, upsert=True)
                if res.upserted_id:
                    inserted += 1
                    src_key = f"{source} | {feed_url}"
                    inserted_by_source[src_key] = inserted_by_source.get(src_key, 0) + 1
                    new_items.append(item)

    # 2) sentiment jen pro omezený počet nových věcí
    for item in new_items[:SENTIMENT_MAX_NEW]:
        already = sentiments.find_one({"news_id": item["_id"], "source": "news_worker"})
        if already:
            continue

        s = classify_sentiment(item["title"])
        sentiments.insert_one({
            "news_id": item["_id"],
            "text": item["title"],
            "sentiment": s,
            "symbols": item["symbols"],
            "created_at": datetime.now(timezone.utc),
            "source": "news_worker",
            "url": item["url"],
        })
        analyzed += 1
        src_key = f"{item.get('source', 'rss')} | {item.get('feed_url', '')}"
        analyzed_by_source[src_key] = analyzed_by_source.get(src_key, 0) + 1

    print(f"[NEWS] Inserted news: {inserted}, analyzed: {analyzed}")
    if inserted_by_source:
        parts = [f"{k}: {v}" for k, v in sorted(inserted_by_source.items(), key=lambda x: x[0])]
        print(f"[NEWS] Inserted by source: {'; '.join(parts)}")
    if analyzed_by_source:
        parts = [f"{k}: {v}" for k, v in sorted(analyzed_by_source.items(), key=lambda x: x[0])]
        print(f"[NEWS] Analyzed by source: {'; '.join(parts)}")

    if new_items:
        preview = " | ".join(
            f"{it.get('symbols', ['?'])[0]}: {str(it.get('title', ''))[:80]}"
            for it in new_items[:3]
        )
        print(f"[NEWS] New headlines preview: {preview}")
    else:
        latest = list(news.find({}, {"title": 1, "symbols": 1}).sort("created_at", -1).limit(2))
        if latest:
            preview = " | ".join(
                f"{(it.get('symbols') or ['?'])[0]}: {str(it.get('title', ''))[:80]}"
                for it in latest
            )
            print(f"[NEWS] No new items. Latest known: {preview}")

    cycle_ended = datetime.now(timezone.utc)
    took = (cycle_ended - cycle_started).total_seconds()
    print(f"[NEWS] cycle ok {cycle_ended.isoformat()} duration={took:.2f}s")


def main():
    parser = argparse.ArgumentParser(description="AIInvest News Sentiment Worker")
    parser.add_argument("--once", action="store_true", help="Run once and exit")
    parser.add_argument(
        "--interval",
        type=int,
        default=POLL_INTERVAL_SEC,
        help=f"Polling interval in seconds (default: {POLL_INTERVAL_SEC})",
    )
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[WARN] News worker cycle failed: {e}")
        time.sleep(max(30, args.interval))


if __name__ == "__main__":
    main()
