# trading/paper.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Dict, Tuple
from collections import deque
from trading.config import settings
from trading.fees import get_fee_rate_per_side


def _safe_float(value, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value, default: int) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)

def _iso_to_dt(s: str) -> datetime:
    if not s: return datetime.now(timezone.utc)
    try:
        if "." in s:
            base, parts = s.split(".")
            sub = "".join([c for c in parts if c.isdigit()])
            s = f"{base}.{sub[:6]}Z"
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except:
        try: return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        except: return datetime.now(timezone.utc)

def _dt_day_key_utc(t_iso: str) -> str:
    return _iso_to_dt(t_iso).strftime("%Y-%m-%d")

@dataclass
class ATRState:
    period: int = 14
    atr: Optional[float] = None
    prev_close: Optional[float] = None

class PaperExecutor:
    def __init__(self, db, run_id: str):
        self.db = db
        self.run_id = run_id

        self.spread_bps = _safe_float(settings.SPREAD_BPS, 2.0)
        self.risk_per_trade = _safe_float(settings.RISK_PER_TRADE, 0.003)
        self.daily_stop = _safe_float(settings.DAILY_STOP, 0.02)
        self.split_reinvest = _safe_float(settings.PROFIT_SPLIT_REINVEST, 0.6)

        # Exits z configu
        self.sl_atr_mult = _safe_float(settings.SL_ATR_MULT, 1.2)
        self.tp_atr_mult = _safe_float(settings.TP_ATR_MULT, 3.0)
        self.time_exit_minutes = _safe_float(settings.TIME_EXIT_MINUTES, 720.0)

        # Trailing stop
        self.trailing_stop = bool(settings.TRAILING_STOP)
        self.trail_atr_mult = _safe_float(settings.TRAIL_ATR_MULT, 1.0)
        self.trail_activation_atr = _safe_float(settings.TRAIL_ACTIVATION_ATR, 2.0)

        self._atr: Dict[str, ATRState] = {}
        self._day_state: Dict[str, Dict] = {}
        self._risk_multipliers: Dict[str, float] = {
            "pf_guard": 1.0,
            "llm_degraded": 1.0,
        }
        self._portfolio_cache = {"equity": 1000.0, "cash_buffer": 0.0}
        self._open_positions: Dict[str, dict] = {}
        self._pf_recent = deque(maxlen=max(1, _safe_int(settings.PF_GUARD_WINDOW_TRADES, 30)))

        p = self.db.portfolio.find_one({"run_id": self.run_id})
        if not p:
            prev_runtime = self.db.portfolio.find_one(
                {"run_id": {"$not": {"$regex": "^bt-"}}},
                {"cash_buffer": 1, "_id": 0},
                sort=[("_id", -1)],
            ) or {}
            carry_buffer = float(prev_runtime.get("cash_buffer", 0.0))
            seed = {
                "run_id": self.run_id,
                "equity": 1000.0,
                "cash_buffer": round(carry_buffer, 2),
                "initial_equity": 1000.0,
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            self.db.portfolio.update_one({"run_id": self.run_id}, {"$setOnInsert": seed}, upsert=True)
            p = self.db.portfolio.find_one({"run_id": self.run_id})
        p = p or {"equity": 1000.0, "cash_buffer": 0.0}
        self._portfolio_cache = {
            "equity": float(p.get("equity", 1000.0)),
            "cash_buffer": float(p.get("cash_buffer", 0.0)),
        }
        for pos in self.db.positions.find({"run_id": self.run_id, "status": "OPEN"}):
            sym = str(pos.get("symbol", "")).strip()
            if sym:
                self._open_positions[sym] = pos
        pf_window = max(1, _safe_int(settings.PF_GUARD_WINDOW_TRADES, 30))
        rows = list(
            self.db.positions.find(
                {"run_id": self.run_id, "status": "CLOSED"},
                {"pnl": 1, "_id": 0},
            ).sort("exit_time", -1).limit(pf_window)
        )
        for r in reversed(rows):
            self._pf_recent.append(float(r.get("pnl", 0)))

    def _get_portfolio(self) -> Tuple[float, float]:
        return float(self._portfolio_cache["equity"]), float(self._portfolio_cache["cash_buffer"])

    def _set_portfolio(self, equity: float, buffer: float):
        self._portfolio_cache["equity"] = round(equity, 2)
        self._portfolio_cache["cash_buffer"] = round(buffer, 2)
        self.db.portfolio.update_one(
            {"run_id": self.run_id},
            {"$set": {"equity": self._portfolio_cache["equity"], "cash_buffer": self._portfolio_cache["cash_buffer"]}}
        )

    def _fill_price(self, side: str, mid: float) -> float:
        spread = (self.spread_bps / 10000.0) * mid
        if side == "BUY": return round(mid + spread, 2)
        return round(mid - spread, 2)

    def set_risk_multiplier(self, name: str, value: float):
        """Set external risk multiplier (0..1+)."""
        try:
            v = float(value)
        except Exception:
            v = 1.0
        if v < 0:
            v = 0.0
        self._risk_multipliers[name] = v

    def get_effective_risk_multiplier(self) -> float:
        mult = 1.0
        for v in self._risk_multipliers.values():
            mult *= float(v)
        return max(0.0, mult)

    def get_pf_guard_multiplier(self) -> float:
        """Compute rolling PF guard multiplier from latest closed trades."""
        if not settings.PF_GUARD_ENABLED:
            return 1.0

        pf_min_trades = max(1, _safe_int(settings.PF_GUARD_MIN_TRADES, 12))
        pf_hard = _safe_float(settings.PF_GUARD_HARD_THRESHOLD, 0.9)
        pf_soft = _safe_float(settings.PF_GUARD_SOFT_THRESHOLD, 1.05)
        pf_hard_mult = _safe_float(settings.PF_GUARD_HARD_RISK_MULT, 0.0)
        pf_soft_mult = _safe_float(settings.PF_GUARD_SOFT_RISK_MULT, 0.5)

        if len(self._pf_recent) < pf_min_trades:
            return 1.0

        pnls = list(self._pf_recent)
        gross_profit = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        if gross_loss <= 0:
            pf = float("inf") if gross_profit > 0 else 0.0
        else:
            pf = gross_profit / gross_loss

        if pf < pf_hard:
            return max(0.0, pf_hard_mult)
        if pf < pf_soft:
            return max(0.0, pf_soft_mult)
        return 1.0

    def _update_atr(self, symbol: str, candle: dict) -> float:
        st = self._atr.get(symbol) or ATRState(period=14)
        self._atr[symbol] = st
        h, l, c = float(candle["h"]), float(candle["l"]), float(candle["c"])
        pc = st.prev_close
        tr = max(h - l, abs(h - pc), abs(l - pc)) if pc is not None else h - l
        st.atr = tr if st.atr is None else (st.atr * 13 + tr) / 14
        st.prev_close = c
        return float(st.atr)

    async def on_candle_closed(self, symbol: str, tf: int, t: str, close: float, candle: dict = None):
        if candle is None:
            candle = self.db.market_candles.find_one({"symbol": symbol, "tf": tf, "t": t})
            if not candle:
                return
        atr = self._update_atr(symbol, candle)
        open_pos = self._open_positions.get(symbol)
        if not open_pos: return

        age_min = (_iso_to_dt(t) - _iso_to_dt(open_pos["entry_time"])).total_seconds() / 60.0
        side, sl, tp = open_pos["side"], float(open_pos["sl"]), float(open_pos["tp"])
        entry_px = float(open_pos["entry_price"])
        h, l = float(candle["h"]), float(candle["l"])

        # Trailing stop update
        if self.trailing_stop:
            trail_dist = atr * self.trail_atr_mult
            activation_dist = atr * self.trail_activation_atr
            if side == "BUY":
                # Aktivace: cena musí překročit entry + activation distance
                if h >= entry_px + activation_dist:
                    new_sl = round(h - trail_dist, 2)
                    if new_sl > sl:
                        sl = new_sl
                        self.db.positions.update_one(
                            {"_id": open_pos["_id"]}, {"$set": {"sl": sl}}
                        )
            else:  # SELL
                if l <= entry_px - activation_dist:
                    new_sl = round(l + trail_dist, 2)
                    if new_sl < sl:
                        sl = new_sl
                        self.db.positions.update_one(
                            {"_id": open_pos["_id"]}, {"$set": {"sl": sl}}
                        )

        exit_reason = None
        if side == "BUY":
            if l <= sl: exit_reason = "trailing_stop" if sl > float(open_pos.get("original_sl", sl)) else "stop_loss"
            elif h >= tp: exit_reason = "take_profit"
        else:
            if h >= sl: exit_reason = "trailing_stop" if sl < float(open_pos.get("original_sl", sl)) else "stop_loss"
            elif l <= tp: exit_reason = "take_profit"

        if not exit_reason and age_min >= self.time_exit_minutes:
            exit_reason = "time_exit"

        if exit_reason:
            await self._close_position(symbol, open_pos, t, close, exit_reason)

    def has_open_position(self, symbol: str) -> bool:
        return symbol in self._open_positions

    async def on_signal(self, symbol: str, tf: int, t: str, close: float, side: str, reason: str) -> bool:
        day = _dt_day_key_utc(t)
        equity, _ = self._get_portfolio()
        st = self._day_state.get(day) or {"start_equity": equity, "stopped": False}
        self._day_state[day] = st
        # Hard daily stop based on realized equity drawdown from start of UTC day.
        if self.daily_stop and self.daily_stop > 0:
            day_loss = float(st["start_equity"]) - float(equity)
            if day_loss >= float(self.daily_stop):
                st["stopped"] = True
        if st["stopped"]:
            return False

        open_pos = self._open_positions.get(symbol)
        if open_pos:
            return False  # pozice otevřená → čekej na SL/TP/time exit, žádný flip

        atr = self._atr[symbol].atr if symbol in self._atr else (close * 0.01)
        effective_risk = self.risk_per_trade * self.get_effective_risk_multiplier()
        if effective_risk <= 0:
            return False
        qty = (equity * effective_risk) / max(atr * self.sl_atr_mult, close * 0.002)
        if qty * close < 10.0: qty = 10.0 / close

        entry_px = self._fill_price(side, close)
        fee_rate = get_fee_rate_per_side(settings, symbol)
        fee = entry_px * qty * fee_rate

        sl = round(entry_px - (atr * self.sl_atr_mult if side == "BUY" else -atr * self.sl_atr_mult), 2)
        tp = round(entry_px + (atr * self.tp_atr_mult if side == "BUY" else -atr * self.tp_atr_mult), 2)

        pos_doc = {
            "run_id": self.run_id, "symbol": symbol, "status": "OPEN", "side": side,
            "entry_time": t, "entry_price": entry_px, "qty": qty, "fee_entry": fee,
            "fee_rate_per_side": fee_rate,
            "sl": sl, "tp": tp, "original_sl": sl, "reason": reason,
            "risk_per_trade_effective": effective_risk,
            "risk_multiplier_effective": self.get_effective_risk_multiplier(),
        }
        res = self.db.positions.insert_one(pos_doc)
        pos_doc["_id"] = res.inserted_id
        self._open_positions[symbol] = pos_doc
        return True

    async def _close_position(self, symbol: str, open_pos: dict, exit_time: str, exit_mid: float, reason: str):
        exit_px = self._fill_price("SELL" if open_pos["side"] == "BUY" else "BUY", exit_mid)
        qty = float(open_pos["qty"])
        gross = (exit_px - open_pos["entry_price"]) * qty * (1 if open_pos["side"] == "BUY" else -1)
        fee_rate = _safe_float(open_pos.get("fee_rate_per_side"), get_fee_rate_per_side(settings, symbol))
        fee_exit = exit_px * qty * fee_rate
        net_pnl = round(gross - open_pos["fee_entry"] - fee_exit, 2)

        self.db.positions.update_one({"_id": open_pos["_id"]}, {"$set": {
            "status": "CLOSED", "exit_time": exit_time, "exit_price": exit_px, "pnl": net_pnl, "reason_exit": reason
        }})
        self._open_positions.pop(symbol, None)
        
        self.db.trades.insert_one({
            "run_id": self.run_id, "symbol": symbol, "pnl": net_pnl, "reason_exit": reason, "t": exit_time
        })
        self._pf_recent.append(net_pnl)

        equity, buffer = self._get_portfolio()
        if net_pnl > 0:
            reinvest = net_pnl * self.split_reinvest
            equity += reinvest
            buffer += (net_pnl - reinvest)
        else: equity += net_pnl
        self._set_portfolio(equity, buffer)
