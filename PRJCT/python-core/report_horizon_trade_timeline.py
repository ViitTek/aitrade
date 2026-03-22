import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from pymongo import MongoClient

from trading.config import settings


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "RPRTS" / "_shadow-reports" / "bin_krak"

SUITE_CONFIG = {
    "bin_krak": {
        "title": "Trade Timeline Report",
        "accounts": ["kraken", "binance"],
        "base_capital": 200.0,
        "capital_suffix": "200",
        "kraken_start": 100.0,
        "binance_start": 100.0,
        "ibkr_start": 0.0,
        "kraken_fee_rate": 0.0025,
        "binance_fee_rate": 0.0010,
        "ibkr_fee_rate": 0.0,
    },
    "ibkr": {
        "title": "IBKR Trade Timeline Report",
        "accounts": ["ibkr"],
        "base_capital": 100.0,
        "capital_suffix": "100",
        "kraken_start": 0.0,
        "binance_start": 0.0,
        "ibkr_start": 100.0,
        "kraken_fee_rate": 0.0,
        "binance_fee_rate": 0.0,
        "ibkr_fee_rate": 0.0,
    },
}


def parse_iso_utc(value: str) -> datetime:
    s = str(value or "").strip()
    if not s:
        return datetime.now(timezone.utc)
    if "." in s:
        head, tail = s.split(".", 1)
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
        frac = frac[:6] if frac else ""
        s = f"{head}.{frac}{tz}" if frac else f"{head}{tz}"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def norm_t(value: str) -> str:
    return parse_iso_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify_account(symbol: str) -> str:
    base = str(symbol or "").strip().upper()
    base = base.split("/")[0] if "/" in base else base
    if base in {"BTC", "ETH"}:
        return "kraken"
    if base in {"SOL", "BNB", "XRP", "DOGE", "TRX", "USDC"}:
        return "binance"
    if base in {"PAXG", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "CL"}:
        return "ibkr"
    return "binance"


