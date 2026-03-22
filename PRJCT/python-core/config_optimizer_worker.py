from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict

from config_optimizer import apply_overrides, optimize_from_mongo, passes_apply_guard
from llama_wrapper import run_llama_structured
from trading.config import settings
from trading.mongo import get_db


def _pick_with_llm(payload: Dict[str, Any]) -> Dict[str, Any]:
    top = payload.get("top", [])
    if not top:
        return payload["best"]
    top_compact = []
    for idx, cand in enumerate(top, start=1):
        top_compact.append(
            {
                "index": idx,
                "score": cand.get("score"),
                "oos_guard_passed": cand.get("oos_guard_passed"),
                "summary": cand.get("summary", {}),
                "train_summary": cand.get("train_summary", {}),
                "overrides": cand.get("overrides", {}),
            }
        )
    guard_constraints = {
        "min_win_rate": float(settings.AUTO_TUNE_MIN_WIN_RATE),
        "min_profit_factor": float(settings.AUTO_TUNE_MIN_PROFIT_FACTOR),
        "min_final_equity": float(settings.AUTO_TUNE_MIN_FINAL_EQUITY),
        "min_trades": int(settings.AUTO_TUNE_MIN_TRADES),
    }
    runtime_constraints = {
        "risk_per_trade": float(settings.RISK_PER_TRADE),
        "max_dynamic_symbols": int(settings.MAX_DYNAMIC_SYMBOLS),
        "always_active_symbols": str(settings.ALWAYS_ACTIVE_SYMBOLS),
        "intel_block_low_conf": bool(settings.INTEL_BLOCK_LOW_CONF),
        "llm_degraded_action": str(settings.LLM_DEGRADED_ACTION),
    }
    prompt = (
        "You are a trading config optimizer. Choose ONE candidate index that best balances:\n"
        "1) profit factor > 1, 2) win rate >= 0.5, 3) final_equity > 1000, 4) robust number of trades.\n"
        "Prefer defensive candidates when market is weak. Do not choose candidates that materially increase risk.\n"
        f"Guard constraints: {json.dumps(guard_constraints, ensure_ascii=True)}\n"
        f"Runtime constraints: {json.dumps(runtime_constraints, ensure_ascii=True)}\n"
        "Return strict JSON only: {\"choice\": <index 1..N>, \"reason\": \"short\"}\n"
        f"Candidates JSON:\n{json.dumps(top_compact, ensure_ascii=True)}\n"
    )
    raw = run_llama_structured(prompt, max_tokens=120, timeout_sec=180)
    try:
        obj = json.loads(raw)
        idx = int(obj.get("choice", 1)) - 1
        if 0 <= idx < len(top):
            selected = dict(top[idx])
            selected["llm_reason"] = str(obj.get("reason", "")).strip()
            return selected
    except Exception:
        pass
    return payload["best"]


def run_once() -> None:
    db = get_db()
    payload = None
    try:
        payload = asyncio.run(optimize_from_mongo(db, interval=settings.INTERVAL_MINUTES))
    except Exception as e:
        db.bot_events.insert_one(
            {
                "run_id": "system",
                "t": datetime.now(timezone.utc).isoformat(),
                "level": "error",
                "msg": "auto_tune_failed",
                "data": {"err": str(e)},
            }
        )
        return

    selected = _pick_with_llm(payload)
    summary = selected.get("summary", {})
    overrides = selected.get("overrides", {})
    can_apply = passes_apply_guard(summary)
    applied = {}

    if settings.AUTO_TUNE_APPLY and can_apply:
        applied = apply_overrides(overrides)

    rec_doc = {
        "created_at": datetime.now(timezone.utc),
        "source": "config_optimizer_worker",
        "range": payload.get("range"),
        "symbols": payload.get("symbols", []),
        "best": payload.get("best"),
        "selected": selected,
        "apply_guard_passed": can_apply,
        "applied": applied,
        "auto_apply_enabled": settings.AUTO_TUNE_APPLY,
    }
    db.config_recommendations.insert_one(rec_doc)

    db.bot_events.insert_one(
        {
            "run_id": "system",
            "t": datetime.now(timezone.utc).isoformat(),
            "level": "info",
            "msg": "auto_tune_completed",
            "data": {
                "selected_overrides": overrides,
                "apply_guard_passed": can_apply,
                "applied": applied,
                "score": selected.get("score"),
            },
        }
    )
