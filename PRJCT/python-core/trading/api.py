# trading/api.py
import asyncio
import uuid
import math
import json
import time
import os
import traceback
import hmac
import hashlib
import base64
from urllib.parse import urlencode
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
import requests

from trading.mongo import ensure_indexes, get_db
from trading.engine import TradingEngine
from trading.kraken_ws import KrakenWS
from trading.binance_ws import BinanceWS
from trading.backtest import BacktestRunner, MultiBacktestRunner
from trading.config import settings
from trading.ibkr_client import get_ibkr_status
from reaction_forecast import forecast_symbol_reaction
from signal_quality import train_signal_quality_model, score_signal_quality
import news_worker
import market_intel_worker
import market_data_worker
import config_optimizer_worker
import cross_asset_shadow_worker
from config_optimizer import apply_overrides, passes_apply_guard


class BacktestRequest(BaseModel):
    source: str = "mongo"              # "mongo" | "kraken" | "binance"
    symbol: str = "ALL"                # single symbol or "ALL" for multi-symbol
    dt_from: str                       # "YYYY-MM-DD"
    dt_to: Optional[str] = None        # "YYYY-MM-DD", default: now
    initial_equity: float = 1000.0     # backtest initial equity
    interval: int = 5
    with_sentiment: bool = False
    mode: str = "exact"                # "exact" | "vectorized_fast"
    overrides: Optional[dict] = None   # parameter overrides: {"BREAKOUT_N": 15, ...}

router = APIRouter(prefix="/bot", tags=["bot"])

_state = {
    "running": False,
    "run_id": None,
    "ws_kraken": None,
    "ws_binance": None,
    "news_task": None,
    "intel_task": None,
    "market_data_task": None,
    "cross_asset_task": None,
    "tune_task": None,
    "last_stopped_at": None,
    "last_stopped_reason": None,
}

# Lightweight response cache for hot dashboard endpoints.
_endpoint_cache: dict[str, tuple[float, object]] = {}


def _cache_get(key: str):
    row = _endpoint_cache.get(key)
    if not row:
        return None
    exp, payload = row
    if time.time() >= exp:
        _endpoint_cache.pop(key, None)
        return None
    return payload


def _cache_put(key: str, payload, ttl_sec: float):
    _endpoint_cache[key] = (time.time() + max(0.1, float(ttl_sec)), payload)
    return payload


def _cache_key(name: str, *parts) -> str:
    return f"{name}::" + "::".join(str(p) for p in parts)

_PERSISTED_DEFAULTS_PATH = Path(__file__).resolve().parents[1] / "config_defaults.json"
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_PRESETS_DIR = _PROJECT_ROOT / "dashboard" / "public" / "config-presets"
_LOCAL_ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
_PERSIST_EXCLUDED_KEYS = {"KRAKEN_API_KEY", "KRAKEN_API_SECRET", "BINANCE_API_KEY", "BINANCE_API_SECRET"}




class ExchangeCredentialsUpdate(BaseModel):
    exchange: str
    api_key: str
    api_secret: str


class SignalQualityTrainRequest(BaseModel):
    lookback_days: Optional[int] = None
    horizon_min: Optional[int] = None
    min_samples: Optional[int] = None


def _parse_env_file(path: Path) -> dict:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            s = line.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            out[k.strip()] = v.strip().strip("'").strip('"')
    except Exception:
        return out
    return out


def _load_credentials_from_env() -> dict:
    file_vars = _parse_env_file(_LOCAL_ENV_PATH)
    def _get(name: str) -> str:
        return str(os.getenv(name) or file_vars.get(name) or "").strip()

    settings.KRAKEN_API_KEY = _get("KRAKEN_API_KEY")
    settings.KRAKEN_API_SECRET = _get("KRAKEN_API_SECRET")
    settings.BINANCE_API_KEY = _get("BINANCE_API_KEY")
    settings.BINANCE_API_SECRET = _get("BINANCE_API_SECRET")
    return {
        "kraken_configured": bool(settings.KRAKEN_API_KEY and settings.KRAKEN_API_SECRET),
        "binance_configured": bool(settings.BINANCE_API_KEY and settings.BINANCE_API_SECRET),
        "env_path": str(_LOCAL_ENV_PATH),
    }


def _coerce_config_value(key: str, value):
    field = settings.model_fields.get(key)
    if field is None:
        return value
    ann = field.annotation
    current = getattr(settings, key, None)
    try:
        if ann is bool:
            if value is None:
                return bool(current) if current is not None else False
            if isinstance(value, str):
                return value.strip().lower() in {"1", "true", "yes", "on"}
            return bool(value)
        if ann is int:
            return _safe_int(value, _safe_int(current, 0))
        if ann is float:
            return _safe_float(value, _safe_float(current, 0.0))
        if ann is str:
            if value is None:
                return str(current or "")
            return str(value)
    except Exception:
        return current if current is not None else value
    return value


