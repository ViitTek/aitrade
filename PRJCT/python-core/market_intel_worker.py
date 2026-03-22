"""
Market Intelligence Worker — stahuje tržní data, ptá se LLM na analýzu,
ukládá výsledky do MongoDB kolekce "market_intel".

Běží každou hodinu, nebo jednorázově s --once.

Použití:
    python market_intel_worker.py
    python market_intel_worker.py --once
"""
import argparse
import logging
import re
import time
import requests
from datetime import datetime, timezone
from pymongo import MongoClient

from llama_wrapper import run_llama_structured
from trading.config import settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [INTEL] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

COINGECKO_MARKETS = "https://api.coingecko.com/api/v3/coins/markets"
COINGECKO_TRENDING = "https://api.coingecko.com/api/v3/search/trending"
FNG_URL = "https://api.alternative.me/fng/"
BINANCE_EXCHANGE_INFO = "https://api.binance.com/api/v3/exchangeInfo"

INTERVAL_SECONDS = 3600  # 1 hodina

# Cache pro Binance symboly (1h TTL)
_binance_cache = {"symbols": set(), "fetched_at": None}

def _get_with_backoff(url: str, *, params: dict | None = None, timeout: int = 10, retries: int = 3):
    """HTTP GET with simple exponential backoff for transient errors (429/5xx)."""
    wait = 1.0
    last_err = None
    for i in range(retries):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            if r.status_code in (429, 500, 502, 503, 504):
                if i < retries - 1:
                    ra = r.headers.get("Retry-After")
                    try:
                        wait = max(wait, float(ra))
                    except Exception:
                        pass
                    time.sleep(wait)
                    wait = min(wait * 2.0, 20.0)
                    continue
            r.raise_for_status()
            return r
        except Exception as e:
            last_err = e
            if i < retries - 1:
                time.sleep(wait)
                wait = min(wait * 2.0, 20.0)
    raise last_err if last_err else RuntimeError("request failed")


def fetch_market_data() -> dict:
    """Stáhne tržní přehled z CoinGecko + Fear & Greed Index."""
    result = {"coins": [], "fng": None, "trending": []}

    # Top coiny podle market cap
    try:
        r = _get_with_backoff(COINGECKO_MARKETS, params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 10,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "1h,24h,7d",
        }, timeout=10, retries=4)
        for coin in r.json():
            result["coins"].append({
                "symbol": coin["symbol"].upper(),
                "name": coin["name"],
                "price": coin["current_price"],
                "change_24h": coin.get("price_change_percentage_24h"),
                "change_7d": coin.get("price_change_percentage_7d_in_currency"),
                "volume_24h": coin.get("total_volume"),
                "market_cap": coin.get("market_cap"),
            })
    except Exception as e:
        log.warning(f"CoinGecko markets selhal: {e}")

    # Fear & Greed Index
    try:
        r = requests.get(FNG_URL, params={"limit": 1}, timeout=5)
        r.raise_for_status()
        data = r.json().get("data", [{}])[0]
        result["fng"] = {
            "value": int(data.get("value", 50)),
            "classification": data.get("value_classification", "Neutral"),
        }
    except Exception as e:
        log.warning(f"Fear & Greed fetch selhal: {e}")

    # Trending coiny
    try:
        r = _get_with_backoff(COINGECKO_TRENDING, timeout=10, retries=4)
        for item in r.json().get("coins", [])[:5]:
            coin = item.get("item", {})
            result["trending"].append(coin.get("symbol", "?"))
    except Exception as e:
        log.warning(f"CoinGecko trending selhal: {e}")

    return result


def fetch_binance_symbols() -> set:
    """Stáhne dostupné USDT trading páry z Binance. Cache 1h."""
    now = datetime.now(timezone.utc)
    if (_binance_cache["fetched_at"] and
            (now - _binance_cache["fetched_at"]).total_seconds() < 3600):
        return _binance_cache["symbols"]

    try:
        r = requests.get(BINANCE_EXCHANGE_INFO, timeout=15)
        r.raise_for_status()
        pairs = set()
        for s in r.json()["symbols"]:
            if s["status"] == "TRADING" and s["quoteAsset"] == "USDT":
                pairs.add(f"{s['baseAsset']}/USDT")
        _binance_cache["symbols"] = pairs
        _binance_cache["fetched_at"] = now
        log.info(f"Binance: {len(pairs)} USDT párů dostupných")
        return pairs
    except Exception as e:
        log.warning(f"Binance exchangeInfo selhal: {e}")
        return _binance_cache["symbols"] or set()


