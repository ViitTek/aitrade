# trading/mongo.py
from datetime import datetime, timezone, timedelta
from typing import Optional
from collections import Counter
from pymongo import MongoClient, ASCENDING
from trading.config import settings

_client = None

def get_db():
    global _client
    if _client is None:
        # Return timezone-aware UTC datetimes from MongoDB documents.
        _client = MongoClient(settings.MONGO_URI, tz_aware=True)
    return _client[settings.MONGO_DB]


def _as_utc_aware(dt: datetime) -> datetime:
    """Normalize datetime to timezone-aware UTC for safe arithmetic."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _asof_utc(as_of: Optional[datetime]) -> datetime:
    if as_of is None:
        return datetime.now(timezone.utc)
    return _as_utc_aware(as_of)

def ensure_indexes():
    db = get_db()
    db.market_candles.create_index([("symbol", ASCENDING), ("tf", ASCENDING), ("t", ASCENDING)], unique=True)
    db.bot_events.create_index([("run_id", ASCENDING), ("t", ASCENDING)])
    db.equity.create_index([("run_id", ASCENDING), ("t", ASCENDING)])
    db.trades.create_index([("run_id", ASCENDING), ("t_exit", ASCENDING)])
    db.orders.create_index([("run_id", ASCENDING), ("t", ASCENDING)])
    db.sentiments.create_index([("symbols", ASCENDING), ("created_at", ASCENDING)])
    db.market_intel.create_index([("created_at", ASCENDING)])
    db.bot_signals.create_index([("run_id", ASCENDING), ("t", ASCENDING)])
    db.asset_recommendations.create_index([("created_at", -1)])
    db.funding_oi.create_index([("symbol", ASCENDING), ("timestamp", -1)])
    db.market_metrics.create_index([("timestamp", -1)])
    db.bot_runtime_state.create_index([("run_id", ASCENDING), ("symbol", ASCENDING), ("tf", ASCENDING)], unique=True)
    db.signal_quality_models.create_index([("trained_at", -1)])


def get_recent_sentiment(
    db,
    symbol: str,
    window_minutes: int = 60,
    min_articles: int = 1,
    no_data_action: str = "pass",
    as_of: Optional[datetime] = None,
) -> Optional[str]:
    """Zjistí převládající sentiment pro daný symbol za posledních N minut.

    Vrací "Positive", "Negative", "Neutral" nebo None (pass-through).
    """
    # Extrahuj base symbol z páru: "BTC/USDT" -> "BTC"
    base = symbol.split("/")[0].upper()

    now_utc = _asof_utc(as_of)
    cutoff = now_utc - timedelta(minutes=window_minutes)

    docs = list(db.sentiments.find(
        {"symbols": base, "created_at": {"$gte": cutoff, "$lte": now_utc}},
        {"sentiment": 1},
    ))

    if len(docs) < min_articles:
        if no_data_action == "block":
            return "Neutral"
        return None  # pass-through

    # Majority vote
    counts = Counter(d["sentiment"] for d in docs)
    winner = counts.most_common(1)[0][0]
    return winner


def get_latest_intel(db, symbol: str = None, as_of: Optional[datetime] = None) -> Optional[dict]:
    """Vrátí nejnovější market intelligence dokument.
    Pokud je zadán symbol, vrátí asset-level outlook pro daný symbol.
    """
    now_utc = _asof_utc(as_of)
    doc = db.market_intel.find_one({"created_at": {"$lte": now_utc}}, sort=[("created_at", -1)])
    if not doc:
        return None

    if symbol:
        base = symbol.split("/")[0].upper()
        asset_intel = doc.get("assets", {}).get(base)
        if asset_intel:
            asset_intel["overall"] = doc.get("overall", "NEUTRAL")
            created_at = _as_utc_aware(doc["created_at"])
            age = (now_utc - created_at).total_seconds() / 60
            asset_intel["intel_age_minutes"] = age
        return asset_intel

    return doc


def get_latest_funding_oi(db, symbol: str, as_of: Optional[datetime] = None) -> Optional[dict]:
    """Vrátí nejnovější funding rate + OI záznam pro daný symbol."""
    now_utc = _asof_utc(as_of)
    doc = db.funding_oi.find_one({"symbol": symbol, "timestamp": {"$lte": now_utc}}, sort=[("timestamp", -1)])
    if not doc:
        return None
    timestamp = _as_utc_aware(doc["timestamp"])
    age = (now_utc - timestamp).total_seconds() / 60
    return {
        "funding_rate": doc.get("funding_rate"),
        "open_interest": doc.get("open_interest"),
        "open_interest_usdt": doc.get("open_interest_usdt"),
        "mark_price": doc.get("mark_price"),
        "timestamp": timestamp,
        "age_minutes": age,
    }
