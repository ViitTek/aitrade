from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import joblib
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction import DictVectorizer
from sklearn.metrics import roc_auc_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from trading.config import settings
from trading.mongo import get_recent_sentiment, get_latest_funding_oi, get_latest_intel


MODEL_PATH = Path(__file__).resolve().parent / "signal_quality_model.joblib"
MODEL_META_PATH = Path(__file__).resolve().parent / "signal_quality_model_meta.json"

_MODEL_CACHE = {"model": None, "loaded_at": None}


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def _parse_iso_utc(ts: str) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _sentiment_to_num(s: Optional[str]) -> float:
    x = (s or "").strip().lower()
    if x.startswith("pos"):
        return 1.0
    if x.startswith("neg"):
        return -1.0
    if x.startswith("neu"):
        return 0.0
    return 0.0


def _build_feature_row(db, symbol: str, side: str, t_dt: datetime) -> Dict[str, Any]:
    hour = int(t_dt.hour)
    dow = int(t_dt.weekday())
    sentiment = get_recent_sentiment(
        db,
        symbol,
        window_minutes=max(30, int(settings.SENTIMENT_WINDOW_MINUTES)),
        min_articles=1,
        no_data_action="pass",
        as_of=t_dt,
    )
    foi = get_latest_funding_oi(db, symbol, as_of=t_dt)
    intel = get_latest_intel(db, symbol, as_of=t_dt)
    fr = _safe_float((foi or {}).get("funding_rate"), 0.0)
    oi_usdt = _safe_float((foi or {}).get("open_interest_usdt"), 0.0)
    intel_conf = str((intel or {}).get("confidence", "LOW")).upper()
    intel_outlook = str((intel or {}).get("outlook", "NEUTRAL")).upper()
    intel_overall = str((intel or {}).get("overall", "NEUTRAL")).upper()
    return {
        "symbol": symbol,
        "side": side,
        "hour": hour,
        "dow": dow,
        "funding_rate": fr,
        "open_interest_usdt": oi_usdt,
        "sentiment_num": _sentiment_to_num(sentiment),
        "intel_confidence": intel_conf,
        "intel_outlook": intel_outlook,
        "intel_overall": intel_overall,
    }


def train_signal_quality_model(
    db,
    lookback_days: Optional[int] = None,
    horizon_min: Optional[int] = None,
    min_samples: Optional[int] = None,
) -> Dict[str, Any]:
    lookback_days = int(lookback_days or settings.SIGNAL_QUALITY_LOOKBACK_DAYS)
    horizon_min = int(horizon_min or settings.SIGNAL_QUALITY_HORIZON_MIN)
    min_samples = int(min_samples or settings.SIGNAL_QUALITY_MIN_SAMPLES)
    cutoff = datetime.now(timezone.utc) - timedelta(days=max(30, lookback_days))
    key_ret = f"ret_{horizon_min}m"

    cur = db.signal_outcomes.find(
        {
            "signal_t": {"$exists": True},
            key_ret: {"$exists": True, "$ne": None},
            "updated_at": {"$gte": cutoff},
        },
        {
            "_id": 0,
            "signal_t": 1,
            "symbol": 1,
            "side": 1,
            key_ret: 1,
        },
    )

    X_rows = []
    y = []
    n_total = 0
    for row in cur:
        n_total += 1
        sym = str(row.get("symbol") or "").strip()
        side = str(row.get("side") or "").strip().upper()
        if not sym or side not in {"BUY", "SELL"}:
            continue
        t_dt = _parse_iso_utc(str(row.get("signal_t") or ""))
        if t_dt is None:
            continue
        ret = _safe_float(row.get(key_ret), 0.0)
        label = 1 if ret > 0 else 0
        X_rows.append(_build_feature_row(db, sym, side, t_dt))
        y.append(label)

    if len(X_rows) < min_samples:
        return {
            "ok": False,
            "reason": "not_enough_samples",
            "samples": len(X_rows),
            "min_samples": min_samples,
            "rows_scanned": n_total,
            "horizon_min": horizon_min,
        }

    X_train, X_test, y_train, y_test = train_test_split(
        X_rows, y, test_size=0.25, random_state=42, stratify=y
    )
    model = Pipeline(
        steps=[
            ("vec", DictVectorizer(sparse=False)),
            (
                "clf",
                RandomForestClassifier(
                    n_estimators=300,
                    max_depth=10,
                    min_samples_leaf=5,
                    random_state=42,
                    n_jobs=-1,
                ),
            ),
        ]
    )
    model.fit(X_train, y_train)
    proba = model.predict_proba(X_test)[:, 1]
    auc = float(roc_auc_score(y_test, proba)) if len(set(y_test)) > 1 else 0.5
    acc = float(((proba >= 0.5).astype(int) == y_test).mean())

    artifact = {
        "model": model,
        "meta": {
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "lookback_days": lookback_days,
            "horizon_min": horizon_min,
            "samples": len(X_rows),
            "rows_scanned": n_total,
            "auc": round(auc, 4),
            "accuracy": round(acc, 4),
            "positive_rate": round(float(sum(y)) / float(len(y)), 4),
        },
    }
    joblib.dump(artifact, MODEL_PATH)
    _MODEL_CACHE["model"] = artifact
    _MODEL_CACHE["loaded_at"] = datetime.now(timezone.utc)

    # keep metadata in Mongo for dashboard/API audit
    db.signal_quality_models.insert_one(artifact["meta"] | {"source": "sklearn_rf_v1"})
    return {"ok": True, **artifact["meta"]}


def load_latest_model() -> Optional[Dict[str, Any]]:
    if _MODEL_CACHE.get("model") is not None:
        return _MODEL_CACHE["model"]
    if not MODEL_PATH.exists():
        return None
    try:
        artifact = joblib.load(MODEL_PATH)
        _MODEL_CACHE["model"] = artifact
        _MODEL_CACHE["loaded_at"] = datetime.now(timezone.utc)
        return artifact
    except Exception:
        return None


def score_signal_quality(db, symbol: str, side: str, as_of: Optional[datetime] = None) -> Dict[str, Any]:
    artifact = load_latest_model()
    if not artifact:
        return {"ok": False, "reason": "model_not_found", "prob": None}
    model = artifact.get("model")
    meta = artifact.get("meta", {})
    t_dt = as_of.astimezone(timezone.utc) if as_of is not None else datetime.now(timezone.utc)
    x = _build_feature_row(db, symbol, side, t_dt)
    try:
        prob = float(model.predict_proba([x])[0][1])
    except Exception as e:
        return {"ok": False, "reason": f"predict_failed: {e}", "prob": None}
    return {
        "ok": True,
        "prob": round(prob, 6),
        "meta": meta,
        "features": {
            "symbol": x["symbol"],
            "side": x["side"],
            "hour": x["hour"],
            "dow": x["dow"],
            "sentiment_num": x["sentiment_num"],
            "funding_rate": round(float(x["funding_rate"]), 8),
            "open_interest_usdt": round(float(x["open_interest_usdt"]), 2),
            "intel_outlook": x["intel_outlook"],
            "intel_confidence": x["intel_confidence"],
            "intel_overall": x["intel_overall"],
        },
    }