def filter_eligible_symbols(data: dict, binance_pairs: set) -> list:
    """Filtruje CoinGecko coiny dle market cap, volume a dostupnosti na Binance."""
    eligible = []
    always = set(s.strip().split("/")[0].upper()
                 for s in settings.ALWAYS_ACTIVE_SYMBOLS.split(",") if s.strip())

    for coin in data["coins"]:
        sym = coin["symbol"].upper()
        pair = f"{sym}/USDT"

        # Přeskoč always-active (ty jsou vždy zahrnuté)
        if sym in always:
            continue

        if pair not in binance_pairs:
            continue

        mc = coin.get("market_cap") or 0
        vol = coin.get("volume_24h") or 0
        if mc < settings.MIN_MARKET_CAP_USD or vol < settings.MIN_VOLUME_24H_USD:
            continue

        eligible.append(pair)

    return eligible


def _metric_based_selection(data: dict, eligible: list) -> list:
    """Fallback výběr aktiv bez LLM — řadí dle volume a absolutní price change."""
    coin_map = {c["symbol"].upper(): c for c in data.get("coins", [])}
    scored = []
    for pair in eligible:
        sym = pair.split("/")[0].upper()
        coin = coin_map.get(sym)
        if not coin:
            continue
        vol = coin.get("volume_24h") or 0
        chg = coin.get("change_24h") or 0
        # Skóre: kombinace objemu (normalizovaného) a absolutní změny
        score = (vol / 1e9) + abs(chg) * 0.5
        outlook = "BULLISH" if chg > 0 else "BEARISH"
        reason = f"24h vol ${vol/1e6:.0f}M, change {chg:+.1f}%"
        scored.append({"symbol": sym, "pair": pair, "outlook": outlook, "reason": reason, "score": score})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:settings.MAX_DYNAMIC_SYMBOLS]


def build_selection_prompt(data: dict, eligible: list) -> str:
    """Prompt pro LLM výběr aktiv k obchodování."""
    fng = data.get("fng") or {}
    fng_val = int(fng.get("value", 50) or 50)
    risk_mode = "DEFENSIVE" if (fng_val <= 45 or (fng.get("classification", "") or "").lower() in ("fear", "extreme fear")) else "NORMAL"
    always = [s.strip() for s in settings.ALWAYS_ACTIVE_SYMBOLS.split(",") if s.strip()]

    lines = [
        "You are a crypto trading analyst. Based on the market data below,",
        f"select the TOP {settings.MAX_DYNAMIC_SYMBOLS} most promising assets to trade.",
        f"Operating mode: {risk_mode}.",
        "Hard constraints:",
        f"- Always-active symbols: {', '.join(always) if always else 'none'}",
        f"- Max dynamic symbols: {settings.MAX_DYNAMIC_SYMBOLS}",
        "- Prefer highly liquid USDT pairs and avoid weak/uncertain setups.",
        "- In DEFENSIVE mode, prefer fewer symbols and avoid forcing picks.",
        "",
        "TOP COINS (24h):",
    ]

    for c in data["coins"]:
        chg = f"{c['change_24h']:+.1f}%" if c["change_24h"] is not None else "N/A"
        vol = f"${c['volume_24h'] / 1e6:.0f}M" if c.get("volume_24h") else "N/A"
        lines.append(f"  {c['symbol']}: ${c['price']:,.2f} ({chg}) vol={vol}")

    if data["fng"]:
        lines.append(f"\nFear & Greed: {data['fng']['value']}/100 ({data['fng']['classification']})")
    if data["trending"]:
        lines.append(f"Trending: {', '.join(data['trending'])}")

    lines.extend([
        "",
        f"Available USDT pairs: {', '.join(eligible[:25])}",
        "",
        "Respond with EXACTLY this format, one per line:",
        "1. SYMBOL: BULLISH/BEARISH | Reason: <one short sentence>",
        "2. SYMBOL: BULLISH/BEARISH | Reason: <one short sentence>",
        "...",
        "Only use symbols from the available pairs list above.",
        "If no strong candidates exist, return fewer lines.",
        "",
        "Answer:",
    ])

    return "\n".join(lines)


def parse_selection(raw: str, available: set) -> list:
    """Parsuje LLM výběr aktiv. Vrací list dictů {symbol, pair, outlook, reason}."""
    results = []
    for match in re.finditer(
        r"(\w+)\s*:\s*(BULLISH|BEARISH)\s*\|\s*Reason:\s*(.+)",
        raw, re.IGNORECASE,
    ):
        sym = match.group(1).upper()
        pair = f"{sym}/USDT"
        if pair in available:
            results.append({
                "symbol": sym,
                "pair": pair,
                "outlook": match.group(2).upper(),
                "reason": match.group(3).strip(),
            })
    return results