def infer_suite(explicit_suite: str | None, output_dir: Path) -> str:
    if explicit_suite:
        suite = explicit_suite.strip().lower()
        if suite in SUITE_CONFIG:
            return suite
    if output_dir.name.strip().lower() == "ibkr":
        return "ibkr"
    return "bin_krak"


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate progressive horizon trade timeline report")
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--from-iso", required=True)
    ap.add_argument("--to-iso", required=True)
    ap.add_argument("--horizon-min", type=int, default=720)
    ap.add_argument("--actions", default="shadow,executed")
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--suite", default=None)
    args = ap.parse_args()

    output_dir = Path(args.output_dir).expanduser()
    suite = infer_suite(args.suite, output_dir)
    cfg = SUITE_CONFIG[suite]

    from_iso = norm_t(args.from_iso)
    to_iso = norm_t(args.to_iso)
    split_reinvest = float(settings.PROFIT_SPLIT_REINVEST)
    split_reinvest = max(0.0, min(split_reinvest, 1.0))

    client = MongoClient(settings.MONGO_URI, tz_aware=True)
    db = client[settings.MONGO_DB]

    actions = [a.strip().lower() for a in str(args.actions).split(",") if a.strip()]
    rows = list(
        db.bot_signals.find(
            {
                "run_id": args.run_id,
                "action": {"$in": actions},
                "t": {"$gte": from_iso, "$lte": to_iso},
            },
            {"_id": 0, "symbol": 1, "side": 1, "t": 1, "action": 1},
        ).sort("t", 1)
    )

    prio = {"policy": 1, "shadow": 2, "executed": 3}
    dedup = {}
    for r in rows:
        a = str(r.get("action", "")).lower()
        if a not in prio:
            continue
        k = (
            str(r.get("symbol") or "").strip(),
            str(r.get("side") or "").strip().upper(),
            norm_t(str(r.get("t") or "")),
        )
        if not k[0] or k[1] not in {"BUY", "SELL"}:
            continue
        prev = dedup.get(k)
        if prev is None or prio[a] > prio.get(str(prev.get("action", "")).lower(), 0):
            dedup[k] = r

    eval_docs = list(
        db.signal_quality_shadow_eval.find(
            {
                "run_id": args.run_id,
                "horizon_min": int(args.horizon_min),
                "t": {"$gte": from_iso, "$lte": to_iso},
            },
            {"_id": 0, "symbol": 1, "side": 1, "t": 1, "ret_h": 1},
        )
    )
    eval_map = {}
    for e in eval_docs:
        key = (
            str(e.get("symbol") or "").strip(),
            str(e.get("side") or "").strip().upper(),
            norm_t(str(e.get("t") or "")),
        )
        try:
            eval_map[key] = float(e.get("ret_h"))
        except Exception:
            pass

    eq = {
        "kraken": float(cfg["kraken_start"]),
        "binance": float(cfg["binance_start"]),
        "ibkr": float(cfg["ibkr_start"]),
    }
    buf = {"kraken": 0.0, "binance": 0.0, "ibkr": 0.0}
    fees_paid = {"kraken": 0.0, "binance": 0.0, "ibkr": 0.0}
    fee_rates = {
        "kraken": float(cfg["kraken_fee_rate"]),
        "binance": float(cfg["binance_fee_rate"]),
        "ibkr": float(cfg["ibkr_fee_rate"]),
    }
    active_accounts = set(cfg["accounts"])
    stake_pct = 0.10

    timeline = []
    seq = 0
    ordered_keys = sorted(dedup.keys(), key=lambda x: x[2])
    for key in ordered_keys:
        ret = eval_map.get(key)
        if ret is None:
            continue
        symbol, side, t = key
        account = classify_account(symbol)
        if account not in active_accounts:
            continue
        seq += 1
        stake = eq[account] * stake_pct
        fees = stake * fee_rates[account] * 2.0
        pnl = (stake * float(ret)) - fees
        fees_paid[account] += fees
        if pnl > 0:
            reinvest = pnl * split_reinvest
            eq[account] += reinvest
            buf[account] += (pnl - reinvest)
        else:
            eq[account] += pnl
        combined_eq = sum(eq[a] for a in active_accounts)
        combined_buf = sum(buf[a] for a in active_accounts)
        row = {
            "seq": seq,
            "t": t,
            "symbol": symbol,
            "side": side,
            "account": account,
            "ret_h": float(ret),
            "stake": round(stake, 4),
            "fees": round(fees, 4),
            "pnl": round(pnl, 4),
            "c_eq": round(combined_eq, 4),
            "c_buf": round(combined_buf, 4),
            "c_total": round(combined_eq + combined_buf, 4),
            "c_pnl_vs_base": round(combined_eq + combined_buf - float(cfg["base_capital"]), 4),
        }
        if suite == "bin_krak":
            row["k_eq"] = round(eq["kraken"], 4)
            row["k_buf"] = round(buf["kraken"], 4)
            row["b_eq"] = round(eq["binance"], 4)
            row["b_buf"] = round(buf["binance"], 4)
        else:
            row["i_eq"] = round(eq["ibkr"], 4)
            row["i_buf"] = round(buf["ibkr"], 4)
        timeline.append(row)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    out_path = output_dir / f"trade_timeline_h{int(args.horizon_min)}_{args.run_id}_{stamp}.md"

    md = []
    md.append(f"# {cfg['title']} h={int(args.horizon_min)} min")
    md.append("")
    md.append(f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}")
    md.append(f"RunId: {args.run_id}")
    md.append(f"Window: {from_iso} -> {to_iso}")
    md.append(f"Actions: {','.join(actions)}")
    md.append(f"Trades with eval: {len(timeline)}")
    md.append(f"Initial capital: {int(cfg['base_capital'])} EUR")
    md.append("")
    md.append("## Summary")
    md.append("")
    if suite == "bin_krak":
        md.append(f"- Kraken: equity `{eq['kraken']:.4f}`, buffer `{buf['kraken']:.4f}`, fees `{fees_paid['kraken']:.4f}`")
        md.append(f"- Binance: equity `{eq['binance']:.4f}`, buffer `{buf['binance']:.4f}`, fees `{fees_paid['binance']:.4f}`")
    else:
        md.append(f"- IBKR: equity `{eq['ibkr']:.4f}`, buffer `{buf['ibkr']:.4f}`, fees `{fees_paid['ibkr']:.4f}`")
    combined_total = sum(eq[a] + buf[a] for a in active_accounts)
    md.append(f"- Combined total: `{combined_total:.4f}`")
    md.append(f"- Combined PnL vs {int(cfg['base_capital'])}: `{combined_total - float(cfg['base_capital']):.4f}`")
    md.append("")
    md.append("## Trade By Trade")
    md.append("")
    if suite == "bin_krak":
        md.append(f"| # | t | symbol | side | acct | ret_h | stake | fees | pnl | K_eq | K_buf | B_eq | B_buf | C_eq | C_buf | C_total | C_pnl_vs{cfg['capital_suffix']} |")
        md.append("|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in timeline:
            md.append(
                f"| {r['seq']} | {r['t']} | {r['symbol']} | {r['side']} | {r['account']} | {r['ret_h']:.6f} | {r['stake']:.4f} | {r['fees']:.4f} | {r['pnl']:.4f} | {r['k_eq']:.4f} | {r['k_buf']:.4f} | {r['b_eq']:.4f} | {r['b_buf']:.4f} | {r['c_eq']:.4f} | {r['c_buf']:.4f} | {r['c_total']:.4f} | {r['c_pnl_vs_base']:.4f} |"
            )
    else:
        md.append(f"| # | t | symbol | side | acct | ret_h | stake | fees | pnl | I_eq | I_buf | C_eq | C_buf | C_total | C_pnl_vs{cfg['capital_suffix']} |")
        md.append("|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
        for r in timeline:
            md.append(
                f"| {r['seq']} | {r['t']} | {r['symbol']} | {r['side']} | {r['account']} | {r['ret_h']:.6f} | {r['stake']:.4f} | {r['fees']:.4f} | {r['pnl']:.4f} | {r['i_eq']:.4f} | {r['i_buf']:.4f} | {r['c_eq']:.4f} | {r['c_buf']:.4f} | {r['c_total']:.4f} | {r['c_pnl_vs_base']:.4f} |"
            )

    out_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(json.dumps({"ok": True, "path": str(out_path), "trades": len(timeline)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
