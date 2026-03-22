from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import product
from typing import Any, Dict, List

from trading.backtest import MultiBacktestRunner
from trading.config import settings


@dataclass
class CandidateResult:
    overrides: Dict[str, Any]
    summary: Dict[str, Any]
    score: float


def _symbols_for_interval(db, interval: int) -> List[str]:
    syms = sorted(db.market_candles.distinct("symbol", {"tf": interval, "symbol": {"$ne": None}}))
    if syms:
        return syms
    seen = set()
    out: List[str] = []
    for src in (settings.SYMBOLS, settings.BINANCE_SYMBOLS, settings.ALWAYS_ACTIVE_SYMBOLS):
        for s in src.split(","):
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                out.append(s)
    return out


def _build_candidates(max_evals: int) -> List[Dict[str, Any]]:
    grid = {
        "BREAKOUT_N": [7, 10, 14],
        "RISK_PER_TRADE": [0.003, 0.004, 0.005],
        "SL_ATR_MULT": [1.5, 1.8, 2.0],
        "TP_ATR_MULT": [2.0, 2.5, 3.0],
        "VOL_MULT": [1.3, 1.5],
        "COOLDOWN_CANDLES": [1, 2],
    }
    keys = list(grid.keys())
    out: List[Dict[str, Any]] = []
    for vals in product(*[grid[k] for k in keys]):
        out.append({k: v for k, v in zip(keys, vals)})
        if len(out) >= max(1, int(max_evals)):
            break
    return out


def _score(summary: Dict[str, Any], min_trades: int) -> float:
    trades = int(summary.get("total_trades", 0) or 0)
    win_rate = float(summary.get("win_rate", 0) or 0)
    pf = float(summary.get("profit_factor", 0) or 0)
    final_equity = float(summary.get("final_equity", 0) or 0)
    cash_buffer = float(summary.get("cash_buffer", 0) or 0)
    max_dd = abs(float(summary.get("max_drawdown", 0) or 0))
    # Base objective with drawdown penalty.
    base = (pf * 120.0) + (win_rate * 80.0) + ((final_equity - 1000.0) * 0.25) + (cash_buffer * 0.15) - (max_dd * 20.0)
    # Strong penalties when candidate is below apply-guard thresholds.
    min_wr = float(settings.AUTO_TUNE_MIN_WIN_RATE)
    min_pf = float(settings.AUTO_TUNE_MIN_PROFIT_FACTOR)
    min_eq = float(settings.AUTO_TUNE_MIN_FINAL_EQUITY)
    penalty = 0.0
    if trades < min_trades:
        penalty += float(min_trades - trades) * 20.0
    if win_rate < min_wr:
        penalty += float(min_wr - win_rate) * 400.0
    if pf < min_pf:
        penalty += float(min_pf - pf) * 500.0
    if final_equity < min_eq:
        penalty += float(min_eq - final_equity) * 1.5
    return base - penalty