def build_prompt(data: dict) -> str:
    """Sestaví stručný prompt pro 7B model."""
    lines = [
        "You are a crypto market analyst. Analyze the data below and provide a brief trading outlook.",
        "",
        "TOP COINS (24h):",
    ]

    for c in data["coins"][:8]:
        chg = f"{c['change_24h']:+.1f}%" if c["change_24h"] is not None else "N/A"
        lines.append(f"  {c['symbol']}: ${c['price']:,.2f} ({chg})")

    if data["fng"]:
        lines.append(f"\nFear & Greed Index: {data['fng']['value']}/100 ({data['fng']['classification']})")

    if data["trending"]:
        lines.append(f"Trending: {', '.join(data['trending'])}")

    lines.extend([
        "",
        "For each of BTC and ETH, respond in EXACTLY this format:",
        "BTC: BULLISH/BEARISH/NEUTRAL | Confidence: HIGH/MEDIUM/LOW | Reason: <one sentence>",
        "ETH: BULLISH/BEARISH/NEUTRAL | Confidence: HIGH/MEDIUM/LOW | Reason: <one sentence>",
        "OVERALL: RISK-ON/RISK-OFF/NEUTRAL",
        "",
        "Answer:",
    ])

    return "\n".join(lines)


def parse_intel(raw: str) -> dict:
    """Parsuje LLM výstup do strukturovaného dictu."""
    result = {"assets": {}, "overall": "NEUTRAL", "raw": raw}

    # Per-asset řádky: "BTC: BULLISH | Confidence: HIGH | Reason: ..."
    for match in re.finditer(
        r"([A-Z0-9]{2,12})\s*:\s*(BULLISH|BEARISH|NEUTRAL)\s*\|\s*Confidence:\s*(HIGH|MEDIUM|LOW)\s*\|\s*Reason:\s*(.+)",
        raw, re.IGNORECASE,
    ):
        sym = match.group(1).upper()
        result["assets"][sym] = {
            "outlook": match.group(2).upper(),
            "confidence": match.group(3).upper(),
            "reason": match.group(4).strip(),
        }

    # Overall
    overall_match = re.search(r"OVERALL\s*:\s*(RISK-ON|RISK-OFF|NEUTRAL)", raw, re.IGNORECASE)
    if overall_match:
        result["overall"] = overall_match.group(1).upper()

    return result


def _active_symbol_bases() -> list:
    bases = set()
    for src in (settings.SYMBOLS, settings.BINANCE_SYMBOLS, settings.ALWAYS_ACTIVE_SYMBOLS):
        for s in str(src).split(","):
            pair = s.strip().upper()
            if not pair:
                continue
            base = pair.split("/")[0].strip()
            if base:
                bases.add(base)
    return sorted(bases)


def _fallback_assets_from_market_data(data: dict, include_bases: list) -> tuple[dict, str]:
    coin_map = {str(c.get("symbol", "")).upper(): c for c in data.get("coins", [])}
    assets = {}
    changes = []
    for base in include_bases:
        coin = coin_map.get(base)
        chg = coin.get("change_24h") if coin else None
        if isinstance(chg, (int, float)):
            changes.append(float(chg))
        if chg is None:
            outlook = "NEUTRAL"
            confidence = "LOW"
            reason = "No fresh market snapshot for this asset."
        else:
            if chg > 0.5:
                outlook = "BULLISH"
                confidence = "MEDIUM"
            elif chg < -0.5:
                outlook = "BEARISH"
                confidence = "MEDIUM"
            else:
                outlook = "NEUTRAL"
                confidence = "LOW"
            vol = coin.get("volume_24h") or 0
            reason = f"Fallback from 24h change {chg:+.2f}% and volume ${vol/1e6:.0f}M."
        assets[base] = {
            "outlook": outlook,
            "confidence": confidence,
            "reason": reason,
        }

    avg_chg = (sum(changes) / len(changes)) if changes else 0.0
    if avg_chg > 0.6:
        overall = "RISK-ON"
    elif avg_chg < -0.6:
        overall = "RISK-OFF"
    else:
        overall = "NEUTRAL"
    return assets, overall


