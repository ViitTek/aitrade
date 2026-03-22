from __future__ import annotations
import re


def _safe_float(value, default: float) -> float:
    try:
        if value is None:
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def infer_asset_class(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    if "/" in s:
        return "crypto"
    if "=" in s:
        return "fx"
    if re.fullmatch(r"[A-Z]{6}", s or ""):
        return "fx"
    if s.startswith(("CL", "NG", "GC", "SI", "HG", "ES", "NQ", "YM", "RTY", "ZN", "ZF", "ZT")):
        return "futures"
    return "stocks"


def infer_venue(settings, symbol: str) -> str:
    sym = str(symbol or "").strip()
    binance_symbols = {x.strip() for x in str(getattr(settings, "BINANCE_SYMBOLS", "") or "").split(",") if x.strip()}
    ibkr_symbols = {x.strip() for x in str(getattr(settings, "IBKR_SYMBOLS", "") or "").split(",") if x.strip()}
    if sym and sym in binance_symbols:
        return "binance"
    if sym and sym in ibkr_symbols:
        return "ibkr"
    if infer_asset_class(sym) == "crypto":
        return "kraken"
    # Non-crypto symbols default to IBKR in this project.
    return "ibkr"


def get_fee_rate_per_side(settings, symbol: str, venue: str | None = None, asset_class: str | None = None) -> float:
    v = str(venue or infer_venue(settings, symbol)).strip().lower()
    a = str(asset_class or infer_asset_class(symbol)).strip().lower()

    if v == "binance":
        return max(0.0, _safe_float(getattr(settings, "FEE_RATE_BINANCE", None), _safe_float(getattr(settings, "FEE_RATE", 0.001), 0.001)))
    if v == "kraken":
        return max(0.0, _safe_float(getattr(settings, "FEE_RATE_KRAKEN", None), _safe_float(getattr(settings, "FEE_RATE", 0.001), 0.001)))
    if v == "ibkr":
        if a == "fx":
            return max(0.0, _safe_float(getattr(settings, "FEE_RATE_IBKR_FX", 0.00002), 0.00002))
        if a == "futures":
            return max(0.0, _safe_float(getattr(settings, "FEE_RATE_IBKR_FUTURES", 0.00008), 0.00008))
        return max(0.0, _safe_float(getattr(settings, "FEE_RATE_IBKR_STOCKS", 0.00005), 0.00005))
    return max(0.0, _safe_float(getattr(settings, "FEE_RATE", 0.001), 0.001))


def estimate_roundtrip_cost_frac(settings, symbol: str, venue: str | None = None, asset_class: str | None = None) -> float:
    fee = get_fee_rate_per_side(settings, symbol, venue=venue, asset_class=asset_class)
    spread_bps = _safe_float(getattr(settings, "SPREAD_BPS", 2.0), 2.0)
    slippage_bps = _safe_float(getattr(settings, "SLIPPAGE_BPS_BASE", 1.5), 1.5)
    return max(0.0, (2.0 * fee) + ((spread_bps + (2.0 * slippage_bps)) / 10000.0))