def _aggregate_summaries(items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not items:
        return {
            "total_trades": 0,
            "win_rate": 0.0,
            "profit_factor": 0.0,
            "final_equity": 1000.0,
            "cash_buffer": 0.0,
            "max_drawdown": 0.0,
            "total_pnl": 0.0,
        }
    n = float(len(items))
    return {
        "total_trades": int(sum(int(x.get("total_trades", 0) or 0) for x in items)),
        "win_rate": float(sum(float(x.get("win_rate", 0) or 0) for x in items) / n),
        "profit_factor": float(sum(float(x.get("profit_factor", 0) or 0) for x in items) / n),
        "final_equity": float(sum(float(x.get("final_equity", 1000) or 1000) for x in items) / n),
        "cash_buffer": float(sum(float(x.get("cash_buffer", 0) or 0) for x in items) / n),
        "max_drawdown": float(max(abs(float(x.get("max_drawdown", 0) or 0)) for x in items)),
        "total_pnl": float(sum(float(x.get("total_pnl", 0) or 0) for x in items)),
    }


def _build_walk_forward_windows(dt_from: datetime, dt_to: datetime) -> List[Dict[str, datetime]]:
    total = (dt_to - dt_from).total_seconds()
    if total <= 0:
        return []
    # 3 windows:
    # A: train [0-60%], oos [60-75%]
    # B: train [20-75%], oos [75-90%]
    # C: train [0-75%], oos [75-100%]
    def _at(r: float) -> datetime:
        return dt_from + timedelta(seconds=total * r)

    a_train_from, a_train_to, a_oos_to = _at(0.00), _at(0.60), _at(0.75)
    b_train_from, b_train_to, b_oos_to = _at(0.20), _at(0.75), _at(0.90)
    c_train_from, c_train_to, c_oos_to = _at(0.00), _at(0.75), _at(1.00)
    windows = [
        {"name": "A", "train_from": a_train_from, "train_to": a_train_to, "oos_from": a_train_to, "oos_to": a_oos_to},
        {"name": "B", "train_from": b_train_from, "train_to": b_train_to, "oos_from": b_train_to, "oos_to": b_oos_to},
        {"name": "C", "train_from": c_train_from, "train_to": c_train_to, "oos_from": c_train_to, "oos_to": c_oos_to},
    ]
    # Keep only valid windows (minimum span 7 days train, 14 days oos).
    out = []
    for w in windows:
        train_days = (w["train_to"] - w["train_from"]).total_seconds() / 86400.0
        oos_days = (w["oos_to"] - w["oos_from"]).total_seconds() / 86400.0
        if train_days >= 7 and oos_days >= 14:
            out.append(w)
    return out


async def _run_summary(
    symbols: List[str],
    interval: int,
    dt_from: datetime,
    dt_to: datetime,
    overrides: Dict[str, Any],
) -> Dict[str, Any]:
    runner = MultiBacktestRunner(
        source="mongo",
        symbols=symbols,
        dt_from=dt_from,
        dt_to=dt_to,
        interval=interval,
        with_sentiment=False,
        overrides=overrides,
        mode="exact",
    )
    result = await runner.run()
    return {
        "total_trades": result.total_trades,
        "win_rate": result.win_rate,
        "profit_factor": result.profit_factor,
        "final_equity": result.final_equity,
        "cash_buffer": result.cash_buffer,
        "max_drawdown": result.max_drawdown,
        "total_pnl": result.total_pnl,
        "symbols": len(result.symbols),
    }


async def optimize_from_mongo(db, interval: int = 60) -> Dict[str, Any]:
    dt_to = datetime.now(timezone.utc)
    dt_from = dt_to - timedelta(days=max(7, int(settings.AUTO_TUNE_LOOKBACK_DAYS)))
    symbols = _symbols_for_interval(db, interval)
    if not symbols:
        raise RuntimeError("No symbols available for optimizer.")

    candidates = _build_candidates(settings.AUTO_TUNE_MAX_EVALS)
    windows = _build_walk_forward_windows(dt_from, dt_to)
    if not windows:
        raise RuntimeError("Walk-forward windows are invalid for selected range.")

    results: List[Dict[str, Any]] = []

    for i, overrides in enumerate(candidates, start=1):
        train_items: List[Dict[str, Any]] = []
        oos_items: List[Dict[str, Any]] = []
        per_window: List[Dict[str, Any]] = []
        for w in windows:
            train_summary = await _run_summary(
                symbols=symbols,
                interval=interval,
                dt_from=w["train_from"],
                dt_to=w["train_to"],
                overrides=overrides,
            )
            oos_summary = await _run_summary(
                symbols=symbols,
                interval=interval,
                dt_from=w["oos_from"],
                dt_to=w["oos_to"],
                overrides=overrides,
            )
            train_items.append(train_summary)
            oos_items.append(oos_summary)
            per_window.append(
                {
                    "name": w["name"],
                    "train_range": {"from": w["train_from"].isoformat(), "to": w["train_to"].isoformat()},
                    "oos_range": {"from": w["oos_from"].isoformat(), "to": w["oos_to"].isoformat()},
                    "train": train_summary,
                    "oos": oos_summary,
                }
            )

        train_agg = _aggregate_summaries(train_items)
        oos_agg = _aggregate_summaries(oos_items)
        per_window_oos_guard = all(passes_apply_guard(x.get("oos", {})) for x in per_window) if per_window else False
        oos_guard_passed = passes_apply_guard(oos_agg) and per_window_oos_guard
        score = _score(oos_agg, settings.AUTO_TUNE_MIN_TRADES)
        if not oos_guard_passed:
            score -= 1e6
        if not per_window_oos_guard:
            score -= 2e5

        row = {
            "overrides": overrides,
            "summary": oos_agg,
            "train_summary": train_agg,
            "walk_forward": per_window,
            "oos_guard_passed": oos_guard_passed,
            "per_window_oos_guard_passed": per_window_oos_guard,
            "score": round(score, 4),
        }
        results.append(row)
        print(
            f"[AUTO-TUNE] candidate {i}/{len(candidates)} score={score:.2f} "
            f"oos_trades={oos_agg['total_trades']} oos_pf={oos_agg['profit_factor']:.3f} "
            f"oos_guard={'PASS' if oos_guard_passed else 'FAIL'}"
        )

    ranked = sorted(results, key=lambda x: float(x.get("score", -1e9)), reverse=True)
    top = ranked[:5]
    best = top[0]
    payload = {
        "t": datetime.now(timezone.utc),
        "range": {"from": dt_from.isoformat(), "to": dt_to.isoformat(), "interval": interval},
        "walk_forward_windows": [
            {
                "name": w["name"],
                "train_from": w["train_from"].isoformat(),
                "train_to": w["train_to"].isoformat(),
                "oos_from": w["oos_from"].isoformat(),
                "oos_to": w["oos_to"].isoformat(),
            }
            for w in windows
        ],
        "symbols": symbols,
        "best": {
            "overrides": best.get("overrides", {}),
            "summary": best.get("summary", {}),
            "train_summary": best.get("train_summary", {}),
            "walk_forward": best.get("walk_forward", []),
            "oos_guard_passed": bool(best.get("oos_guard_passed")),
            "score": round(float(best.get("score", 0) or 0), 4),
        },
        "top": top,
    }
    return payload


def passes_apply_guard(summary: Dict[str, Any]) -> bool:
    return (
        float(summary.get("win_rate", 0) or 0) >= float(settings.AUTO_TUNE_MIN_WIN_RATE)
        and float(summary.get("profit_factor", 0) or 0) > float(settings.AUTO_TUNE_MIN_PROFIT_FACTOR)
        and float(summary.get("final_equity", 0) or 0) > float(settings.AUTO_TUNE_MIN_FINAL_EQUITY)
        and int(summary.get("total_trades", 0) or 0) >= int(settings.AUTO_TUNE_MIN_TRADES)
    )


def apply_overrides(overrides: Dict[str, Any]) -> Dict[str, Any]:
    applied = {}
    for k, v in overrides.items():
        ku = str(k).upper()
        if ku in settings.model_fields:
            setattr(settings, ku, v)
            applied[ku] = v
    return applied
