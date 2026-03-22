# trading/engine.py
import uuid
from datetime import datetime, timezone
from collections import deque, defaultdict
import pandas as pd
from trading.mongo import get_db, get_recent_sentiment, get_latest_intel, get_latest_funding_oi
from trading.config import settings
from trading.paper import PaperExecutor
from trading.fees import estimate_roundtrip_cost_frac, infer_asset_class
from signal_quality import score_signal_quality


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


class TradingEngine:
    def __init__(
        self,
        run_id: str = None,
        seed_before: str = None,
        interval: int = None,
        persist_candles: bool = True,
        persist_runtime_state: bool = True,
        persist_signals: bool = True,
        backtest_historical_time: bool = False,
    ):
        # Pokud run_id nepřijde zvenčí, vygenerujeme nové unikátní pro tento start
        self.run_id = run_id or str(uuid.uuid4())[:8]
        self.db = get_db()
        self.exec = PaperExecutor(self.db, self.run_id)
        self.seed_before = seed_before  # ISO timestamp — omezí seed jen na svíčky před tímto datem
        self.interval = _safe_int(interval if interval is not None else settings.INTERVAL_MINUTES, 60)
        self.persist_candles = bool(persist_candles)
        self.persist_runtime_state = bool(persist_runtime_state)
        self.persist_signals = bool(persist_signals)
        self.backtest_historical_time = bool(backtest_historical_time)

        # Parametry z configu
        self.N = max(2, _safe_int(settings.BREAKOUT_N, 7))
        self.ema_period = max(2, _safe_int(settings.EMA_PERIOD, 50))
        self.cooldown = max(0, _safe_int(settings.COOLDOWN_CANDLES, 1))

        self._buf = defaultdict(lambda: deque(maxlen=max(100, _safe_int(settings.ENGINE_BUFFER_MAXLEN, 1000))))
        self._current = {}
        self._cooldown_remaining = defaultdict(int)  # symbol → zbývající svíčky cooldownu
        self._rec_cache = None       # cache pro asset recommendations
        self._rec_cache_time = None  # čas posledního DB dotazu
        self._llm_health_cache = None
        self._llm_health_cache_time = None

        print("\n" + "="*50)
        print(f">>> TRADING BOT STARTUJE <<<")
        print(f">>> AKTIVNÍ RUN ID: {self.run_id} <<<")
        print(f">>> STRATEGIE: N={self.N}, EMA={self.ema_period} <<<")
        if settings.SENTIMENT_ENABLED:
            print(f">>> SENTIMENT FILTR: ON (window={settings.SENTIMENT_WINDOW_MINUTES}m) <<<")
        print("="*50 + "\n")

        self._seed_buffer()

    def _mark_last_processed(self, symbol: str, tf: int, t: str):
        if not self.persist_runtime_state:
            return
        try:
            self.db.bot_runtime_state.update_one(
                {"run_id": self.run_id, "symbol": symbol, "tf": tf},
                {"$set": {"last_processed_t": t, "updated_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
        except Exception:
            pass

    def _get_last_processed(self, symbol: str, tf: int):
        doc = self.db.bot_runtime_state.find_one({"run_id": self.run_id, "symbol": symbol, "tf": tf})
        return doc.get("last_processed_t") if doc else None

    async def replay_missed_from_mongo(self):
        """Replay missed candles after API/bot restart to simulate outage continuity."""
        symbols = set(self._get_seed_symbols())
        for p in self.db.positions.find({"run_id": self.run_id, "status": "OPEN"}, {"symbol": 1}):
            sym = p.get("symbol")
            if isinstance(sym, str) and sym.strip():
                symbols.add(sym.strip())

        total_replayed = 0
        for sym in sorted(symbols):
            last_t = self._get_last_processed(sym, self.interval)
            if not last_t:
                continue  # no reliable checkpoint yet for this symbol

            q = {"symbol": sym, "tf": self.interval, "t": {"$gt": last_t}}
            cursor = self.db.market_candles.find(q, {"_id": 0, "t": 1, "o": 1, "h": 1, "l": 1, "c": 1, "v": 1}).sort("t", 1)
            rows = list(cursor)
            if len(rows) < 2:
                continue

            # Reuse live on_candle path. Last row stays "currently forming/live".
            for r in rows:
                await self.on_candle(
                    sym,
                    self.interval,
                    {
                        "symbol": sym,
                        "timestamp": r["t"],
                        "open": r["o"],
                        "high": r["h"],
                        "low": r["l"],
                        "close": r["c"],
                        "volume": r["v"],
                        "_no_persist": True,
                    },
                )
            # on_candle processes "previous closed", so replay count is rows-1
            replayed = max(0, len(rows) - 1)
            total_replayed += replayed
            print(f"[{self.run_id}] Replay {sym}: {replayed} candles from {last_t}")

        if total_replayed > 0:
            print(f"[{self.run_id}] Replay done: {total_replayed} candles processed")

    @staticmethod
    def _as_utc_aware(dt: datetime) -> datetime:
        if dt.tzinfo is None:
            return dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)

    @staticmethod
    def _parse_iso_utc(ts: str) -> datetime:
        if not ts:
            return datetime.now(timezone.utc)
        try:
            return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)
        except Exception:
            return datetime.now(timezone.utc)

    @staticmethod
    def _normalize_signal_t(ts: str) -> str:
        """Normalize signal timestamp to second precision UTC ISO for dedup/log stability."""
        dt = TradingEngine._parse_iso_utc(str(ts or ""))
        return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _seed_buffer(self):
        symbols = self._get_seed_symbols()
        for sym in symbols:
            query = {"symbol": sym, "tf": self.interval}
            if self.seed_before:
                query["t"] = {"$lt": self.seed_before}
            seed_candles = max(50, _safe_int(settings.ENGINE_SEED_CANDLES, 800))
            rows = list(self.db.market_candles.find(query).sort("t", -1).limit(seed_candles))
            for r in reversed(rows):
                self._buf[(sym, self.interval)].append({"t": r["t"], "o": r["o"], "h": r["h"], "l": r["l"], "c": r["c"], "v": r["v"]})
            print(f"DEBUG: {sym} - Načteno {len(rows)} svíček pro indikátory.")

    def _parse_symbols(self, src: str):
        return [s.strip() for s in src.split(",") if s.strip()]

    def _get_seed_symbols(self):
        symbols = set(self._parse_symbols(settings.SYMBOLS))
        if settings.TRADING_BINANCE_ENABLED:
            symbols.update(self._parse_symbols(settings.BINANCE_SYMBOLS))
            symbols.update(self._parse_symbols(settings.ALWAYS_ACTIVE_SYMBOLS))
        if bool(getattr(settings, "TRADING_IBKR_ENABLED", False)):
            symbols.update(self._parse_symbols(getattr(settings, "IBKR_SYMBOLS", "")))
        if settings.EXPAND_UNIVERSE_FROM_RECOMMENDATIONS and settings.DYNAMIC_ASSETS_ENABLED:
            rec = self._get_recommendation()
            if rec:
                symbols.update(rec.get("symbols", []))
        return sorted(symbols)

    def _estimate_atr(self, candles, period: int = 14):
        if len(candles) < max(3, period + 1):
            return None
        highs = pd.Series([c["h"] for c in candles], dtype="float64")
        lows = pd.Series([c["l"] for c in candles], dtype="float64")
        closes = pd.Series([c["c"] for c in candles], dtype="float64")
        prev_close = closes.shift(1)
        tr = pd.concat(
            [
                (highs - lows).abs(),
                (highs - prev_close).abs(),
                (lows - prev_close).abs(),
            ],
            axis=1,
        ).max(axis=1)
        atr = tr.rolling(window=max(2, int(period))).mean().iloc[-1]
        if pd.isna(atr):
            return None
        try:
            return float(atr)
        except Exception:
            return None

    def _estimate_roundtrip_cost_frac(self, symbol: str) -> float:
        return estimate_roundtrip_cost_frac(settings, symbol)

    def _log_signal(self, t, symbol, side, price, action, detail=""):
        if not self.persist_signals:
            return
        try:
            t_norm = self._normalize_signal_t(str(t or ""))
            q = {
                "run_id": self.run_id,
                "t": t_norm,
                "symbol": str(symbol or "").strip(),
                "side": str(side or "").strip().upper(),
                "action": str(action or "").strip().lower(),
                "detail": str(detail or ""),
            }
            self.db.bot_signals.update_one(
                q,
                {
                    "$setOnInsert": {
                        **q,
                        "price": float(price),
                        "reason": f"breakout_{self.N}",
                        "created_at": datetime.now(timezone.utc),
                    }
                },
                upsert=True,
            )
        except Exception:
            pass

    def _calculate_ema(self, candles, period):
        if len(candles) < period:
            return None
        closes = [c["c"] for c in candles]
        return pd.Series(closes).ewm(span=period, adjust=False).mean().iloc[-1]

    def _get_recommendation(self, as_of: datetime | None = None):
        """Vrátí aktuální asset recommendation (cache 60s)."""
        if not settings.DYNAMIC_ASSETS_ENABLED:
            return None
        now = self._as_utc_aware(as_of) if as_of is not None else datetime.now(timezone.utc)
        if (not self.backtest_historical_time) and self._rec_cache_time and (now - self._rec_cache_time).total_seconds() < 60:
            return self._rec_cache
        q = {"created_at": {"$lte": now}} if self.backtest_historical_time else None
        doc = self.db.asset_recommendations.find_one(q, sort=[("created_at", -1)]) if q else self.db.asset_recommendations.find_one(sort=[("created_at", -1)])
        if doc:
            created_at = self._as_utc_aware(doc["created_at"])
            age = (now - created_at).total_seconds() / 60
            if age <= _safe_float(settings.RECOMMENDATION_MAX_AGE_MINUTES, 180.0):
                self._rec_cache = doc
            else:
                self._rec_cache = None
        else:
            self._rec_cache = None
        self._rec_cache_time = now
        return self._rec_cache

    def _get_llm_health(self, as_of: datetime | None = None):
        """Detect whether latest LLM intel is degraded (LLM_FAILED)."""
        now = self._as_utc_aware(as_of) if as_of is not None else datetime.now(timezone.utc)
        if (not self.backtest_historical_time) and self._llm_health_cache_time and (now - self._llm_health_cache_time).total_seconds() < 60:
            return self._llm_health_cache

        q = {"created_at": {"$lte": now}} if self.backtest_historical_time else None
        doc = self.db.market_intel.find_one(q, sort=[("created_at", -1)]) if q else self.db.market_intel.find_one(sort=[("created_at", -1)])
        degraded = False
        if doc:
            created_at = self._as_utc_aware(doc["created_at"])
            age = (now - created_at).total_seconds() / 60
            raw = str(doc.get("raw", "") or "")
            degraded = raw.startswith("LLM_FAILED:") and age <= _safe_float(settings.LLM_DEGRADED_MAX_AGE_MINUTES, 180.0)

        self._llm_health_cache = {"degraded": degraded}
        self._llm_health_cache_time = now
        return self._llm_health_cache

    async def on_candle(self, symbol: str, tf: int, item: dict):
        sym = item.get("symbol") or symbol
        t = item.get("interval_begin") or item.get("timestamp")
        
        cndl = {"t": t, "o": float(item["open"]), "h": float(item["high"]), "l": float(item["low"]), "c": float(item["close"]), "v": float(item["volume"])}
        persist_this = self.persist_candles and not bool(item.get("_no_persist"))
        if persist_this:
            self.db.market_candles.update_one({"symbol": sym, "tf": tf, "t": t}, {"$set": cndl}, upsert=True)

        if tf != self.interval: return

        key = (sym, tf)
        prev_live = self._current.get(key)
        self._current[key] = cndl
        if prev_live is None or prev_live["t"] == t: return 

        closed = prev_live
        buf = self._buf[key]
        buf.append(closed)
        as_of = self._parse_iso_utc(closed["t"]) if self.backtest_historical_time else None

        # Cooldown dekrementace
        if self._cooldown_remaining[sym] > 0:
            self._cooldown_remaining[sym] -= 1

        had_position = self.exec.has_open_position(sym)
        await self.exec.on_candle_closed(sym, tf, closed["t"], closed["c"], closed)
        # Pokud se pozice právě zavřela, aktivuj cooldown
        if had_position and not self.exec.has_open_position(sym):
            self._cooldown_remaining[sym] = self.cooldown

        ema_val = self._calculate_ema(buf, self.ema_period)

        if ema_val is None: return

        # Cooldown — po zavření pozice nepouštět nové signály
        if self._cooldown_remaining[sym] > 0:
            return

        history = list(buf)[-self.N - 1 : -1]
        level_high = max(x["h"] for x in history)
        level_low = min(x["l"] for x in history)
        close_px = closed["c"]

        # Volume filtr — breakout bez objemu je falešný
        if settings.VOL_FILTER:
            avg_vol = sum(x["v"] for x in history) / len(history) if history else 0
            vol_mult = _safe_float(settings.VOL_MULT, 1.3)
            if avg_vol > 0 and closed["v"] < avg_vol * vol_mult:
                return  # nedostatečný objem, ignoruj breakout

        side = None
        if close_px > level_high and close_px > ema_val:
            side = "BUY"
        elif close_px < level_low and close_px < ema_val:
            side = "SELL"

        if side:
            if bool(getattr(settings, "FEE_AWARE_GATE_ENABLED", True)):
                atr_period = max(2, _safe_int(settings.ATR_PERIOD, 14))
                atr = self._estimate_atr(list(buf), period=atr_period)
                if atr is not None and close_px > 0:
                    tp_mult = max(0.0, _safe_float(settings.TP_ATR_MULT, 2.0))
                    expected_gross = (atr * tp_mult) / close_px
                    roundtrip_cost = self._estimate_roundtrip_cost_frac(sym)
                    edge_mult = max(0.0, _safe_float(settings.FEE_AWARE_MIN_EDGE_MULT, 1.2))
                    if expected_gross < (roundtrip_cost * edge_mult):
                        self._log_signal(
                            closed["t"],
                            sym,
                            side,
                            close_px,
                            "blocked",
                            f"fee_gate: edge={expected_gross:.5f} cost={roundtrip_cost:.5f} mult={edge_mult:.2f}",
                        )
                        return

            # Rolling PF guard: auto-risk throttle / block when recent PF degrades.
            apply_pf_guard = True
            if infer_asset_class(sym) != "crypto" and not bool(getattr(settings, "PF_GUARD_NON_CRYPTO_ENABLED", False)):
                apply_pf_guard = False
            pf_mult = self.exec.get_pf_guard_multiplier() if apply_pf_guard else 1.0
            self.exec.set_risk_multiplier("pf_guard", pf_mult)
            if pf_mult <= 0:
                self._log_signal(closed["t"], sym, side, close_px, "blocked", "pf_guard")
                return

            llm_non_blocking = bool(getattr(settings, "LLM_NON_BLOCKING_MODE", False))

            # LLM degraded safety mode (fresh LLM failures).
            llm_degraded = self._get_llm_health(as_of=as_of).get("degraded", False)
            if llm_degraded:
                action = (settings.LLM_DEGRADED_ACTION or "throttle").lower().strip()
                if action == "block":
                    if llm_non_blocking:
                        self.exec.set_risk_multiplier("llm_degraded", 1.0)
                        if bool(settings.LLM_POLICY_LOG_DECISIONS):
                            self._log_signal(closed["t"], sym, side, close_px, "policy", "llm_degraded_block_advisory")
                    else:
                        if bool(settings.LLM_POLICY_LOG_DECISIONS):
                            self._log_signal(closed["t"], sym, side, close_px, "policy", "llm_degraded_block")
                        self._log_signal(closed["t"], sym, side, close_px, "blocked", "llm_degraded")
                        return
                if action == "throttle":
                    self.exec.set_risk_multiplier("llm_degraded", 1.0 if llm_non_blocking else settings.LLM_DEGRADED_RISK_MULT)
                    if bool(settings.LLM_POLICY_LOG_DECISIONS):
                        self._log_signal(
                            closed["t"],
                            sym,
                            side,
                            close_px,
                            "policy",
                            (
                                f"llm_degraded_throttle_advisory: {float(settings.LLM_DEGRADED_RISK_MULT):.3f}"
                                if llm_non_blocking
                                else f"llm_degraded_throttle: {float(settings.LLM_DEGRADED_RISK_MULT):.3f}"
                            ),
                        )
                else:
                    self.exec.set_risk_multiplier("llm_degraded", 1.0)
                    if bool(settings.LLM_POLICY_LOG_DECISIONS):
                        self._log_signal(closed["t"], sym, side, close_px, "policy", "llm_degraded_pass")
            else:
                self.exec.set_risk_multiplier("llm_degraded", 1.0)

            # Tabular signal-quality policy overlay (does not generate direction).
            if settings.SIGNAL_QUALITY_ENABLED:
                sq = score_signal_quality(self.db, sym, side, as_of=as_of)
                if sq.get("ok"):
                    prob = _safe_float(sq.get("prob"), 0.0)
                    min_prob = _safe_float(settings.SIGNAL_QUALITY_MIN_PROB, 0.55)
                    thr_prob = _safe_float(settings.SIGNAL_QUALITY_THROTTLE_PROB, 0.62)
                    if prob < min_prob:
                        self._log_signal(closed["t"], sym, side, close_px, "blocked", f"quality_prob: {prob:.3f}")
                        return
                    if prob < thr_prob:
                        self.exec.set_risk_multiplier("signal_quality", settings.SIGNAL_QUALITY_LOW_RISK_MULT)
                        if bool(settings.SIGNAL_QUALITY_LOG_DECISIONS):
                            self._log_signal(closed["t"], sym, side, close_px, "policy", f"quality_prob_throttle: {prob:.3f}")
                    else:
                        self.exec.set_risk_multiplier("signal_quality", 1.0)
                        if bool(settings.SIGNAL_QUALITY_LOG_DECISIONS):
                            self._log_signal(closed["t"], sym, side, close_px, "policy", f"quality_prob_pass: {prob:.3f}")
                else:
                    # If model is unavailable, keep pass-through behavior.
                    self.exec.set_risk_multiplier("signal_quality", 1.0)

            # Dynamic asset recommendation filtr
            rec = self._get_recommendation(as_of=as_of)
            if rec:
                rec_symbols = set(rec.get("symbols", []))
                if sym not in rec_symbols:
                    if bool(settings.LLM_POLICY_LOG_DECISIONS):
                        self._log_signal(
                            closed["t"], sym, side, close_px, "policy",
                            "llm_allowlist_block_advisory" if llm_non_blocking else "llm_allowlist_block"
                        )
                    if not llm_non_blocking:
                        self._log_signal(closed["t"], sym, side, close_px, "blocked", "not in recommendations")
                        return
                if bool(settings.LLM_POLICY_LOG_DECISIONS):
                    self._log_signal(closed["t"], sym, side, close_px, "policy", "llm_allowlist_pass")

                # Direction bias — LLM doporučuje BULLISH/BEARISH
                base = sym.split("/")[0].upper()
                detail = rec.get("details", {}).get(base, {})
                rec_outlook = detail.get("outlook")
                if rec_outlook:
                    if (side == "BUY" and rec_outlook == "BEARISH") or \
                       (side == "SELL" and rec_outlook == "BULLISH"):
                        if bool(settings.LLM_POLICY_LOG_DECISIONS):
                            self._log_signal(
                                closed["t"], sym, side, close_px, "policy",
                                (
                                    f"llm_direction_block_advisory: {rec_outlook}"
                                    if llm_non_blocking else f"llm_direction_block: {rec_outlook}"
                                ),
                            )
                        if not llm_non_blocking:
                            print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN doporučením ({rec_outlook})")
                            self._log_signal(closed["t"], sym, side, close_px, "blocked", f"rec direction: {rec_outlook}")
                            return
                    if bool(settings.LLM_POLICY_LOG_DECISIONS):
                        self._log_signal(closed["t"], sym, side, close_px, "policy", f"llm_direction_pass: {rec_outlook}")
            if settings.SENTIMENT_ENABLED:
                sentiment = get_recent_sentiment(
                    self.db, sym,
                    window_minutes=settings.SENTIMENT_WINDOW_MINUTES,
                    min_articles=settings.SENTIMENT_MIN_ARTICLES,
                    no_data_action=settings.SENTIMENT_NO_DATA_ACTION,
                    as_of=as_of,
                )
                if sentiment is not None:
                    if (side == "BUY" and sentiment != "Positive") or \
                       (side == "SELL" and sentiment != "Negative"):
                        print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN sentimentem ({sentiment})")
                        self._log_signal(closed["t"], sym, side, close_px, "blocked", f"sentiment: {sentiment}")
                        return
                else:
                    print(f"[{self.run_id}] Sentiment: nedostatek dat pro {sym}, pass-through")

            # Market intelligence filtr
            if settings.INTEL_ENABLED:
                intel = get_latest_intel(self.db, sym, as_of=as_of)
                intel_max_age = _safe_float(settings.INTEL_MAX_AGE_MINUTES, 120.0)
                if intel and intel.get("intel_age_minutes", 999) < intel_max_age:
                    outlook = intel.get("outlook", "NEUTRAL")
                    confidence = intel.get("confidence", "LOW")
                    overall = intel.get("overall", "NEUTRAL")

                    if overall == "RISK-OFF" and side == "BUY":
                        if llm_non_blocking:
                            if bool(settings.LLM_POLICY_LOG_DECISIONS):
                                self._log_signal(closed["t"], sym, side, close_px, "policy", "intel_riskoff_advisory")
                        else:
                            print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN intelem (RISK-OFF)")
                            self._log_signal(closed["t"], sym, side, close_px, "blocked", "intel: RISK-OFF")
                            return

                    if (side == "BUY" and outlook == "BEARISH") or \
                       (side == "SELL" and outlook == "BULLISH"):
                        if confidence != "LOW":
                            if llm_non_blocking:
                                if bool(settings.LLM_POLICY_LOG_DECISIONS):
                                    self._log_signal(
                                        closed["t"], sym, side, close_px, "policy",
                                        f"intel_direction_advisory: {outlook} {confidence}"
                                    )
                            else:
                                print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN intelem ({outlook}, {confidence})")
                                self._log_signal(closed["t"], sym, side, close_px, "blocked", f"intel: {outlook} {confidence}")
                                return

                    if settings.INTEL_BLOCK_LOW_CONF and confidence == "LOW":
                        if llm_non_blocking:
                            if bool(settings.LLM_POLICY_LOG_DECISIONS):
                                self._log_signal(closed["t"], sym, side, close_px, "policy", "intel_low_conf_advisory")
                        else:
                            print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN intelem (LOW confidence)")
                            self._log_signal(closed["t"], sym, side, close_px, "blocked", "intel: LOW confidence")
                            return
                else:
                    print(f"[{self.run_id}] Intel: žádná data pro {sym}, pass-through")

            # Funding Rate + Open Interest filtr
            if settings.FUNDING_ENABLED or settings.OI_ENABLED:
                foi = get_latest_funding_oi(self.db, sym, as_of=as_of)
                if foi:
                    # Funding Rate: extrémní FR → blokuj contrarian signály
                    funding_max_age = _safe_float(settings.FUNDING_MAX_AGE_MINUTES, 60.0)
                    funding_block_thr = _safe_float(settings.FUNDING_BLOCK_THRESHOLD, 0.01)
                    if settings.FUNDING_ENABLED and foi["age_minutes"] < funding_max_age:
                        fr = foi.get("funding_rate")
                        if fr is not None and abs(fr) > funding_block_thr:
                            if (fr > 0 and side == "BUY") or (fr < 0 and side == "SELL"):
                                print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN funding rate ({fr:.4f})")
                                self._log_signal(closed["t"], sym, side, close_px, "blocked", f"funding_rate: {fr:.4f}")
                                return

                    # Open Interest: klesající OI → false breakout risk
                    oi_max_age = _safe_float(settings.OI_MAX_AGE_MINUTES, 60.0)
                    oi_change_thr = _safe_float(settings.OI_CHANGE_THRESHOLD, 0.10)
                    if settings.OI_ENABLED and foi["age_minutes"] < oi_max_age and foi.get("open_interest"):
                        prev_docs = list(self.db.funding_oi.find(
                            {"symbol": sym, "timestamp": {"$lt": foi["timestamp"]}},
                        ).sort("timestamp", -1).skip(5).limit(1))
                        prev_oi = _safe_float(prev_docs[0].get("open_interest"), 0.0) if prev_docs else 0.0
                        curr_oi = _safe_float(foi.get("open_interest"), 0.0)
                        if prev_oi > 0:
                            oi_change = (curr_oi - prev_oi) / prev_oi
                            if oi_change < -oi_change_thr:
                                print(f"[{self.run_id}] SIGNAL {side} na {sym} BLOKOVÁN klesajícím OI ({oi_change:.1%})")
                                self._log_signal(closed["t"], sym, side, close_px, "blocked", f"oi_drop: {oi_change:.1%}")
                                return
                else:
                    print(f"[{self.run_id}] Funding/OI: žádná data pro {sym}, pass-through")

            mode = str(getattr(settings, "MODE", "paper") or "paper").strip().lower()
            shadow_live = (mode == "live") and bool(getattr(settings, "SHADOW_MODE_ENABLED", False))
            if shadow_live:
                eff_risk = self.exec.get_effective_risk_multiplier()
                detail = f"shadow_would_execute: breakout_{self.N}; risk_mult={eff_risk:.3f}"
                print(f"[{self.run_id}] SHADOW {side} na {sym} @ {close_px} | {detail}")
                self._log_signal(closed["t"], sym, side, close_px, "shadow", detail)
            else:
                opened = await self.exec.on_signal(sym, tf, closed["t"], close_px, side, f"breakout_{self.N}")
                if opened:
                    print(f"!!! [{self.run_id}] SIGNAL {side} na {sym} @ {close_px}")
                    self._log_signal(closed["t"], sym, side, close_px, "executed", f"breakout_{self.N}")
                else:
                    self._log_signal(closed["t"], sym, side, close_px, "blocked", "executor_rejected")
        self._mark_last_processed(sym, tf, closed["t"])