def run_once():
    """Jeden cyklus: stáhni data, zavolej LLM, ulož výsledek."""
    db = MongoClient(settings.MONGO_URI)[settings.MONGO_DB]

    log.info("Stahuji tržní data...")
    data = fetch_market_data()

    if not data["coins"]:
        log.warning("Žádná tržní data, přeskakuji tento cyklus")
        return

    if settings.INTEL_ENABLED:
        prompt = build_prompt(data)
        log.info(f"Prompt délka: {len(prompt)} znaků, spouštím LLM...")
        try:
            raw_output = run_llama_structured(prompt, max_tokens=110, timeout_sec=240)
            log.info(f"LLM výstup: {raw_output[:200]}")
            intel = parse_intel(raw_output)
        except Exception as e:
            log.warning(f"LLM selhal: {e} — ukládám data bez LLM analýzy")
            intel = {"assets": {}, "overall": "NEUTRAL", "raw": f"LLM_FAILED: {e}"}
    else:
        log.info("INTEL_ENABLED=false — přeskakuji LLM, použiji fallback intel.")
        intel = {"assets": {}, "overall": "NEUTRAL", "raw": "LLM_DISABLED_BY_CONFIG"}

    # Ensure per-asset intel exists for active runtime universe.
    active_bases = _active_symbol_bases()
    fallback_assets, fallback_overall = _fallback_assets_from_market_data(data, active_bases)
    if not intel.get("assets"):
        intel["assets"] = fallback_assets
        if str(intel.get("overall", "NEUTRAL")).upper() == "NEUTRAL":
            intel["overall"] = fallback_overall
        intel["raw"] = f"{intel.get('raw', '')}\nFALLBACK_ASSETS_APPLIED"
    else:
        # Fill only missing assets to avoid pass-through on non-BTC/ETH symbols.
        for base, payload in fallback_assets.items():
            intel["assets"].setdefault(base, payload)

    intel["created_at"] = datetime.now(timezone.utc)
    intel["market_data"] = data
    intel["source"] = "market_intel_worker"

    db.market_intel.insert_one(intel)
    log.info(f"Uloženo: overall={intel['overall']}, assets={list(intel['assets'].keys())}")

    # --- Dynamic Asset Selection ---
    if settings.DYNAMIC_ASSETS_ENABLED:
        log.info("Dynamic assets zapnuto, spouštím výběr aktiv...")
        try:
            binance_pairs = fetch_binance_symbols()
            eligible = filter_eligible_symbols(data, binance_pairs)
            log.info(f"Eligible symboly: {eligible[:10]}")

            selections = []
            selection_source = "none"

            if eligible:
                # LLM výběr jen pokud je intel LLM zapnutý.
                if settings.INTEL_ENABLED:
                    try:
                        sel_prompt = build_selection_prompt(data, eligible)
                        raw_sel = run_llama_structured(sel_prompt, max_tokens=110, timeout_sec=240)
                        log.info(f"LLM selection výstup: {raw_sel[:200]}")
                        selections = parse_selection(raw_sel, set(eligible))
                        selection_source = "llm"
                    except Exception as llm_err:
                        log.warning(f"LLM selection selhal: {llm_err} — použiji metrický fallback")
                else:
                    log.info("INTEL_ENABLED=false — LLM selection přeskočena, použiji metrický fallback.")

                # Fallback: výběr na základě volume + price change (bez LLM)
                if not selections:
                    selections = _metric_based_selection(data, eligible)
                    selection_source = "metric_fallback"

            always = [s.strip() for s in settings.ALWAYS_ACTIVE_SYMBOLS.split(",") if s.strip()]
            recommended = list(dict.fromkeys(
                always + [s["pair"] for s in selections[:settings.MAX_DYNAMIC_SYMBOLS]]
            ))

            details = {s["symbol"]: {"outlook": s["outlook"], "reason": s["reason"]}
                       for s in selections}

            db.asset_recommendations.insert_one({
                "created_at": datetime.now(timezone.utc),
                "source": f"market_intel_worker ({selection_source})",
                "symbols": recommended,
                "details": details,
                "always_active": always,
                "overall": intel.get("overall", "NEUTRAL"),
            })
            log.info(f"Asset recommendations uloženy ({selection_source}): {recommended}")
        except Exception as e:
            log.error(f"Asset selection selhal: {e}", exc_info=True)


def main():
    parser = argparse.ArgumentParser(description="AIInvest Market Intelligence Worker")
    parser.add_argument("--once", action="store_true", help="Spustit jednou a skončit")
    args = parser.parse_args()

    if args.once:
        run_once()
        return

    log.info(f"Market intel worker startuje, interval={INTERVAL_SECONDS}s")
    while True:
        try:
            run_once()
        except Exception as e:
            log.error(f"Cyklus selhal: {e}", exc_info=True)
        log.info(f"Čekám {INTERVAL_SECONDS}s do dalšího cyklu...")
        time.sleep(INTERVAL_SECONDS)


if __name__ == "__main__":
    main()
