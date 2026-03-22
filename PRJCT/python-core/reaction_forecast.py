from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict


def _mean(xs):
    if not xs:
        return 0.0
    return float(sum(xs)) / float(len(xs))


def _safe_float(v):
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


def forecast_symbol_reaction(db, symbol: str, lookback_days: int = 120, sentiment_window_minutes: int = 180) -> Dict:
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(7, int(lookback_days)))

    rows = list(
        db.signal_outcomes.find(
            {
                "symbol": symbol,
                "updated_at": {"$gte": cutoff},
            },
            {
                "_id": 0,
                "ret_60m": 1,
                "ret_240m": 1,
                "ret_1440m": 1,
                "side": 1,
            },
        )
    )

    r60 = [_safe_float(r.get("ret_60m")) for r in rows]
    r240 = [_safe_float(r.get("ret_240m")) for r in rows]
    r1440 = [_safe_float(r.get("ret_1440m")) for r in rows]
    r60 = [x for x in r60 if x is not None]
    r240 = [x for x in r240 if x is not None]
    r1440 = [x for x in r1440 if x is not None]

    def hit_rate(xs):
        if not xs:
            return 0.0
        return sum(1 for x in xs if x > 0) / len(xs)

    base = symbol.split("/")[0].upper()
    s_cut = datetime.now(timezone.utc) - timedelta(minutes=max(30, int(sentiment_window_minutes)))
    sent_docs = list(
        db.sentiments.find(
            {"symbols": base, "created_at": {"$gte": s_cut}},
            {"_id": 0, "sentiment": 1},
        )
    )
    counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
    for d in sent_docs:
        s = str(d.get("sentiment", "Neutral"))
        if s in counts:
            counts[s] += 1
    dominant = max(counts, key=counts.get) if sum(counts.values()) > 0 else "Neutral"

    mean60 = _mean(r60)
    mean240 = _mean(r240)
    mean1440 = _mean(r1440)
    hr60 = hit_rate(r60)

    score = (mean60 * 0.45) + (mean240 * 0.35) + (mean1440 * 0.20)
    if dominant == "Positive":
        score += 0.0015
    elif dominant == "Negative":
        score -= 0.0015

    if score > 0.003:
        outlook = "BULLISH"
    elif score < -0.003:
        outlook = "BEARISH"
    else:
        outlook = "NEUTRAL"

    n = len(rows)
    if n >= 120:
        confidence = "HIGH"
    elif n >= 40:
        confidence = "MEDIUM"
    else:
        confidence = "LOW"

    return {
        "symbol": symbol,
        "lookback_days": lookback_days,
        "sample_size": n,
        "outlook": outlook,
        "confidence": confidence,
        "score": round(score, 6),
        "metrics": {
            "mean_ret_60m": round(mean60, 6),
            "mean_ret_240m": round(mean240, 6),
            "mean_ret_1440m": round(mean1440, 6),
            "hit_rate_60m": round(hr60, 4),
        },
        "sentiment": {
            "window_minutes": sentiment_window_minutes,
            "dominant": dominant,
            "counts": counts,
        },
    }