def _safe_int(value, default: int) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def _safe_float(value, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _field_default(key: str, fallback):
    try:
        field = settings.model_fields.get(key)
        if field is None:
            return fallback
        default = field.default
        return fallback if default is None else default
    except Exception:
        return fallback


_RUNTIME_INT_DEFAULTS = {
    key: _safe_int(_field_default(key, fallback), fallback)
    for key, fallback in {
        "INTERVAL_MINUTES": 60,
        "BREAKOUT_N": 7,
        "EMA_PERIOD": 50,
        "COOLDOWN_CANDLES": 1,
        "ENGINE_BUFFER_MAXLEN": 1000,
        "ENGINE_SEED_CANDLES": 800,
        "SENTIMENT_WINDOW_MINUTES": 60,
        "SENTIMENT_MIN_ARTICLES": 1,
        "INTEL_POLL_SECONDS": 900,
        "INTEL_MAX_AGE_MINUTES": 120,
        "MAX_DYNAMIC_SYMBOLS": 6,
        "SYMBOL_WARMUP_CANDLES": 50,
        "RECOMMENDATION_MAX_AGE_MINUTES": 180,
        "FUNDING_POLL_SECONDS": 300,
        "FUNDING_MAX_AGE_MINUTES": 60,
        "OI_POLL_SECONDS": 300,
        "OI_MAX_AGE_MINUTES": 60,
        "MARKET_DATA_POLL_SECONDS": 300,
        "AUTO_TUNE_INTERVAL_SECONDS": 21600,
        "AUTO_TUNE_LOOKBACK_DAYS": 60,
        "AUTO_TUNE_MAX_EVALS": 24,
        "AUTO_TUNE_MIN_TRADES": 20,
        "SIGNAL_QUALITY_SHADOW_HORIZON_MIN": 60,
        "IBKR_TWS_PORT": 4002,
        "IBKR_CLIENT_ID": 77,
    }.items()
}

_RUNTIME_FLOAT_DEFAULTS = {
    key: _safe_float(_field_default(key, fallback), fallback)
    for key, fallback in {
        "RISK_PER_TRADE": 0.003,
        "DAILY_STOP": 0.02,
        "PROFIT_SPLIT_REINVEST": 0.6,
        "PF_GUARD_SOFT_THRESHOLD": 1.05,
        "PF_GUARD_HARD_THRESHOLD": 0.90,
        "PF_GUARD_SOFT_RISK_MULT": 0.5,
        "PF_GUARD_HARD_RISK_MULT": 0.0,
        "VOL_MULT": 1.3,
        "FEE_RATE": 0.0010,
        "SPREAD_BPS": 2.0,
        "SL_ATR_MULT": 1.2,
        "TP_ATR_MULT": 3.0,
        "TIME_EXIT_MINUTES": 1440.0,
        "TRAIL_ATR_MULT": 1.0,
        "TRAIL_ACTIVATION_ATR": 2.0,
        "LLM_DEGRADED_RISK_MULT": 0.35,
        "MIN_MARKET_CAP_USD": 1_000_000_000.0,
        "MIN_VOLUME_24H_USD": 50_000_000.0,
        "FUNDING_BLOCK_THRESHOLD": 0.01,
        "OI_CHANGE_THRESHOLD": 0.10,
        "AUTO_TUNE_MIN_WIN_RATE": 0.50,
        "AUTO_TUNE_MIN_PROFIT_FACTOR": 1.0,
        "AUTO_TUNE_MIN_FINAL_EQUITY": 1000.0,
        "FEE_RATE_IBKR_FX": 0.00002,
        "FEE_RATE_IBKR_FUTURES": 0.00008,
        "FEE_RATE_IBKR_STOCKS": 0.00005,
    }.items()
}

_RUNTIME_BOOL_DEFAULTS = {
    key: bool(_field_default(key, fallback))
    for key, fallback in {
        "TRADING_BINANCE_ENABLED": True,
        "TRADING_IBKR_ENABLED": False,
        "EXPAND_UNIVERSE_FROM_RECOMMENDATIONS": False,
        "PF_GUARD_ENABLED": True,
        "PF_GUARD_NON_CRYPTO_ENABLED": False,
        "VOL_FILTER": True,
        "TRAILING_STOP": True,
        "SENTIMENT_ENABLED": False,
        "INTEL_ENABLED": False,
        "INTEL_BLOCK_LOW_CONF": False,
        "DYNAMIC_ASSETS_ENABLED": False,
        "FUNDING_ENABLED": True,
        "OI_ENABLED": False,
        "AUTO_TUNE_ENABLED": False,
        "AUTO_TUNE_APPLY": False,
        "RESUME_ON_START": True,
        "IBKR_READONLY_API": True,
        "CROSS_ASSET_SHADOW_ENABLED": False,
        "SIGNAL_QUALITY_ENABLED": False,
        "SHADOW_MODE_ENABLED": False,
        "NEWS_WORKER_ENABLED": True,
        "MARKET_DATA_WORKER_ENABLED": True,
    }.items()
}


def _normalize_runtime_value(key: str, value):
    key_upper = str(key).upper()
    if key_upper in _RUNTIME_INT_DEFAULTS:
        normalized = _safe_int(_coerce_config_value(key_upper, value), _RUNTIME_INT_DEFAULTS[key_upper])
        if key_upper == "SIGNAL_QUALITY_SHADOW_HORIZON_MIN" and normalized < 1:
            return _RUNTIME_INT_DEFAULTS[key_upper]
        return normalized
    if key_upper in _RUNTIME_FLOAT_DEFAULTS:
        normalized = _safe_float(_coerce_config_value(key_upper, value), _RUNTIME_FLOAT_DEFAULTS[key_upper])
        if key_upper == "TIME_EXIT_MINUTES" and normalized < 1.0:
            return _RUNTIME_FLOAT_DEFAULTS[key_upper]
        return normalized
    if key_upper in _RUNTIME_BOOL_DEFAULTS:
        if value is None:
            return _RUNTIME_BOOL_DEFAULTS[key_upper]
        coerced = _coerce_config_value(key_upper, value)
        return _RUNTIME_BOOL_DEFAULTS[key_upper] if coerced is None else bool(coerced)
    return _coerce_config_value(key_upper, value)


def _normalize_config_payload(payload: dict | None, include_private: bool = False) -> dict:
    data = dict(payload or {})
    allowed = set(settings.model_fields.keys())
    if not include_private:
        allowed -= _PERSIST_EXCLUDED_KEYS
    normalized = {}
    for key, value in data.items():
        key_upper = str(key).upper()
        if key_upper not in allowed:
            continue
        normalized[key_upper] = _normalize_runtime_value(key_upper, value)
    return normalized


def _sanitize_runtime_settings():
    """Normalize runtime settings to safe numeric/bool values and enforce stable defaults."""
    for key in _RUNTIME_INT_DEFAULTS:
        try:
            setattr(settings, key, _normalize_runtime_value(key, getattr(settings, key, None)))
        except Exception:
            pass
    for key in _RUNTIME_FLOAT_DEFAULTS:
        try:
            setattr(settings, key, _normalize_runtime_value(key, getattr(settings, key, None)))
        except Exception:
            pass
    for key in _RUNTIME_BOOL_DEFAULTS:
        try:
            setattr(settings, key, _normalize_runtime_value(key, getattr(settings, key, None)))
        except Exception:
            pass


def _get_persistable_config() -> dict:
    return _normalize_config_payload(settings.model_dump(), include_private=False)


def _get_public_config() -> dict:
    return _normalize_config_payload(settings.model_dump(), include_private=False)


def _kraken_sign(path: str, data: dict, api_secret: str) -> str:
    post_data = urlencode(data)
    encoded = (str(data["nonce"]) + post_data).encode("utf-8")
    message = path.encode("utf-8") + hashlib.sha256(encoded).digest()
    secret = base64.b64decode(api_secret)
    sig = hmac.new(secret, message, hashlib.sha512)
    return base64.b64encode(sig.digest()).decode()


def _test_kraken_private(api_key: str, api_secret: str) -> tuple[bool, str]:
    try:
        nonce = str(int(time.time() * 1000))
        path = "/0/private/Balance"
        payload = {"nonce": nonce}
        headers = {
            "API-Key": api_key,
            "API-Sign": _kraken_sign(path, payload, api_secret),
        }
        resp = requests.post(
            "https://api.kraken.com" + path,
            data=payload,
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}"
        errs = data.get("error") or []
        if errs:
            return False, "; ".join(str(e) for e in errs)
        return True, "OK"
    except Exception as e:
        return False, str(e)


def _test_binance_private(api_key: str, api_secret: str) -> tuple[bool, str]:
    try:
        # Use Binance server time to avoid local clock drift issues.
        t_resp = requests.get("https://api.binance.com/api/v3/time", timeout=10)
        t_data = t_resp.json() if t_resp.status_code == 200 else {}
        srv_ts = int(t_data.get("serverTime") or 0)
        if srv_ts <= 0:
            srv_ts = int(time.time() * 1000)

        query = urlencode({"timestamp": srv_ts, "recvWindow": 15000})
        sig = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"https://api.binance.com/api/v3/account?{query}&signature={sig}"
        resp = requests.get(url, headers={"X-MBX-APIKEY": api_key}, timeout=10)
        data = resp.json()
        if resp.status_code != 200:
            msg = data.get("msg") if isinstance(data, dict) else str(data)
            return False, f"HTTP {resp.status_code}: {msg}"
        if not isinstance(data, dict) or "balances" not in data:
            return False, "Unexpected response"
        return True, "OK"
    except Exception as e:
        return False, str(e)


def _get_kraken_account_snapshot(api_key: str, api_secret: str) -> tuple[bool, str, dict]:
    try:
        nonce = str(int(time.time() * 1000))
        path = "/0/private/Balance"
        payload = {"nonce": nonce}
        headers = {
            "API-Key": api_key,
            "API-Sign": _kraken_sign(path, payload, api_secret),
        }
        resp = requests.post(
            "https://api.kraken.com" + path,
            data=payload,
            headers=headers,
            timeout=10,
        )
        data = resp.json()
        if resp.status_code != 200:
            return False, f"HTTP {resp.status_code}", {}
        errs = data.get("error") or []
        if errs:
            return False, "; ".join(str(e) for e in errs), {}
        bal = data.get("result") or {}
        non_zero = []
        for asset, amount in bal.items():
            try:
                a = float(amount)
            except Exception:
                continue
            if a > 0:
                non_zero.append({"asset": asset, "amount": a})
        non_zero = sorted(non_zero, key=lambda x: x["amount"], reverse=True)[:10]
        return True, "OK", {"balances_non_zero_top10": non_zero, "balances_total": len(bal)}
    except Exception as e:
        return False, str(e), {}


def _get_binance_account_snapshot(api_key: str, api_secret: str) -> tuple[bool, str, dict]:
    try:
        t_resp = requests.get("https://api.binance.com/api/v3/time", timeout=10)
        t_data = t_resp.json() if t_resp.status_code == 200 else {}
        srv_ts = int(t_data.get("serverTime") or 0)
        if srv_ts <= 0:
            srv_ts = int(time.time() * 1000)

        query = urlencode({"timestamp": srv_ts, "recvWindow": 15000})
        sig = hmac.new(api_secret.encode("utf-8"), query.encode("utf-8"), hashlib.sha256).hexdigest()
        url = f"https://api.binance.com/api/v3/account?{query}&signature={sig}"
        resp = requests.get(url, headers={"X-MBX-APIKEY": api_key}, timeout=10)
        data = resp.json()
        if resp.status_code != 200:
            msg = data.get("msg") if isinstance(data, dict) else str(data)
            return False, f"HTTP {resp.status_code}: {msg}", {}
        bals = data.get("balances") or []
        non_zero = []
        for b in bals:
            free = float(b.get("free", 0) or 0)
            locked = float(b.get("locked", 0) or 0)
            total = free + locked
            if total > 0:
                non_zero.append({"asset": b.get("asset"), "free": free, "locked": locked, "total": total})
        non_zero = sorted(non_zero, key=lambda x: x["total"], reverse=True)[:10]
        return True, "OK", {
            "can_trade": bool(data.get("canTrade")),
            "balances_non_zero_top10": non_zero,
            "balances_total": len(bals),
            "update_time": data.get("updateTime"),
        }
    except Exception as e:
        return False, str(e), {}


def _apply_persisted_defaults_if_present():
    if not _PERSISTED_DEFAULTS_PATH.exists():
        return
    try:
        raw = json.loads(_PERSISTED_DEFAULTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            return
        allowed = set(settings.model_fields.keys()) - _PERSIST_EXCLUDED_KEYS
        for key, value in raw.items():
            key_upper = str(key).upper()
            if key_upper not in allowed:
                continue
            try:
                setattr(settings, key_upper, _normalize_runtime_value(key_upper, value))
            except Exception:
                pass
    except Exception as e:
        print(f"[CONFIG] Failed to load persisted defaults: {e}")


async def _periodic_blocking_task(task_name: str, fn, interval_sec: int, initial_delay_sec: int = 0):
    """Run blocking worker function periodically in thread executor."""
    loop = asyncio.get_running_loop()
    delay_sec = _safe_int(initial_delay_sec, 0)
    interval = _safe_int(interval_sec, 300)
    if delay_sec > 0:
        await asyncio.sleep(max(0, delay_sec))
    while True:
        started = datetime.now(timezone.utc)
        print(f"[AUX] {task_name} cycle start {started.isoformat()}")
        try:
            await loop.run_in_executor(None, fn)
            ended = datetime.now(timezone.utc)
            print(f"[AUX] {task_name} cycle ok {ended.isoformat()} duration={(ended-started).total_seconds():.2f}s")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[AUX] {task_name} cycle failed: {e}")
        await asyncio.sleep(max(30, interval))


async def _periodic_cross_asset_engine_task(engine, interval_sec: int, initial_delay_sec: int = 0):
    loop = asyncio.get_running_loop()
    delay_sec = _safe_int(initial_delay_sec, 0)
    interval = _safe_int(interval_sec, 300)
    if delay_sec > 0:
        await asyncio.sleep(max(0, delay_sec))

    last_seen: dict[str, str] = {}
    while True:
        started = datetime.now(timezone.utc)
        try:
            await loop.run_in_executor(None, cross_asset_shadow_worker.run_once)
            db = get_db()
            tf = _safe_int(settings.INTERVAL_MINUTES, 60)
            ibkr_symbols = [s.strip() for s in str(getattr(settings, "IBKR_SYMBOLS", "") or "").split(",") if s.strip()]
            for sym in ibkr_symbols:
                d = db.market_candles.find_one({"symbol": sym, "tf": tf}, {"_id": 0, "t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}, sort=[("t", -1)])
                if not d:
                    continue
                t = str(d.get("t") or "")
                if not t or last_seen.get(sym) == t:
                    continue
                await engine.on_candle(
                    sym,
                    tf,
                    {
                        "symbol": sym,
                        "timestamp": t,
                        "open": d["o"],
                        "high": d["h"],
                        "low": d["l"],
                        "close": d["c"],
                        "volume": d["v"],
                        "_no_persist": True,
                    },
                )
                last_seen[sym] = t
            ended = datetime.now(timezone.utc)
            print(f"[AUX] cross_asset_engine cycle ok {ended.isoformat()} duration={(ended-started).total_seconds():.2f}s")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[AUX] cross_asset_engine cycle failed: {e}")
        await asyncio.sleep(max(30, interval))


async def _start_aux_workers(db, run_id: str, engine):
    """Start background refresh tasks for news sentiment and market intel."""
    start_delay_sec = 5
    news_task = None
    intel_task = None
    market_data_task = None
    tune_task = None
    cross_asset_task = None

    if bool(getattr(settings, "NEWS_WORKER_ENABLED", True)):
        news_task = asyncio.create_task(
            _periodic_blocking_task("news_worker", news_worker.run_once, 300, start_delay_sec)
        )
        try:
            db.bot_events.insert_one({
                "run_id": run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "info",
                "msg": "news_worker_started",
                "data": {"interval_sec": 300},
            })
        except Exception:
            pass

    # Market intel refresh: relevant for intel filter or dynamic asset selection.
    if settings.INTEL_ENABLED or settings.DYNAMIC_ASSETS_ENABLED:
        intel_task = asyncio.create_task(
            _periodic_blocking_task("market_intel_worker", market_intel_worker.run_once, settings.INTEL_POLL_SECONDS, start_delay_sec)
        )
        try:
            db.bot_events.insert_one({
                "run_id": run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "info",
                "msg": "market_intel_worker_started",
                "data": {"interval_sec": settings.INTEL_POLL_SECONDS},
            })
        except Exception:
            pass

    if bool(getattr(settings, "MARKET_DATA_WORKER_ENABLED", True)):
        market_data_task = asyncio.create_task(
            _periodic_blocking_task(
                "market_data_worker",
                market_data_worker.run_once,
                settings.MARKET_DATA_POLL_SECONDS,
                start_delay_sec,
            )
        )
        try:
            db.bot_events.insert_one({
                "run_id": run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "info",
                "msg": "market_data_worker_started",
                "data": {"interval_sec": settings.MARKET_DATA_POLL_SECONDS},
            })
        except Exception:
            pass

    if bool(getattr(settings, "TRADING_IBKR_ENABLED", False)) and bool(getattr(settings, "CROSS_ASSET_SHADOW_ENABLED", False)):
        cross_asset_task = asyncio.create_task(
            _periodic_cross_asset_engine_task(
                engine,
                _safe_int(getattr(settings, "CROSS_ASSET_POLL_SECONDS", 300), 300),
                start_delay_sec,
            )
        )
        try:
            db.bot_events.insert_one({
                "run_id": run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "info",
                "msg": "cross_asset_engine_started",
                "data": {"interval_sec": _safe_int(getattr(settings, "CROSS_ASSET_POLL_SECONDS", 300), 300)},
            })
        except Exception:
            pass

    # Auto config optimizer (optional)
    if settings.AUTO_TUNE_ENABLED:
        tune_task = asyncio.create_task(
            _periodic_blocking_task(
                "config_optimizer_worker",
                config_optimizer_worker.run_once,
                settings.AUTO_TUNE_INTERVAL_SECONDS,
                start_delay_sec,
            )
        )
        try:
            db.bot_events.insert_one({
                "run_id": run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "info",
                "msg": "config_optimizer_worker_started",
                "data": {"interval_sec": settings.AUTO_TUNE_INTERVAL_SECONDS, "auto_apply": settings.AUTO_TUNE_APPLY},
            })
        except Exception:
            pass

    return news_task, intel_task, market_data_task, cross_asset_task, tune_task


async def _stop_aux_workers():
    tasks = [
        t for t in (
            _state.get("news_task"),
            _state.get("intel_task"),
            _state.get("market_data_task"),
            _state.get("cross_asset_task"),
            _state.get("tune_task"),
        ) if t is not None
    ]
    if not tasks:
        return
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


def _get_llm_health(db) -> dict:
    """Infer LLM health from latest market_intel document."""
    intel = db.market_intel.find_one(sort=[("created_at", -1)])
    if not intel:
        return {"llm_ok": None, "degraded": False, "last_error": None}

    raw = str(intel.get("raw", "") or "")
    failed = raw.startswith("LLM_FAILED:")
    err = raw.split("LLM_FAILED:", 1)[1].strip() if failed else None
    return {"llm_ok": not failed, "degraded": failed, "last_error": err}


def _parse_symbols(src: str) -> List[str]:
    return [s.strip() for s in src.split(",") if s.strip()]


def _hour_key_from_candle_t(ts: str) -> Optional[str]:
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(timezone.utc)
        return d.strftime("%Y-%m-%dT%H")
    except Exception:
        return None


def _hour_key_from_dt(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.strftime("%Y-%m-%dT%H")


def _parse_iso_utc_maybe(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        raw = str(value).strip()
        # Normalize high-precision fractional seconds (e.g. 9-digit nanos) to
        # microseconds supported by datetime.fromisoformat.
        if "." in raw:
            head, tail = raw.split(".", 1)
            tz_idx = tail.find("+")
            z_idx = tail.find("Z")
            if z_idx != -1 and (tz_idx == -1 or z_idx < tz_idx):
                frac = tail[:z_idx]
                tz = "Z"
            elif tz_idx != -1:
                frac = tail[:tz_idx]
                tz = tail[tz_idx:]
            else:
                frac = tail
                tz = ""
            if frac:
                frac = frac[:6]
                raw = f"{head}.{frac}{tz}"
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _find_latest_runtime_run_id(db) -> Optional[str]:
    """Find latest non-backtest runtime run_id for post-restart continuity."""
    # 0) Prefer latest runtime started run (most recent session intent).
    ev = db.bot_events.find_one(
        {"msg": "bot_started", "run_id": {"$not": {"$regex": "^bt-"}}},
        sort=[("t", -1)],
    )
    if ev and ev.get("run_id"):
        rid = str(ev["run_id"])
        if db.portfolio.find_one({"run_id": rid}) or db.positions.find_one({"run_id": rid}):
            return rid

    # 1) Prefer run with currently open positions.
    pos = db.positions.find_one(
        {"status": "OPEN", "run_id": {"$not": {"$regex": "^bt-"}}},
        sort=[("entry_time", -1)],
    )
    if pos and pos.get("run_id"):
        return str(pos["run_id"])

    # 2) Fallback to latest portfolio run (insertion order by _id).
    p = db.portfolio.find_one(
        {"run_id": {"$not": {"$regex": "^bt-"}}},
        sort=[("_id", -1)],
    )
    if p and p.get("run_id"):
        return str(p["run_id"])

    return None


_apply_persisted_defaults_if_present()


def _build_binance_trading_symbols(db) -> List[str]:
    """Build Binance symbol universe for trading decision feed."""
    if not settings.TRADING_BINANCE_ENABLED:
        return []

    kraken_symbols = set(_parse_symbols(settings.SYMBOLS))
    symbols = set(_parse_symbols(settings.BINANCE_SYMBOLS))
    symbols.update(_parse_symbols(settings.ALWAYS_ACTIVE_SYMBOLS))

    if settings.EXPAND_UNIVERSE_FROM_RECOMMENDATIONS and settings.DYNAMIC_ASSETS_ENABLED:
        latest = db.asset_recommendations.find_one(sort=[("created_at", -1)])
        if latest and latest.get("created_at"):
            created_at = latest["created_at"]
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            age = (datetime.now(timezone.utc) - created_at.astimezone(timezone.utc)).total_seconds() / 60
            rec_max_age = _safe_float(settings.RECOMMENDATION_MAX_AGE_MINUTES, 180.0)
            if age <= rec_max_age:
                symbols.update(latest.get("symbols", []))

    # Avoid dual feed on the same symbol (Kraken + Binance for identical pair).
    symbols = {s for s in symbols if s not in kraken_symbols}
    return sorted(symbols)


def _latest_stop_info(db, run_id: Optional[str] = None) -> dict:
    q = {"msg": {"$in": ["bot_stopped", "bot_start_failed"]}, "run_id": {"$not": {"$regex": "^bt-"}}}
    if run_id:
        q["run_id"] = run_id
    ev = db.bot_events.find_one(q, sort=[("t", -1)])
    if not ev:
        return {"stopped_at": None, "stopped_reason": None}
    data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
    reason = data.get("reason")
    if not reason:
        reason = "start_failed" if ev.get("msg") == "bot_start_failed" else "manual_stop"
    return {"stopped_at": ev.get("t"), "stopped_reason": str(reason)}


def _has_start_event(db, run_id: Optional[str]) -> bool:
    if not run_id:
        return False
    ev = db.bot_events.find_one({"run_id": run_id, "msg": "bot_started"}, {"_id": 1})
    return ev is not None


def _latest_lifecycle_event(db, run_id: Optional[str]) -> Optional[dict]:
    if not run_id:
        return None
    return db.bot_events.find_one(
        {"run_id": run_id, "msg": {"$in": ["bot_started", "bot_stopped", "bot_start_failed"]}},
        {"_id": 0, "msg": 1, "t": 1, "data": 1},
        sort=[("t", -1)],
    )


def _position_unrealized(pos: dict, current_price: float) -> float:
    entry_px = float(pos.get("entry_price", 0))
    qty = float(pos.get("qty", 0))
    direction = 1 if pos.get("side") == "BUY" else -1
    return (current_price - entry_px) * qty * direction


def _position_daily_unrealized(db, pos: dict) -> float:
    """Mark-to-market daily unrealized component for one open position.

    If position was opened before today's UTC 00:00, daily component is measured
    from today's first candle open; otherwise from entry price.
    """
    sym = pos.get("symbol")
    if not sym:
        return 0.0

    latest = db.market_candles.find_one(
        {"symbol": sym, "tf": settings.INTERVAL_MINUTES},
        sort=[("t", -1)],
    )
    if not latest:
        return 0.0

    current_price = float(latest.get("c", 0))
    entry_px = float(pos.get("entry_price", 0))
    qty = float(pos.get("qty", 0))
    direction = 1 if pos.get("side") == "BUY" else -1

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    entry_time = str(pos.get("entry_time", ""))
    if entry_time.startswith(today):
        base_price = entry_px
    else:
        day_first = db.market_candles.find_one(
            {"symbol": sym, "tf": settings.INTERVAL_MINUTES, "t": {"$regex": f"^{today}"}},
            sort=[("t", 1)],
        )
        base_price = float(day_first.get("o", entry_px)) if day_first else entry_px

    return (current_price - base_price) * qty * direction


def _json_safe_num(v):
    """Convert NaN/Inf to None for JSON-compliant API responses."""
    try:
        f = float(v)
    except Exception:
        return v
    if math.isnan(f) or math.isinf(f):
        return None
    return f

def _validate_mode() -> str:
    mode = (settings.MODE or "paper").lower().strip()
    if mode not in ("paper", "live"):
        raise ValueError("MODE must be 'paper' or 'live'")

    if mode == "live":
        _load_credentials_from_env()
        missing = []
        if not settings.KRAKEN_API_KEY or not settings.KRAKEN_API_SECRET:
            missing.append("KRAKEN_API_KEY/KRAKEN_API_SECRET")
        if not settings.BINANCE_API_KEY or not settings.BINANCE_API_SECRET:
            missing.append("BINANCE_API_KEY/BINANCE_API_SECRET")
        if missing:
            raise ValueError("LIVE mode requires credentials in python-core/.env: " + ", ".join(missing))

    return mode


@router.post("/start")
async def start_bot():
    _sanitize_runtime_settings()
    # pokud už běží, nic nespouštěj
    if _state["running"]:
        return {
            "ok": True,
            "running": True,
            "run_id": _state["run_id"],
            "mode": _validate_mode(),
            "workers": {
                "news_worker": _state.get("news_task") is not None and not _state["news_task"].done(),
                "market_intel_worker": _state.get("intel_task") is not None and not _state["intel_task"].done(),
                "market_data_worker": _state.get("market_data_task") is not None and not _state["market_data_task"].done(),
                "config_optimizer_worker": _state.get("tune_task") is not None and not _state["tune_task"].done(),
                "binance_feed": _state.get("ws_binance") is not None,
            },
        }

    # validace a příprava DB
    try:
        mode = _validate_mode()
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        ensure_indexes()
        db = get_db()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB init failed: {e}")

    # vytvoř run_id (nebo resume posledního runtime run)
    resumed = False
    run_id = None
    if settings.RESUME_ON_START:
        run_id = _find_latest_runtime_run_id(db)
        resumed = bool(run_id)
    if not run_id:
        run_id = uuid.uuid4().hex[:12]

    # start engine + ws
    try:
        interval_minutes = _safe_int(settings.INTERVAL_MINUTES, 60)
        engine = TradingEngine(run_id=run_id, interval=interval_minutes)
        if resumed:
            await engine.replay_missed_from_mongo()
        kraken_symbols = [s.strip() for s in str(getattr(settings, "SYMBOLS", "") or "").split(",") if s.strip()]
        kraken_ws = None
        if kraken_symbols:
            kraken_ws = KrakenWS(on_candle=engine.on_candle, interval=interval_minutes, symbols=kraken_symbols)
            await kraken_ws.start()
        binance_symbols = _build_binance_trading_symbols(db)
        binance_ws = None
        if binance_symbols:
            binance_ws = BinanceWS(on_candle=engine.on_candle, symbols=binance_symbols, interval=interval_minutes)
            await binance_ws.start()
        news_task, intel_task, market_data_task, cross_asset_task, tune_task = await _start_aux_workers(db, run_id, engine)
    except Exception as e:
        print("[BOT-START] exception:")
        print(traceback.format_exc())
        # log do db když to jde
        try:
            db.bot_events.insert_one({
                "run_id": run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "error",
                "msg": "bot_start_failed",
                "data": {"err": str(e), "mode": mode, "traceback": traceback.format_exc()},
            })
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Bot start failed: {e}")

    # update state
    _state.update({
        "running": True,
        "run_id": run_id,
        "ws_kraken": kraken_ws,
        "ws_binance": binance_ws,
        "news_task": news_task,
        "intel_task": intel_task,
        "market_data_task": market_data_task,
        "cross_asset_task": cross_asset_task,
        "tune_task": tune_task,
        "last_stopped_at": None,
        "last_stopped_reason": None,
    })

    # log start
    db.bot_events.insert_one({
        "run_id": run_id,
        "t": datetime.now(timezone.utc).isoformat(),
        "level": "info",
        "msg": "bot_started",
        "data": {"mode": mode, "resumed": resumed}
    })

    return {
        "ok": True,
        "running": True,
        "run_id": run_id,
        "resumed": resumed,
        "mode": mode,
        "workers": {
            "news_worker": news_task is not None,
            "market_intel_worker": intel_task is not None,
                "market_data_worker": market_data_task is not None,
                "cross_asset_worker": cross_asset_task is not None,
                "config_optimizer_worker": tune_task is not None,
            "binance_feed": binance_ws is not None,
        },
    }


@router.post("/stop")
async def stop_bot(reason: str = "manual_stop"):
    if not _state["running"]:
        return {"ok": True, "running": False, "run_id": _state["run_id"]}

    run_id = _state["run_id"]
    kraken_ws = _state["ws_kraken"]
    binance_ws = _state["ws_binance"]

    try:
        if kraken_ws:
            await kraken_ws.stop()
        if binance_ws:
            await binance_ws.stop()
        await _stop_aux_workers()
    finally:
        _state.update({
            "running": False,
            "ws_kraken": None,
            "ws_binance": None,
            "news_task": None,
            "intel_task": None,
            "market_data_task": None,
            "cross_asset_task": None,
            "tune_task": None,
        })

    # log stop (best effort)
    try:
        db = get_db()
        stopped_at = datetime.now(timezone.utc).isoformat()
        db.bot_events.insert_one({
            "run_id": run_id,
            "t": stopped_at,
            "level": "info",
            "msg": "bot_stopped",
            "data": {"reason": reason}
        })
        _state["last_stopped_at"] = stopped_at
        _state["last_stopped_reason"] = reason
    except Exception:
        pass

    return {"ok": True, "running": False, "run_id": run_id}


@router.get("/status")
async def status():
    cache_hit = _cache_get("status")
    if cache_hit is not None:
        return cache_hit
    rid = _state["run_id"] or _resolve_run_id(None)
    stopped_info = {"stopped_at": None, "stopped_reason": None}
    if not _state["running"]:
        latest_lifecycle = None
        try:
            latest_lifecycle = _latest_lifecycle_event(get_db(), rid)
        except Exception:
            latest_lifecycle = None
        if _state.get("last_stopped_reason") or _state.get("last_stopped_at"):
            stopped_info = {
                "stopped_at": _state.get("last_stopped_at"),
                "stopped_reason": _state.get("last_stopped_reason"),
            }
        else:
            try:
                stopped_info = _latest_stop_info(get_db(), rid)
            except Exception:
                stopped_info = {"stopped_at": None, "stopped_reason": None}
        if latest_lifecycle:
            msg = str(latest_lifecycle.get("msg") or "")
            if msg == "bot_started":
                stopped_info = {
                    "stopped_at": latest_lifecycle.get("t"),
                    "stopped_reason": "ungraceful_stop_or_restart",
                }
            elif msg == "bot_start_failed":
                stopped_info = {
                    "stopped_at": latest_lifecycle.get("t"),
                    "stopped_reason": "start_failed",
                }
            elif msg == "bot_stopped":
                data = latest_lifecycle.get("data") if isinstance(latest_lifecycle.get("data"), dict) else {}
                stopped_info = {
                    "stopped_at": latest_lifecycle.get("t"),
                    "stopped_reason": str(data.get("reason") or "manual_stop"),
                }
        if not stopped_info.get("stopped_reason"):
            try:
                db = get_db()
                last_ev = _latest_lifecycle_event(db, rid)
                if last_ev:
                    msg = str(last_ev.get("msg") or "")
                    if msg == "bot_stopped":
                        data = last_ev.get("data") if isinstance(last_ev.get("data"), dict) else {}
                        stopped_info = {
                            "stopped_at": last_ev.get("t"),
                            "stopped_reason": str(data.get("reason") or "manual_stop"),
                        }
                    elif msg == "bot_start_failed":
                        stopped_info = {
                            "stopped_at": last_ev.get("t"),
                            "stopped_reason": "start_failed",
                        }
                    else:
                        # last event is bot_started but runtime is currently down -> ungraceful stop/restart
                        stopped_info = {
                            "stopped_at": last_ev.get("t"),
                            "stopped_reason": "ungraceful_stop_or_restart",
                        }
                elif _has_start_event(db, rid):
                    stopped_info = {
                        "stopped_at": stopped_info.get("stopped_at"),
                        "stopped_reason": "ungraceful_stop_or_restart",
                    }
                else:
                    latest_any = _latest_stop_info(db, None)
                    if latest_any.get("stopped_reason"):
                        stopped_info = latest_any
            except Exception:
                pass
    return _cache_put("status", {
        "running": _state["running"],
        "run_id": rid,
        "workers": {
            "news_worker": _state.get("news_task") is not None and not _state["news_task"].done(),
            "market_intel_worker": _state.get("intel_task") is not None and not _state["intel_task"].done(),
            "market_data_worker": _state.get("market_data_task") is not None and not _state["market_data_task"].done(),
            "cross_asset_worker": _state.get("cross_asset_task") is not None and not _state["cross_asset_task"].done(),
            "config_optimizer_worker": _state.get("tune_task") is not None and not _state["tune_task"].done(),
            "binance_feed": _state.get("ws_binance") is not None,
        },
        **stopped_info,
    }, ttl_sec=2.0)


@router.post("/backtest")
async def run_backtest(req: BacktestRequest):
    db = get_db()
    started_at_dt = datetime.now(timezone.utc)
    started_at = started_at_dt.isoformat()

    try:
        dt_from = datetime.strptime(req.dt_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dt_to = (
            datetime.strptime(req.dt_to, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if req.dt_to
            else datetime.now(timezone.utc)
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid date format: {e}")

    if req.source not in ("mongo", "kraken", "binance"):
        raise HTTPException(status_code=400, detail="source must be 'mongo', 'kraken', or 'binance'")
    if req.initial_equity <= 0:
        raise HTTPException(status_code=400, detail="initial_equity must be > 0")

    # Povolené override parametry pro backtest
    allowed_overrides = {
        "BREAKOUT_N", "EMA_PERIOD", "VOL_FILTER", "VOL_MULT", "COOLDOWN_CANDLES",
        "SL_ATR_MULT", "TP_ATR_MULT", "TRAILING_STOP", "TRAIL_ATR_MULT",
        "TRAIL_ACTIVATION_ATR", "RISK_PER_TRADE", "FEE_RATE", "SPREAD_BPS",
        "TIME_EXIT_MINUTES", "PROFIT_SPLIT_REINVEST",
    }
    overrides = {}
    if req.overrides:
        for k, v in req.overrides.items():
            if k.upper() in allowed_overrides:
                overrides[k.upper()] = v

    # Multi-symbol backtest: "ALL" → sdílené portfolio, jeden engine
    if req.symbol == "ALL":
        all_symbols: List[str] = []
        if req.source == "mongo":
            # Pro Mongo backtest použij všechny skutečně nasbírané symboly pro daný timeframe.
            all_symbols = sorted(
                db.market_candles.distinct(
                    "symbol",
                    {"tf": req.interval},
                )
            )
        else:
            seen = set()
            for src in (settings.SYMBOLS, settings.BINANCE_SYMBOLS, settings.ALWAYS_ACTIVE_SYMBOLS):
                for s in src.split(","):
                    s = s.strip()
                    if s and s not in seen:
                        seen.add(s)
                        all_symbols.append(s)
        if not all_symbols:
            raise HTTPException(status_code=400, detail="No symbols configured")

        runner = MultiBacktestRunner(
            source=req.source,
            symbols=all_symbols,
            dt_from=dt_from,
            dt_to=dt_to,
            initial_equity=req.initial_equity,
            interval=req.interval,
            with_sentiment=req.with_sentiment,
            mode=req.mode,
            overrides=overrides,
        )
        try:
            db.bot_events.insert_one({
                "run_id": runner.run_id,
                "t": started_at,
                "level": "info",
                "msg": "backtest_started",
                "data": {
                    "mode": req.mode,
                    "source": req.source,
                    "symbol": req.symbol,
                    "interval": req.interval,
                    "with_sentiment": req.with_sentiment,
                    "initial_equity": req.initial_equity,
                },
            })
        except Exception:
            pass

        try:
            result = await runner.run()
        except Exception as e:
            try:
                db.bot_events.insert_one({
                    "run_id": runner.run_id,
                    "t": datetime.now(timezone.utc).isoformat(),
                    "level": "error",
                    "msg": "backtest_failed",
                    "data": {"err": str(e)},
                })
            except Exception:
                pass
            raise HTTPException(status_code=500, detail=f"Multi-backtest failed: {e}")
        finished_at_dt = datetime.now(timezone.utc)
        finished_at = finished_at_dt.isoformat()
        duration_sec = round((finished_at_dt - started_at_dt).total_seconds(), 3)
        try:
            db.bot_events.insert_one({
                "run_id": result.run_id,
                "t": finished_at,
                "level": "info",
                "msg": "backtest_finished",
                "data": {"duration_sec": duration_sec, "mode": req.mode},
            })
        except Exception:
            pass

        return {
            "ok": True,
            "multi": True,
            "run_id": result.run_id,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_sec": duration_sec,
            "results": [
                {
                    "ok": True,
                    "symbol": sym,
                    "source": result.source,
                    **{
                        k: _json_safe_num(v)
                        for k, v in result.per_symbol.get(sym, {}).items()
                    },
                }
                for sym in result.symbols
            ],
            "summary": {
                "symbols": len(result.symbols),
                "total_candles": result.total_candles,
                "total_trades": result.total_trades,
                "win_rate": _json_safe_num(result.win_rate),
                "total_pnl": _json_safe_num(result.total_pnl),
                "max_drawdown": _json_safe_num(result.max_drawdown),
                "profit_factor": _json_safe_num(result.profit_factor),
                "avg_win": _json_safe_num(result.avg_win),
                "avg_loss": _json_safe_num(result.avg_loss),
                "final_equity": _json_safe_num(result.final_equity),
                "cash_buffer": _json_safe_num(result.cash_buffer),
            },
        }

    # Single-symbol backtest
    runner = BacktestRunner(
        source=req.source,
        symbol=req.symbol,
        dt_from=dt_from,
        dt_to=dt_to,
        initial_equity=req.initial_equity,
        interval=req.interval,
        with_sentiment=req.with_sentiment,
        mode=req.mode,
    )
    try:
        db.bot_events.insert_one({
            "run_id": runner.run_id,
            "t": started_at,
            "level": "info",
            "msg": "backtest_started",
            "data": {
                "mode": req.mode,
                "source": req.source,
                "symbol": req.symbol,
                "interval": req.interval,
                "with_sentiment": req.with_sentiment,
                "initial_equity": req.initial_equity,
            },
        })
    except Exception:
        pass

    # Aplikuj overrides na settings dočasně
    saved_overrides = {}
    for k, v in overrides.items():
        saved_overrides[k] = getattr(settings, k)
        setattr(settings, k, v)

    try:
        result = await runner.run()
    except Exception as e:
        try:
            db.bot_events.insert_one({
                "run_id": runner.run_id,
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "error",
                "msg": "backtest_failed",
                "data": {"err": str(e)},
            })
        except Exception:
            pass
        raise HTTPException(status_code=500, detail=f"Backtest failed: {e}")
    finally:
        for k, v in saved_overrides.items():
            setattr(settings, k, v)
    finished_at_dt = datetime.now(timezone.utc)
    finished_at = finished_at_dt.isoformat()
    duration_sec = round((finished_at_dt - started_at_dt).total_seconds(), 3)
    try:
        db.bot_events.insert_one({
            "run_id": result.run_id,
            "t": finished_at,
            "level": "info",
            "msg": "backtest_finished",
            "data": {"duration_sec": duration_sec, "mode": req.mode},
        })
    except Exception:
        pass

    portfolio = get_db().portfolio.find_one({"run_id": result.run_id})
    cash_buffer = float(portfolio.get("cash_buffer", 0.0)) if portfolio else 0.0

    return {
        "ok": True,
        "run_id": result.run_id,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_sec": duration_sec,
        "symbol": result.symbol,
        "source": result.source,
        "total_candles": result.total_candles,
        "total_trades": result.total_trades,
        "win_rate": _json_safe_num(result.win_rate),
        "total_pnl": _json_safe_num(result.total_pnl),
        "max_drawdown": _json_safe_num(result.max_drawdown),
        "profit_factor": _json_safe_num(result.profit_factor),
        "avg_win": _json_safe_num(result.avg_win),
        "avg_loss": _json_safe_num(result.avg_loss),
        "final_equity": _json_safe_num(result.final_equity),
        "cash_buffer": _json_safe_num(cash_buffer),
    }


# ─── Dashboard endpoints ───────────────────────────────────────

def _resolve_run_id(run_id: Optional[str]) -> Optional[str]:
    if run_id:
        return run_id
    if _state.get("run_id"):
        return _state["run_id"]
    try:
        db = get_db()
        return _find_latest_runtime_run_id(db)
    except Exception:
        return None


@router.get("/portfolio")
async def get_portfolio(run_id: Optional[str] = None):
    db = get_db()
    rid = _resolve_run_id(run_id)
    cache_k = _cache_key("portfolio", rid or "none")
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    if not rid:
        return _cache_put(cache_k, {"run_id": None, "equity": 1000.0, "cash_buffer": 0.0, "daily_pnl": 0.0}, ttl_sec=3.0)
    portfolio = db.portfolio.find_one({"run_id": rid})
    if not portfolio:
        # Fallback to latest runtime portfolio if resolved run has no portfolio document.
        p = db.portfolio.find_one(
            {"run_id": {"$not": {"$regex": "^bt-"}}},
            sort=[("_id", -1)],
        )
        if p and p.get("run_id"):
            rid = str(p["run_id"])
            portfolio = p
    if not portfolio:
        return _cache_put(cache_k, {"run_id": rid, "equity": 1000.0, "cash_buffer": 0.0, "daily_pnl": 0.0}, ttl_sec=3.0)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    reset_at = _parse_iso_utc_maybe(portfolio.get("daily_pnl_reset_at"))
    day_trades = list(db.positions.find({
        "run_id": rid, "status": "CLOSED",
        "exit_time": {"$regex": f"^{today}"}
    }))
    if reset_at:
        filtered = []
        for t in day_trades:
            dt = _parse_iso_utc_maybe(t.get("exit_time"))
            if dt and dt >= reset_at:
                filtered.append(t)
        day_trades = filtered
    daily_pnl = sum(float(t.get("pnl", 0)) for t in day_trades)

    # Mark-to-market unrealized PnL from open positions
    unrealized = 0.0
    daily_unrealized = 0.0
    open_positions = list(db.positions.find({"run_id": rid, "status": "OPEN"}))
    for pos in open_positions:
        if reset_at:
            et = _parse_iso_utc_maybe(pos.get("entry_time"))
            if not et or et < reset_at:
                continue
        sym = pos.get("symbol")
        latest = db.market_candles.find_one(
            {"symbol": sym, "tf": settings.INTERVAL_MINUTES},
            sort=[("t", -1)]
        )
        if not latest:
            continue
        current_price = float(latest.get("c", 0))
        unrealized += _position_unrealized(pos, current_price)
        daily_unrealized += _position_daily_unrealized(db, pos)

    equity = float(portfolio.get("equity", 1000.0))
    equity_mtm = equity + unrealized
    daily_pnl_mtm = daily_pnl + daily_unrealized

    cash_buffer = float(portfolio.get("cash_buffer", 0.0) or 0.0)
    if cash_buffer <= 0:
        # Fallback: pokud aktuální run nemá buffer (nebo má 0), vezmi poslední kladný runtime buffer.
        last_buf = db.portfolio.find_one(
            {
                "run_id": {"$not": {"$regex": "^bt-"}},
                "cash_buffer": {"$gt": 0},
            },
            {"cash_buffer": 1, "_id": 0},
            sort=[("_id", -1)],
        )
        if last_buf:
            try:
                cash_buffer = float(last_buf.get("cash_buffer", 0.0) or 0.0)
            except Exception:
                pass

    return _cache_put(cache_k, {
        "run_id": rid,
        "equity": equity,
        "equity_mtm": round(equity_mtm, 2),
        "cash_buffer": round(cash_buffer, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_pnl_mtm": round(daily_pnl_mtm, 2),
        "daily_unrealized_pnl": round(daily_unrealized, 2),
        "unrealized_pnl": round(unrealized, 2),
    }, ttl_sec=8.0)


@router.post("/paper/reset-account")
async def reset_paper_account(run_id: Optional[str] = None):
    mode = (settings.MODE or "paper").lower().strip()
    if mode != "paper":
        raise HTTPException(status_code=400, detail="Account reset is allowed only in paper mode")

    db = get_db()
    rid = _resolve_run_id(run_id)
    if not rid:
        raise HTTPException(status_code=404, detail="No active runtime run found")

    now_iso = datetime.now(timezone.utc).isoformat()
    p = db.portfolio.find_one({"run_id": rid}) or {}
    initial_equity = float(p.get("initial_equity", 1000.0) or 1000.0)
    db.portfolio.update_one(
        {"run_id": rid},
        {
            "$set": {
                "equity": round(initial_equity, 2),
                "cash_buffer": 0.0,
                "daily_pnl_reset_at": now_iso,
            },
            "$setOnInsert": {
                "run_id": rid,
                "initial_equity": round(initial_equity, 2),
                "created_at": now_iso,
            },
        },
        upsert=True,
    )
    db.bot_events.insert_one(
        {
            "run_id": rid,
            "t": now_iso,
            "level": "info",
            "msg": "paper_account_reset",
            "data": {"equity": round(initial_equity, 2), "cash_buffer": 0.0},
        }
    )
    return {"ok": True, "run_id": rid, "equity": round(initial_equity, 2), "cash_buffer": 0.0, "daily_pnl": 0.0}


@router.get("/positions/open")
async def get_open_positions(run_id: Optional[str] = None):
    db = get_db()
    rid = _resolve_run_id(run_id)
    if not rid:
        return []
    positions = list(db.positions.find({"run_id": rid, "status": "OPEN"}, {"_id": 0}))

    for pos in positions:
        sym = pos.get("symbol")
        latest = db.market_candles.find_one(
            {"symbol": sym, "tf": settings.INTERVAL_MINUTES},
            sort=[("t", -1)]
        )
        if latest:
            current_price = latest["c"]
            entry_px = float(pos.get("entry_price", 0))
            qty = float(pos.get("qty", 0))
            direction = 1 if pos.get("side") == "BUY" else -1
            pos["current_price"] = current_price
            pos["unrealized_pnl"] = round((current_price - entry_px) * qty * direction, 2)
        else:
            pos["current_price"] = None
            pos["unrealized_pnl"] = None

    return positions


@router.get("/positions/closed")
async def get_closed_positions(run_id: Optional[str] = None, limit: int = 50):
    db = get_db()
    rid = _resolve_run_id(run_id)
    lim = max(1, min(int(limit), 500))
    cache_k = _cache_key("closed_positions", rid or "none", lim)
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    if not rid:
        return _cache_put(cache_k, [], ttl_sec=3.0)
    positions = list(
        db.positions.find({"run_id": rid, "status": "CLOSED"}, {"_id": 0})
        .sort("exit_time", -1)
        .limit(lim)
    )
    return _cache_put(cache_k, positions, ttl_sec=8.0)


@router.get("/equity-curve")
async def get_equity_curve(
    run_id: Optional[str] = None,
    include_mtm: bool = False,
    all_runtime: bool = False,
    paper_resets: bool = True,
    since_restart: bool = False,
):
    db = get_db()
    rid = _resolve_run_id(run_id)
    restart_t = None
    if since_restart and not all_runtime:
        ev = db.bot_events.find_one(
            {"msg": "bot_started", "run_id": {"$not": {"$regex": "^bt-"}}},
            sort=[("t", -1)],
        )
        if ev:
            restart_t = str(ev.get("t"))
            rid = str(ev.get("run_id") or rid)
    restart_dt = _parse_iso_utc_maybe(restart_t) if restart_t else None

    if all_runtime:
        q = {"status": "CLOSED", "run_id": {"$not": {"$regex": "^bt-"}}}
        trades = list(
            db.positions.find(q, {"run_id": 1, "exit_time": 1, "pnl": 1, "_id": 0}).sort("exit_time", 1)
        )
    else:
        if not rid:
            return []
        q = {"run_id": rid, "status": "CLOSED"}
        trades = list(
            db.positions.find(
                q,
                {"exit_time": 1, "pnl": 1, "_id": 0}
            ).sort("exit_time", 1)
        )
        if restart_dt is not None:
            trades = [
                t for t in trades
                if (_parse_iso_utc_maybe(str(t.get("exit_time") or "")) or datetime.min.replace(tzinfo=timezone.utc)) >= restart_dt
            ]

    initial_equity = 1000.0
    if not all_runtime and rid:
        p0 = db.portfolio.find_one({"run_id": rid}, {"initial_equity": 1, "_id": 0}) or {}
        initial_equity = float(p0.get("initial_equity", 1000.0))
    equity = initial_equity
    current_run = None
    curve = []
    for t in trades:
        if all_runtime:
            rid_t = str(t.get("run_id") or "")
            if current_run is None:
                current_run = rid_t
            elif rid_t != current_run:
                current_run = rid_t
                equity = 1000.0
                curve.append({"t": t.get("exit_time"), "equity": round(equity, 2)})

        equity += float(t.get("pnl", 0))
        eq_round = round(equity, 2)
        curve.append({"t": t.get("exit_time"), "equity": eq_round})

        # Paper-mode visualization reset: if account is wiped, show reset to 1000.
        if all_runtime and paper_resets and eq_round <= 0:
            curve.append({"t": t.get("exit_time"), "equity": 1000.0})
            equity = 1000.0

    if include_mtm:
        # Add optional mark-to-market point for current open positions.
        if all_runtime:
            # For all-runtime curve append only current runtime MTM snapshot.
            # Mixing open positions across historical runs can create invalid
            # extreme last points (stale/orphaned positions from old runs).
            ev = db.bot_events.find_one(
                {"msg": "bot_started", "run_id": {"$not": {"$regex": "^bt-"}}},
                sort=[("t", -1)],
            )
            rid_live = str((ev or {}).get("run_id") or rid or "")
            if rid_live:
                portfolio = db.portfolio.find_one({"run_id": rid_live}) or {}
                if "equity_mtm" in portfolio:
                    equity_mtm = round(float(portfolio.get("equity_mtm", 1000.0)), 2)
                else:
                    equity_mtm = round(float(portfolio.get("equity", 1000.0)), 2)
            else:
                equity_mtm = 1000.0
        else:
            unrealized = 0.0
            open_positions = list(db.positions.find({"run_id": rid, "status": "OPEN"}))
            for pos in open_positions:
                sym = pos.get("symbol")
                latest = db.market_candles.find_one(
                    {"symbol": sym, "tf": settings.INTERVAL_MINUTES},
                    sort=[("t", -1)]
                )
                if not latest:
                    continue
                current_price = float(latest.get("c", 0))
                unrealized += _position_unrealized(pos, current_price)

            if curve:
                base_equity = curve[-1]["equity"]
            else:
                if since_restart and not all_runtime:
                    base_equity = 1000.0
                else:
                    portfolio = db.portfolio.find_one({"run_id": rid}) or {"equity": 1000.0}
                    base_equity = float(portfolio.get("equity", 1000.0))

            equity_mtm = round(base_equity + unrealized, 2)
        curve.append({"t": datetime.now(timezone.utc).isoformat(), "equity": equity_mtm})

    return curve


@router.get("/events")
async def get_events(run_id: Optional[str] = None, limit: int = 50):
    db = get_db()
    rid = _resolve_run_id(run_id)
    if not rid:
        return []
    events = list(
        db.bot_events.find({"run_id": rid}, {"_id": 0})
        .sort("t", -1)
        .limit(limit)
    )
    return events


@router.get("/signals")
async def get_signals(run_id: Optional[str] = None, limit: int = 50):
    db = get_db()
    rid = _resolve_run_id(run_id)
    lim = max(1, min(int(limit), 500))
    cache_k = _cache_key("signals", rid or "none", lim)
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    if not rid:
        return _cache_put(cache_k, [], ttl_sec=3.0)
    signals = list(
        db.bot_signals.find({"run_id": rid}, {"_id": 0})
        .sort("t", -1)
        .limit(lim)
    )
    return _cache_put(cache_k, signals, ttl_sec=8.0)


@router.get("/traded-symbols")
async def get_traded_symbols(run_id: Optional[str] = None, lookback_hours: int = 720):
    db = get_db()
    rid = _resolve_run_id(run_id)
    if not rid:
        return {
            "run_id": None,
            "symbols": [],
            "paper_symbols": [],
            "shadow_symbols": [],
            "signal_symbols": [],
            "cross_asset_symbols": [],
        }

    lb = max(1, min(int(lookback_hours), 24 * 365))
    since_iso = (datetime.now(timezone.utc) - timedelta(hours=lb)).isoformat()
    since_dt = datetime.now(timezone.utc) - timedelta(hours=lb)

    paper_symbols = sorted(
        set(
            str(x).strip()
            for x in db.positions.distinct("symbol", {"run_id": rid})
            if isinstance(x, str) and str(x).strip()
        )
    )
    shadow_symbols = sorted(
        set(
            str(x).strip()
            for x in db.bot_signals.distinct(
                "symbol", {"run_id": rid, "action": {"$in": ["shadow", "policy"]}, "t": {"$gte": since_iso}}
            )
            if isinstance(x, str) and str(x).strip()
        )
    )
    signal_symbols = sorted(
        set(
            str(x).strip()
            for x in db.bot_signals.distinct("symbol", {"run_id": rid, "t": {"$gte": since_iso}})
            if isinstance(x, str) and str(x).strip()
        )
    )
    cross_asset_symbols = sorted(
        set(
            str(x).strip()
            for x in db.cross_asset_candles.distinct("symbol", {"timestamp": {"$gte": since_dt}})
            if isinstance(x, str) and str(x).strip()
        )
    )
    symbols = sorted(set(paper_symbols) | set(shadow_symbols) | set(signal_symbols) | set(cross_asset_symbols))
    return {
        "run_id": rid,
        "lookback_hours": lb,
        "symbols": symbols,
        "paper_symbols": paper_symbols,
        "shadow_symbols": shadow_symbols,
        "signal_symbols": signal_symbols,
        "cross_asset_symbols": cross_asset_symbols,
    }


@router.get("/shadow-horizon-summary")
async def get_shadow_horizon_summary(
    run_id: Optional[str] = None,
    lookback_hours: int = 720,
    horizons: str = "720,1440,2160,2880,3600,4320,5040,5760,6480,7200,7920,8640,9360,10080",
    limit: int = 10000,
    actions: str = "shadow,policy,executed",
):
    hs = []
    for part in str(horizons or "").split(","):
        try:
            v = int(part.strip())
            if v > 0:
                hs.append(v)
        except Exception:
            continue
    hs = sorted(set(hs))
    if not hs:
        hs = [720, 1440]

    def _simulate_for_horizon(rid: str, h: int) -> dict:
        db = get_db()
        since_iso = (datetime.now(timezone.utc) - timedelta(hours=max(1, min(int(lookback_hours), 24 * 365)))).isoformat()
        actions_set = {a.strip().lower() for a in str(actions or "").split(",") if a.strip()}
        if not actions_set:
            actions_set = {"shadow", "policy", "executed"}

        rows = list(
            db.bot_signals.find(
                {"run_id": rid, "action": {"$in": list(actions_set)}, "t": {"$gte": since_iso}},
                {"_id": 0, "symbol": 1, "side": 1, "t": 1},
            ).sort("t", 1)
        )
        dedup = {}
        for r in rows:
            sym = str(r.get("symbol") or "").strip()
            side = str(r.get("side") or "").strip().upper()
            t = str(r.get("t") or "").strip()
            if not sym or side not in {"BUY", "SELL"} or not t:
                continue
            dedup[(sym, side, t)] = r

        eval_docs = list(
            db.signal_quality_shadow_eval.find(
                {"run_id": rid, "horizon_min": int(h), "t": {"$gte": since_iso}},
                {"_id": 0, "symbol": 1, "side": 1, "t": 1, "ret_h": 1},
            )
        )
        eval_map = {}
        for e in eval_docs:
            try:
                k = (str(e.get("symbol") or "").strip(), str(e.get("side") or "").strip().upper(), str(e.get("t") or "").strip())
                eval_map[k] = float(e.get("ret_h"))
            except Exception:
                continue

        kraken_bases = {"ETH"}
        binance_bases = {"SOL", "PAXG", "BNB", "XRP", "DOGE", "TRX", "USDC"}
        ibkr_bases = {"BTC"}

        stake_pct = 0.10
        split_reinvest = max(0.0, min(float(getattr(settings, "PROFIT_SPLIT_REINVEST", 0.6) or 0.6), 1.0))
        kraken_fee = max(0.0, float(getattr(settings, "FEE_RATE_KRAKEN", 0.0025) or 0.0025))
        binance_fee = max(0.0, float(getattr(settings, "FEE_RATE_BINANCE", 0.0010) or 0.0010))
        ibkr_fee = max(0.0, float(getattr(settings, "FEE_RATE_IBKR_STOCKS", 0.00005) or 0.00005))

        k_eq = b_eq = i_eq = 100.0
        k_buf = b_buf = i_buf = 0.0
        for key in sorted(dedup.keys(), key=lambda x: x[2]):
            ret = eval_map.get(key)
            if ret is None:
                continue
            sym = key[0]
            base = sym.split("/")[0].upper() if "/" in sym else sym.upper()
            if base in kraken_bases:
                notional = k_eq * stake_pct
                pnl = (notional * ret) - (notional * kraken_fee * 2.0)
                if pnl > 0:
                    reinvest = pnl * split_reinvest
                    k_eq += reinvest
                    k_buf += (pnl - reinvest)
                else:
                    k_eq += pnl
            elif base in ibkr_bases:
                notional = i_eq * stake_pct
                pnl = (notional * ret) - (notional * ibkr_fee * 2.0)
                if pnl > 0:
                    reinvest = pnl * split_reinvest
                    i_eq += reinvest
                    i_buf += (pnl - reinvest)
                else:
                    i_eq += pnl
            else:
                notional = b_eq * stake_pct
                pnl = (notional * ret) - (notional * binance_fee * 2.0)
                if pnl > 0:
                    reinvest = pnl * split_reinvest
                    b_eq += reinvest
                    b_buf += (pnl - reinvest)
                else:
                    b_eq += pnl

        eq = float(k_eq + b_eq + i_eq)
        buf = float(k_buf + b_buf + i_buf)
        total = eq + buf
        return {
            "equity_end_eur": round(eq, 4),
            "cash_buffer_end_eur": round(buf, 4),
            "total_end_eur": round(total, 4),
            "pnl_vs_300_eur": round(total - 300.0, 4),
        }

    rid = _resolve_run_id(run_id)
    items = []
    for h in hs:
        rep = await get_signal_quality_shadow_report(
            run_id=run_id,
            lookback_hours=lookback_hours,
            horizon_min=h,
            limit=limit,
            actions=actions,
        )
        sim = _simulate_for_horizon(rid, h) if rid else {}
        summary = rep.get("summary", {}) if isinstance(rep, dict) else {}
        counts = rep.get("counts", {}) if isinstance(rep, dict) else {}
        items.append(
            {
                "horizon_min": h,
                "day": int(math.ceil(float(h) / 1440.0)),
                "shadow_eval_samples": int(summary.get("shadow_eval_samples", 0) or 0),
                "shadow_win_rate_h": _json_safe_num(summary.get("shadow_win_rate_h")),
                "shadow_profit_factor_h": _json_safe_num(summary.get("shadow_profit_factor_h")),
                "shadow_avg_ret_h": _json_safe_num(summary.get("shadow_avg_ret_h")),
                "total": int(counts.get("total", 0) or 0),
                "total_dedup": int(counts.get("total_dedup", 0) or 0),
                "shadow": int(counts.get("shadow", 0) or 0),
                "policy": int(counts.get("policy", 0) or 0),
                "executed": int(counts.get("executed", 0) or 0),
                **sim,
            }
        )

    return {
        "run_id": rid,
        "lookback_hours": lookback_hours,
        "horizons": hs,
        "items": items,
    }


@router.get("/runs")
async def get_runs():
    db = get_db()
    pipeline = [
        {"$group": {
            "_id": "$run_id",
            "started_at": {"$min": "$t"},
            "events": {"$sum": 1},
        }},
        {"$sort": {"started_at": -1}},
        {"$limit": 50},
    ]
    runs = list(db.bot_events.aggregate(pipeline))

    result = []
    for r in runs:
        rid = r["_id"]
        trade_count = db.positions.count_documents({"run_id": rid, "status": "CLOSED"})
        is_backtest = str(rid).startswith("bt-")
        result.append({
            "run_id": rid,
            "started_at": r.get("started_at"),
            "trade_count": trade_count,
            "is_backtest": is_backtest,
        })

    return result


@router.get("/config-recommendations/latest")
async def get_latest_config_recommendation():
    db = get_db()
    doc = db.config_recommendations.find_one(sort=[("created_at", -1)])
    if not doc:
        return {"latest": None}
    doc["_id"] = str(doc["_id"])
    if hasattr(doc.get("created_at"), "isoformat"):
        doc["created_at"] = doc["created_at"].isoformat()
    return {"latest": doc}


@router.post("/config-recommendations/apply-latest")
async def apply_latest_config_recommendation():
    db = get_db()
    doc = db.config_recommendations.find_one(sort=[("created_at", -1)])
    if not doc:
        raise HTTPException(status_code=404, detail="No config recommendation available")

    selected = doc.get("selected") or {}
    summary = selected.get("summary") or {}
    overrides = selected.get("overrides") or {}
    if not isinstance(overrides, dict) or not overrides:
        raise HTTPException(status_code=400, detail="Latest recommendation has no overrides")

    can_apply = passes_apply_guard(summary)
    if not can_apply:
        raise HTTPException(status_code=400, detail="Apply guard failed for latest recommendation")

    applied = apply_overrides(overrides)
    db.config_recommendations.update_one(
        {"_id": doc["_id"]},
        {"$set": {"manual_applied": True, "manual_applied_at": datetime.now(timezone.utc), "manual_applied_values": applied}},
    )
    db.bot_events.insert_one(
        {
            "run_id": "system",
            "t": datetime.now(timezone.utc).isoformat(),
            "level": "info",
            "msg": "auto_tune_manual_apply",
            "data": {"applied": applied},
        }
    )
    return {"ok": True, "applied": applied}


@router.get("/config")
async def get_config():
    return _get_public_config()


@router.put("/config")
async def update_config(updates: dict):
    allowed = set(settings.model_fields.keys()) - _PERSIST_EXCLUDED_KEYS
    updated = {}
    for key, value in updates.items():
        key_upper = key.upper()
        if key_upper not in allowed:
            continue
        try:
            normalized = _normalize_runtime_value(key_upper, value)
            setattr(settings, key_upper, normalized)
            updated[key_upper] = normalized
        except Exception:
            pass
    _sanitize_runtime_settings()
    return {"ok": True, "updated": updated}


@router.get("/credentials/status")
async def get_credentials_status():
    env_state = _load_credentials_from_env()
    loop = asyncio.get_running_loop()
    ibkr = await loop.run_in_executor(None, get_ibkr_status, 4)
    return {
        "kraken": {
            "configured": bool((settings.KRAKEN_API_KEY or "").strip() and (settings.KRAKEN_API_SECRET or "").strip()),
        },
        "binance": {
            "configured": bool((settings.BINANCE_API_KEY or "").strip() and (settings.BINANCE_API_SECRET or "").strip()),
        },
        "ibkr": {
            "configured": True,
            "connected": bool(ibkr.get("connected")),
            "host": ibkr.get("host"),
            "port": ibkr.get("port"),
            "client_id": ibkr.get("client_id"),
            "readonly": ibkr.get("readonly"),
            "accounts": ibkr.get("accounts", []),
            "error": ibkr.get("error"),
        },
        "mode": (settings.MODE or "paper").lower().strip(),
        "source": "python-core/.env",
        "env_path": env_state.get("env_path"),
    }


@router.get("/ibkr/status")
async def ibkr_status():
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, get_ibkr_status, 8)


@router.post("/config/profile/ibkr-shadow")
async def apply_ibkr_shadow_profile():
    """Apply fee + gate defaults suitable for IBKR shadow calibration."""
    updates = {
        "DEFAULT_BROKER": "ibkr",
        "FEE_AWARE_GATE_ENABLED": True,
        "FEE_AWARE_MIN_EDGE_MULT": 1.25,
        "FEE_RATE_IBKR_FX": 0.00002,
        "FEE_RATE_IBKR_FUTURES": 0.00008,
        "FEE_RATE_IBKR_STOCKS": 0.00005,
        "IBKR_TWS_HOST": "127.0.0.1",
        "IBKR_TWS_PORT": 7497,
        "IBKR_GATEWAY_TRADING_MODE": "paper",
        "IBKR_CLIENT_ID": 77,
    }
    applied = {}
    for key, value in updates.items():
        try:
            setattr(settings, key, value)
            applied[key] = value
        except Exception:
            pass
    return {"ok": True, "applied": applied}


@router.post("/credentials/set")
async def set_exchange_credentials(payload: ExchangeCredentialsUpdate):
    raise HTTPException(
        status_code=410,
        detail="Credentials write via API is disabled. Put keys into python-core/.env and call /bot/credentials/reload-env.",
    )


@router.post("/credentials/reload-env")
async def reload_credentials_env():
    state = _load_credentials_from_env()
    return {"ok": True, **state}


@router.post("/credentials/test")
async def test_exchange_credentials(exchange: str = Query(..., description="kraken|binance")):
    _load_credentials_from_env()
    ex = (exchange or "").strip().lower()
    if ex == "kraken":
        key = (settings.KRAKEN_API_KEY or "").strip()
        secret = (settings.KRAKEN_API_SECRET or "").strip()
        if not key or not secret:
            raise HTTPException(status_code=400, detail="Kraken credentials are not configured")
        ok, msg = _test_kraken_private(key, secret)
        return {"ok": ok, "exchange": "kraken", "message": msg, "mode": (settings.MODE or "paper").lower().strip()}
    if ex == "binance":
        key = (settings.BINANCE_API_KEY or "").strip()
        secret = (settings.BINANCE_API_SECRET or "").strip()
        if not key or not secret:
            raise HTTPException(status_code=400, detail="Binance credentials are not configured")
        ok, msg = _test_binance_private(key, secret)
        return {"ok": ok, "exchange": "binance", "message": msg, "mode": (settings.MODE or "paper").lower().strip()}
    raise HTTPException(status_code=400, detail="exchange must be 'kraken' or 'binance'")


@router.post("/live/dry-run")
async def live_dry_run():
    """Connectivity and account snapshot check without placing any order."""
    _load_credentials_from_env()
    mode = (settings.MODE or "paper").lower().strip()
    if mode != "live":
        raise HTTPException(status_code=400, detail="Dry-run requires MODE=live")

    k_key = (settings.KRAKEN_API_KEY or "").strip()
    k_sec = (settings.KRAKEN_API_SECRET or "").strip()
    b_key = (settings.BINANCE_API_KEY or "").strip()
    b_sec = (settings.BINANCE_API_SECRET or "").strip()
    if not (k_key and k_sec and b_key and b_sec):
        raise HTTPException(status_code=400, detail="Missing exchange credentials in python-core/.env")

    k_ok, k_msg, k_snap = _get_kraken_account_snapshot(k_key, k_sec)
    b_ok, b_msg, b_snap = _get_binance_account_snapshot(b_key, b_sec)

    out = {
        "ok": bool(k_ok and b_ok),
        "mode": mode,
        "orders_placed": 0,
        "kraken": {"ok": k_ok, "message": k_msg, "snapshot": k_snap},
        "binance": {"ok": b_ok, "message": b_msg, "snapshot": b_snap},
        "note": "Dry-run only checks private API access and account snapshot. No order is sent.",
    }
    if not out["ok"]:
        raise HTTPException(status_code=400, detail=out)
    return out


@router.post("/config/defaults/save-current")
async def save_current_config_as_defaults():
    payload = _get_persistable_config()
    _PERSISTED_DEFAULTS_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "ok": True,
        "saved": len(payload),
        "path": str(_PERSISTED_DEFAULTS_PATH),
    }


@router.get("/config/defaults")
async def get_config_defaults():
    if not _PERSISTED_DEFAULTS_PATH.exists():
        return {"ok": True, "exists": False, "path": str(_PERSISTED_DEFAULTS_PATH), "defaults": {}}
    try:
        payload = json.loads(_PERSISTED_DEFAULTS_PATH.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}
    return {
        "ok": True,
        "exists": _PERSISTED_DEFAULTS_PATH.exists(),
        "path": str(_PERSISTED_DEFAULTS_PATH),
        "defaults": _normalize_config_payload(payload, include_private=False),
    }


@router.post("/config/export-current")
async def export_current_config_to_presets_dir():
    payload = _get_persistable_config()
    _CONFIG_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    filename = f"exported-config-{ts}.json"
    path = _CONFIG_PRESETS_DIR / filename
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "saved": len(payload),
        "filename": filename,
        "path": str(path),
    }


@router.get("/config/presets/list")
async def list_config_presets():
    _CONFIG_PRESETS_DIR.mkdir(parents=True, exist_ok=True)
    files = sorted(
        [p.name for p in _CONFIG_PRESETS_DIR.glob("*.json") if p.is_file()],
        reverse=True,
    )
    return {"ok": True, "files": files, "path": str(_CONFIG_PRESETS_DIR)}


@router.get("/recommendations")
async def get_recommendations():
    db = get_db()
    llm_health = _get_llm_health(db)
    latest = db.asset_recommendations.find_one(sort=[("created_at", -1)])
    if not latest:
        return {
            "symbols": [],
            "details": {},
            "created_at": None,
            "overall": "NEUTRAL",
            "always_active": [],
            **llm_health,
        }

    latest["_id"] = str(latest["_id"])
    if hasattr(latest.get("created_at"), "isoformat"):
        latest["created_at"] = latest["created_at"].isoformat()

    # Enrichment: počet svíček a readiness per symbol
    for sym in latest.get("symbols", []):
        count = db.market_candles.count_documents({"symbol": sym, "tf": settings.INTERVAL_MINUTES})
        base = sym.split("/")[0].upper()
        if base not in latest.get("details", {}):
            latest.setdefault("details", {})[base] = {}
        latest["details"].setdefault(base, {})["candle_count"] = count
        latest["details"][base]["ready"] = count >= settings.SYMBOL_WARMUP_CANDLES

    latest.update(llm_health)
    return latest


@router.post("/signal-quality/train")
async def train_signal_quality(req: SignalQualityTrainRequest):
    db = get_db()
    result = train_signal_quality_model(
        db,
        lookback_days=req.lookback_days,
        horizon_min=req.horizon_min,
        min_samples=req.min_samples,
    )
    if not result.get("ok"):
        raise HTTPException(status_code=400, detail=result)
    db.bot_events.insert_one(
        {
            "run_id": "system",
            "t": datetime.now(timezone.utc).isoformat(),
            "level": "info",
            "msg": "signal_quality_trained",
            "data": result,
        }
    )
    return result


@router.get("/signal-quality/latest")
async def get_signal_quality_latest():
    db = get_db()
    doc = db.signal_quality_models.find_one(sort=[("trained_at", -1)])
    if not doc:
        return {"latest": None}
    doc["_id"] = str(doc["_id"])
    return {"latest": doc}


@router.get("/signal-quality/score")
async def get_signal_quality_score(symbol: str, side: str):
    db = get_db()
    sym = str(symbol or "").strip().upper()
    s = str(side or "").strip().upper()
    if "/" not in sym:
        sym = f"{sym}/USDT"
    if s not in {"BUY", "SELL"}:
        raise HTTPException(status_code=400, detail="side must be BUY or SELL")
    out = score_signal_quality(db, sym, s)
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out


@router.get("/signal-quality/shadow-report")
async def get_signal_quality_shadow_report(
    run_id: Optional[str] = None,
    lookback_hours: int = 48,
    horizon_min: Optional[int] = None,
    limit: int = 3000,
    actions: str = "shadow",
):
    db = get_db()
    rid = _resolve_run_id(run_id)
    if not rid:
        return {"run_id": None, "summary": {}, "counts": {}, "samples": []}

    lookback_h = max(1, min(int(lookback_hours), 24 * 90))
    horizon = max(5, min(int(horizon_min or settings.SIGNAL_QUALITY_SHADOW_HORIZON_MIN), 7 * 24 * 60))
    lim = max(100, min(int(limit), 10000))
    since_dt = datetime.now(timezone.utc) - timedelta(hours=lookback_h)
    since_iso = since_dt.isoformat()

    allowed_actions = {"shadow", "policy", "executed"}
    action_list = [a.strip().lower() for a in str(actions or "shadow").split(",") if a.strip()]
    action_list = [a for a in action_list if a in allowed_actions]
    if not action_list:
        action_list = ["shadow"]

    q = {"run_id": rid, "t": {"$gte": since_iso}}
    rows = list(db.bot_signals.find(q, {"_id": 0}).sort("t", -1).limit(lim))
    policy = [x for x in rows if str(x.get("action", "")).lower() == "policy"]
    blocked = [x for x in rows if str(x.get("action", "")).lower() == "blocked"]
    shadow = [x for x in rows if str(x.get("action", "")).lower() == "shadow"]
    executed = [x for x in rows if str(x.get("action", "")).lower() == "executed"]

    # Prefix counters for policy decision transparency.
    def _prefix_counter(items: list[dict], prefixes: list[str]) -> dict:
        out = {p: 0 for p in prefixes}
        for it in items:
            d = str(it.get("detail", "") or "")
            for p in prefixes:
                if d.startswith(p):
                    out[p] += 1
        return out

    policy_counts = _prefix_counter(
        policy,
        [
            "quality_prob_pass",
            "quality_prob_throttle",
            "llm_allowlist_pass",
            "llm_allowlist_block",
            "llm_direction_pass",
            "llm_direction_block",
            "llm_degraded_pass",
            "llm_degraded_throttle",
            "llm_degraded_block",
        ],
    )
    blocked_counts = _prefix_counter(
        blocked,
        ["quality_prob", "not in recommendations", "rec direction", "llm_degraded", "sentiment", "intel", "funding_rate", "oi_drop", "pf_guard"],
    )

    # Deduplicate by (symbol, side, t) so the same candidate isn't evaluated multiple
    # times across action types. Prefer executed > shadow > policy for canonical record.
    action_priority = {"policy": 1, "shadow": 2, "executed": 3}
    dedup_map = {}
    for r in rows:
        a = str(r.get("action", "")).lower()
        if a not in allowed_actions:
            continue
        key = (str(r.get("symbol") or "").strip(), str(r.get("side") or "").strip().upper(), str(r.get("t") or ""))
        if not key[0] or key[1] not in {"BUY", "SELL"} or not key[2]:
            continue
        prev = dedup_map.get(key)
        if prev is None or action_priority.get(a, 0) > action_priority.get(str(prev.get("action", "")).lower(), 0):
            dedup_map[key] = r
    dedup_rows = list(dedup_map.values())

    db.signal_quality_shadow_eval.create_index(
        [("run_id", 1), ("horizon_min", 1), ("symbol", 1), ("side", 1), ("t", 1)],
        unique=True,
        background=True,
    )

    def _get_cached_ret(s: dict) -> Optional[float]:
        doc = db.signal_quality_shadow_eval.find_one(
            {
                "run_id": rid,
                "horizon_min": horizon,
                "symbol": str(s.get("symbol") or "").strip(),
                "side": str(s.get("side") or "").strip().upper(),
                "t": str(s.get("t") or ""),
            },
            {"_id": 0, "ret_h": 1},
        )
        if not doc:
            return None
        try:
            return float(doc.get("ret_h"))
        except Exception:
            return None

    # Hypothetical outcome on shadow signals from market candles (fallback).
    # ret_h = directional return at horizon based on first candle close >= t+horizon.
    def _compute_ret_h(s: dict) -> tuple[Optional[float], Optional[float], str]:
        try:
            sym = str(s.get("symbol") or "").strip()
            side = str(s.get("side") or "").strip().upper()
            p0 = float(s.get("price"))
            t_raw = str(s.get("t") or "")
            t0 = _parse_iso_utc_maybe(t_raw)
            if t0 is None:
                return None, None, "invalid_t"
            if not sym or side not in {"BUY", "SELL"} or p0 <= 0:
                return None, None, "invalid"

            key_ret = f"ret_{horizon}m"
            so = db.signal_outcomes.find_one(
                {"symbol": sym, "side": side, "signal_t": t_raw, key_ret: {"$exists": True, "$ne": None}},
                {"_id": 0, key_ret: 1, f"px_{horizon}m": 1},
            )
            if so and so.get(key_ret) is not None:
                ret = float(so.get(key_ret))
                px = so.get(f"px_{horizon}m")
                px_h = float(px) if px is not None else None
                return ret, px_h, "signal_outcomes"

            # IBKR/cross-asset candles are often stored on H1 while crypto may run on M1.
            # Try runtime tf first, then common fallbacks to keep shadow eval consistent
            # across account universes.
            tf_candidates = []
            try:
                tf_candidates.append(int(settings.INTERVAL_MINUTES))
            except Exception:
                tf_candidates.append(60)
            for _tf in (60, 1):
                if _tf not in tf_candidates:
                    tf_candidates.append(_tf)
            target = (t0 + timedelta(minutes=horizon)).isoformat()
            c = None
            for tf in tf_candidates:
                c = db.market_candles.find_one(
                    {"symbol": sym, "tf": tf, "t": {"$gte": target}},
                    {"_id": 0, "c": 1},
                    sort=[("t", 1)],
                )
                if c:
                    break
            if not c:
                return None, None, "no_candle"
            px_h = float(c.get("c"))
            direction = 1.0 if side == "BUY" else -1.0
            ret = ((px_h - p0) / p0) * direction
            return ret, px_h, "market_candles"
        except Exception:
            return None, None, "error"

    sample_rows = []
    rets = []
    wins = 0
    eval_counts_by_action = {a: 0 for a in allowed_actions}
    eval_pool = [s for s in dedup_rows if str(s.get("action", "")).lower() in set(action_list)]
    for s in eval_pool[: min(len(eval_pool), 5000)]:
        try:
            sym = str(s.get("symbol") or "").strip()
            side = str(s.get("side") or "").strip().upper()
            p0 = float(s.get("price"))
            t_raw = str(s.get("t") or "")
            action_name = str(s.get("action") or "").lower()
            if not sym or side not in {"BUY", "SELL"} or p0 <= 0:
                continue
            ret = _get_cached_ret(s)
            px = None
            source = "cache"
            if ret is None:
                ret, px, source = _compute_ret_h(s)
            if ret is None:
                continue
            rets.append(ret)
            if ret > 0:
                wins += 1
            if action_name in eval_counts_by_action:
                eval_counts_by_action[action_name] += 1
            if source != "cache":
                db.signal_quality_shadow_eval.update_one(
                    {
                        "run_id": rid,
                        "horizon_min": horizon,
                        "symbol": sym,
                        "side": side,
                        "t": t_raw,
                    },
                    {
                        "$set": {
                            "ret_h": float(ret),
                            "entry_price": p0,
                            "px_h": px,
                            "source": source,
                            "updated_at": datetime.now(timezone.utc),
                        },
                        "$setOnInsert": {"created_at": datetime.now(timezone.utc)},
                    },
                    upsert=True,
                )
            if len(sample_rows) < 20:
                sample_rows.append(
                    {
                        "t": t_raw,
                        "symbol": sym,
                        "side": side,
                        "entry_price": p0,
                        "px_h": px,
                        "ret_h": round(ret, 6),
                    }
                )
        except Exception:
            continue

    n = len(rets)
    avg_ret = float(sum(rets) / n) if n else 0.0
    gross_profit = sum(x for x in rets if x > 0)
    gross_loss = abs(sum(x for x in rets if x < 0))
    pf = (gross_profit / gross_loss) if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)
    wr = (wins / n) if n else 0.0

    return {
        "run_id": rid,
        "window": {"lookback_hours": lookback_h, "horizon_min": horizon, "signals_limit": lim, "actions": action_list},
        "counts": {
            "total": len(rows),
            "total_dedup": len(dedup_rows),
            "policy": len(policy),
            "blocked": len(blocked),
            "shadow": len(shadow),
            "executed": len(executed),
            "eval_input": len(eval_pool),
            "eval_dedup_dropped": max(0, len(rows) - len(dedup_rows)),
            "policy_by_prefix": policy_counts,
            "blocked_by_prefix": blocked_counts,
        },
        "summary": {
            "shadow_eval_samples": n,
            "eval_samples_by_action": eval_counts_by_action,
            "shadow_win_rate_h": round(wr, 4),
            "shadow_profit_factor_h": round(pf, 4) if pf != float("inf") else "inf",
            "shadow_avg_ret_h": round(avg_ret, 6),
        },
        "samples": sample_rows,
    }


@router.post("/signal-quality/shadow-backfill")
async def post_signal_quality_shadow_backfill(
    run_id: Optional[str] = None,
    lookback_days: int = 30,
    horizon_min: Optional[int] = None,
    limit: int = 20000,
    actions: str = "shadow",
):
    db = get_db()
    rid = _resolve_run_id(run_id)
    if not rid:
        raise HTTPException(status_code=400, detail="run_id not found")

    horizon = max(5, min(int(horizon_min or settings.SIGNAL_QUALITY_SHADOW_HORIZON_MIN), 7 * 24 * 60))
    lb_days = max(1, min(int(lookback_days), 3650))
    lim = max(100, min(int(limit), 200000))
    since_dt = datetime.now(timezone.utc) - timedelta(days=lb_days)
    since_iso = since_dt.isoformat()

    db.signal_quality_shadow_eval.create_index(
        [("run_id", 1), ("horizon_min", 1), ("symbol", 1), ("side", 1), ("t", 1)],
        unique=True,
        background=True,
    )

    allowed_actions = {"shadow", "policy", "executed"}
    action_list = [a.strip().lower() for a in str(actions or "shadow").split(",") if a.strip()]
    action_list = [a for a in action_list if a in allowed_actions]
    if not action_list:
        action_list = ["shadow"]

    rows = list(
        db.bot_signals.find(
            {"run_id": rid, "action": {"$in": action_list}, "t": {"$gte": since_iso}},
            {"_id": 0},
        ).sort("t", -1).limit(lim)
    )
    action_priority = {"policy": 1, "shadow": 2, "executed": 3}
    dedup_map = {}
    for r in rows:
        a = str(r.get("action", "")).lower()
        key = (str(r.get("symbol") or "").strip(), str(r.get("side") or "").strip().upper(), str(r.get("t") or ""))
        if not key[0] or key[1] not in {"BUY", "SELL"} or not key[2]:
            continue
        prev = dedup_map.get(key)
        if prev is None or action_priority.get(a, 0) > action_priority.get(str(prev.get("action", "")).lower(), 0):
            dedup_map[key] = r
    dedup_rows = list(dedup_map.values())

    tf_candidates = []
    try:
        tf_candidates.append(int(settings.INTERVAL_MINUTES))
    except Exception:
        tf_candidates.append(60)
    for _tf in (60, 1):
        if _tf not in tf_candidates:
            tf_candidates.append(_tf)
    key_ret = f"ret_{horizon}m"
    now_utc = datetime.now(timezone.utc)
    computed = 0
    skipped = 0
    from_outcomes = 0
    from_candles = 0

    for s in dedup_rows:
        try:
            sym = str(s.get("symbol") or "").strip()
            side = str(s.get("side") or "").strip().upper()
            t_raw = str(s.get("t") or "")
            p0 = float(s.get("price"))
            if not sym or side not in {"BUY", "SELL"} or p0 <= 0 or not t_raw:
                skipped += 1
                continue

            existing = db.signal_quality_shadow_eval.find_one(
                {"run_id": rid, "horizon_min": horizon, "symbol": sym, "side": side, "t": t_raw},
                {"_id": 1},
            )
            if existing:
                skipped += 1
                continue

            ret = None
            px_h = None
            src = "none"

            so = db.signal_outcomes.find_one(
                {"symbol": sym, "side": side, "signal_t": t_raw, key_ret: {"$exists": True, "$ne": None}},
                {"_id": 0, key_ret: 1, f"px_{horizon}m": 1},
            )
            if so and so.get(key_ret) is not None:
                ret = float(so.get(key_ret))
                px = so.get(f"px_{horizon}m")
                px_h = float(px) if px is not None else None
                src = "signal_outcomes"
                from_outcomes += 1
            else:
                t0 = _parse_iso_utc_maybe(t_raw)
                if t0 is None:
                    skipped += 1
                    continue
                target = (t0 + timedelta(minutes=horizon)).isoformat()
                c = None
                for tf in tf_candidates:
                    c = db.market_candles.find_one(
                        {"symbol": sym, "tf": tf, "t": {"$gte": target}},
                        {"_id": 0, "c": 1},
                        sort=[("t", 1)],
                    )
                    if c:
                        break
                if c:
                    px_h = float(c.get("c"))
                    direction = 1.0 if side == "BUY" else -1.0
                    ret = ((px_h - p0) / p0) * direction
                    src = "market_candles"
                    from_candles += 1

            if ret is None:
                skipped += 1
                continue

            db.signal_quality_shadow_eval.update_one(
                {"run_id": rid, "horizon_min": horizon, "symbol": sym, "side": side, "t": t_raw},
                {
                    "$set": {
                        "ret_h": float(ret),
                        "entry_price": p0,
                        "px_h": px_h,
                        "source": src,
                        "updated_at": now_utc,
                    },
                    "$setOnInsert": {"created_at": now_utc},
                },
                upsert=True,
            )
            computed += 1
        except Exception:
            skipped += 1
            continue

    total_cached = db.signal_quality_shadow_eval.count_documents(
        {"run_id": rid, "horizon_min": horizon, "t": {"$gte": since_iso}}
    )

    return {
        "ok": True,
        "run_id": rid,
        "window": {"lookback_days": lb_days, "horizon_min": horizon, "limit": lim, "actions": action_list},
        "rows_scanned": len(rows),
        "rows_dedup": len(dedup_rows),
        "computed": computed,
        "skipped": skipped,
        "from_signal_outcomes": from_outcomes,
        "from_market_candles": from_candles,
        "cached_total_in_window": int(total_cached),
    }


@router.get("/funding")
async def get_funding(symbol: str = "BTC/USDT"):
    db = get_db()
    latest = db.funding_oi.find_one({"symbol": symbol}, sort=[("timestamp", -1)])
    if not latest:
        return {"latest": None, "history": []}

    # Posledních 24 záznamů (~2h historie při 5min pollingu)
    history = list(
        db.funding_oi.find(
            {"symbol": symbol},
            {"_id": 0}
        ).sort("timestamp", -1).limit(24)
    )
    for doc in history:
        if hasattr(doc.get("timestamp"), "isoformat"):
            doc["timestamp"] = doc["timestamp"].isoformat()

    latest["_id"] = str(latest["_id"])
    if hasattr(latest.get("timestamp"), "isoformat"):
        latest["timestamp"] = latest["timestamp"].isoformat()

    history.reverse()
    return {"latest": latest, "history": history}


@router.get("/market-data")
async def get_market_data():
    db = get_db()
    latest = db.market_metrics.find_one(sort=[("timestamp", -1)])
    if not latest:
        return {"latest": None, "history": []}

    # Posledních 12 záznamů (~1h historie při 5min pollingu)
    history = list(
        db.market_metrics.find({}, {"_id": 0}).sort("timestamp", -1).limit(12)
    )
    for doc in history:
        if hasattr(doc.get("timestamp"), "isoformat"):
            doc["timestamp"] = doc["timestamp"].isoformat()
    history.reverse()

    latest["_id"] = str(latest["_id"])
    if hasattr(latest.get("timestamp"), "isoformat"):
        latest["timestamp"] = latest["timestamp"].isoformat()

    return {"latest": latest, "history": history}


@router.get("/data-coverage")
async def get_data_coverage(days: int = 60, tf: int = 60):
    db = get_db()
    lookback_days = max(1, min(int(days), 3650))
    tf = int(tf)
    since_dt = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    since_iso = since_dt.isoformat()

    symbols = sorted(
        set(_parse_symbols(settings.SYMBOLS))
        | set(_parse_symbols(settings.BINANCE_SYMBOLS))
        | set(_parse_symbols(settings.ALWAYS_ACTIVE_SYMBOLS))
    )
    if not symbols:
        symbols = sorted(db.market_candles.distinct("symbol", {"tf": tf}))

    rows = []
    now_utc = datetime.now(timezone.utc)
    def _hours_ago(ts: Optional[datetime]) -> Optional[float]:
        if not isinstance(ts, datetime):
            return None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        return round(max(0.0, (now_utc - ts).total_seconds() / 3600.0), 2)

    for sym in symbols:
        base = sym.split("/")[0].upper()
        candle_hours = set()
        intel_hours = set()
        funding_hours = set()
        sentiment_hours = set()

        for c in db.market_candles.find(
            {"symbol": sym, "tf": tf, "t": {"$gte": since_iso}},
            {"_id": 0, "t": 1},
        ):
            hk = _hour_key_from_candle_t(str(c.get("t", "")))
            if hk:
                candle_hours.add(hk)

        for f in db.funding_oi.find(
            {"symbol": sym, "timestamp": {"$gte": since_dt}},
            {"_id": 0, "timestamp": 1},
        ):
            ts = f.get("timestamp")
            if isinstance(ts, datetime):
                funding_hours.add(_hour_key_from_dt(ts))

        intel_q = {"created_at": {"$gte": since_dt}, f"assets.{base}": {"$exists": True}}
        for i in db.market_intel.find(intel_q, {"_id": 0, "created_at": 1}):
            ts = i.get("created_at")
            if isinstance(ts, datetime):
                intel_hours.add(_hour_key_from_dt(ts))

        for s in db.sentiments.find(
            {"symbols": base, "created_at": {"$gte": since_dt}},
            {"_id": 0, "created_at": 1},
        ):
            ts = s.get("created_at")
            if isinstance(ts, datetime):
                sentiment_hours.add(_hour_key_from_dt(ts))

        candle_n = len(candle_hours)
        intel_cov = (len(candle_hours & intel_hours) / candle_n * 100.0) if candle_n > 0 else 0.0
        funding_cov = (len(candle_hours & funding_hours) / candle_n * 100.0) if candle_n > 0 else 0.0
        sentiment_cov = (len(candle_hours & sentiment_hours) / candle_n * 100.0) if candle_n > 0 else 0.0

        last_funding = db.funding_oi.find_one({"symbol": sym}, sort=[("timestamp", -1)])
        last_intel = db.market_intel.find_one({f"assets.{base}": {"$exists": True}}, sort=[("created_at", -1)])
        last_sentiment = db.sentiments.find_one({"symbols": base}, sort=[("created_at", -1)])
        # News is usually market-wide, not symbol-specific; compute once per symbol for easy UI mapping.
        last_news = db.news.find_one(sort=[("published_at", -1), ("created_at", -1)])
        news_count = db.news.count_documents({"created_at": {"$gte": since_dt}})
        sentiment_count = db.sentiments.count_documents({"symbols": base, "created_at": {"$gte": since_dt}})

        last_news_ts = None
        if last_news:
            pn = last_news.get("published_at")
            cn = last_news.get("created_at")
            last_news_ts = pn if isinstance(pn, datetime) else (cn if isinstance(cn, datetime) else None)

        rows.append(
            {
                "symbol": sym,
                "candle_hours": candle_n,
                "intel_hours": len(intel_hours),
                "funding_hours": len(funding_hours),
                "sentiment_hours": len(sentiment_hours),
                "news_items": int(news_count),
                "sentiment_items": int(sentiment_count),
                "intel_coverage_pct": round(intel_cov, 1),
                "funding_coverage_pct": round(funding_cov, 1),
                "sentiment_coverage_pct": round(sentiment_cov, 1),
                "last_intel_at": last_intel.get("created_at").isoformat() if last_intel and hasattr(last_intel.get("created_at"), "isoformat") else None,
                "last_funding_at": last_funding.get("timestamp").isoformat() if last_funding and hasattr(last_funding.get("timestamp"), "isoformat") else None,
                "last_sentiment_at": last_sentiment.get("created_at").isoformat() if last_sentiment and hasattr(last_sentiment.get("created_at"), "isoformat") else None,
                "last_news_at": last_news_ts.isoformat() if last_news_ts and hasattr(last_news_ts, "isoformat") else None,
                "intel_staleness_h": _hours_ago(last_intel.get("created_at") if last_intel else None),
                "funding_staleness_h": _hours_ago(last_funding.get("timestamp") if last_funding else None),
                "sentiment_staleness_h": _hours_ago(last_sentiment.get("created_at") if last_sentiment else None),
                "news_staleness_h": _hours_ago(last_news_ts),
            }
        )

    return {
        "lookback_days": lookback_days,
        "tf": tf,
        "symbols": rows,
    }


# ─── Market data endpoint ──────────────────────────────────────

market_router = APIRouter(prefix="/market", tags=["market"])


@market_router.get("/candles")
async def get_candles(
    symbol: str = "BTC/USDT",
    tf: int = 60,
    limit: int = 300,
):
    lim = max(10, min(int(limit), 3000))
    sym = str(symbol or "").strip().upper()
    cache_k = _cache_key("market_candles", sym, tf, lim)
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    db = get_db()
    candles = list(
        db.market_candles.find(
            {"symbol": sym, "tf": int(tf)},
            {"_id": 0, "t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}
        )
        .sort("t", -1)
        .limit(lim)
    )
    candles.reverse()
    return _cache_put(cache_k, candles, ttl_sec=20.0)


@market_router.get("/symbols")
async def get_symbols(tf: int = 60):
    """Return all collected symbols from MongoDB for a given timeframe."""
    cache_k = _cache_key("market_symbols", tf)
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    db = get_db()
    tf = int(tf)

    # Fast path: start with configured universe so dashboard tabs can render
    # immediately even before the DB scan finishes on cold start.
    configured = (
        set(_parse_symbols(getattr(settings, "SYMBOLS", "") or ""))
        | set(_parse_symbols(getattr(settings, "BINANCE_SYMBOLS", "") or ""))
        | set(_parse_symbols(getattr(settings, "ALWAYS_ACTIVE_SYMBOLS", "") or ""))
        | set(_parse_symbols(getattr(settings, "IBKR_SYMBOLS", "") or ""))
    )

    symbols = set()
    try:
        pipeline = [
            {"$match": {"tf": tf, "symbol": {"$type": "string", "$ne": ""}}},
            {"$group": {"_id": "$symbol"}},
            {"$sort": {"_id": 1}},
        ]
        rows = db.market_candles.aggregate(pipeline, hint="symbol_1_tf_1_t_1")
        symbols.update(str(r.get("_id") or "").strip() for r in rows if r.get("_id"))
    except Exception:
        try:
            symbols.update(db.market_candles.distinct("symbol", {"tf": tf, "symbol": {"$ne": None}}))
        except Exception:
            pass

    symbols.update(s for s in configured if s)
    payload = {"symbols": sorted(s for s in symbols if s)}
    return _cache_put(cache_k, payload, ttl_sec=300.0)


# ─── Sentiment endpoints ───────────────────────────────────────

sentiment_router = APIRouter(prefix="/sentiment", tags=["sentiment"])


@sentiment_router.get("/recent")
async def get_recent_sentiments(symbol: str = "BTC", limit: int = 20):
    lim = max(1, min(int(limit), 200))
    cache_k = _cache_key("sentiment_recent", symbol, lim)
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    db = get_db()
    base = symbol.split("/")[0].upper()
    docs = list(
        db.sentiments.find(
            {"symbols": base},
            {"_id": 0}
        )
        .sort("created_at", -1)
        .limit(lim)
    )
    for d in docs:
        if "created_at" in d and hasattr(d["created_at"], "isoformat"):
            d["created_at"] = d["created_at"].isoformat()
    return _cache_put(cache_k, docs, ttl_sec=10.0)


@sentiment_router.get("/summary")
async def get_sentiment_summary(symbol: str = "BTC", window: int = 60):
    win = max(5, min(int(window), 24 * 60))
    cache_k = _cache_key("sentiment_summary", symbol, win)
    cache_hit = _cache_get(cache_k)
    if cache_hit is not None:
        return cache_hit
    db = get_db()
    from datetime import timedelta
    base = symbol.split("/")[0].upper()
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=win)
    docs = list(db.sentiments.find(
        {"symbols": base, "created_at": {"$gte": cutoff}},
        {"sentiment": 1}
    ))
    counts = {"Positive": 0, "Neutral": 0, "Negative": 0}
    for d in docs:
        s = d.get("sentiment", "Neutral")
        if s in counts:
            counts[s] += 1
    total = sum(counts.values())
    dominant = max(counts, key=counts.get) if total > 0 else "Neutral"
    return _cache_put(cache_k, {**counts, "total": total, "dominant": dominant}, ttl_sec=10.0)


@sentiment_router.get("/intel")
async def get_intel():
    db = get_db()
    doc = db.market_intel.find_one(sort=[("created_at", -1)])
    if not doc:
        return {"overall": "NEUTRAL", "assets": {}, "created_at": None, "llm_ok": None, "degraded": False, "last_error": None}
    raw = str(doc.get("raw", "") or "")
    failed = raw.startswith("LLM_FAILED:")
    last_error = raw.split("LLM_FAILED:", 1)[1].strip() if failed else None
    doc["_id"] = str(doc["_id"])
    if hasattr(doc.get("created_at"), "isoformat"):
        doc["created_at"] = doc["created_at"].isoformat()
    doc["llm_ok"] = not failed
    doc["degraded"] = failed
    doc["last_error"] = last_error
    return doc


@sentiment_router.get("/reaction-forecast")
async def get_reaction_forecast(symbol: str = "BTC/USDT", lookback_days: int = 120, sentiment_window: int = 180):
    db = get_db()
    return forecast_symbol_reaction(
        db=db,
        symbol=symbol,
        lookback_days=lookback_days,
        sentiment_window_minutes=sentiment_window,
    )
