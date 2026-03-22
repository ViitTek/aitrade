import argparse
import json
from datetime import datetime, timezone
from pymongo import MongoClient
from trading.config import settings


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
        frac = (frac[:6] if frac else "")
        s = f"{head}.{frac}{tz}" if frac else f"{head}{tz}"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm_t(value: str) -> str:
    return parse_iso_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--run-ids", default=None)
    ap.add_argument("--from-iso", required=True)
    ap.add_argument("--to-iso", required=True)
    ap.add_argument("--horizon-min", type=int, default=60)
    ap.add_argument("--actions", default="shadow")
    # Preferred account starts (EUR)
    ap.add_argument("--kraken-eur", type=float, default=100.0)
    ap.add_argument("--binance-eur", type=float, default=100.0)
    ap.add_argument("--ibkr-eur", type=float, default=100.0)
    # Backward-compatible aliases (USD legacy naming)
    ap.add_argument("--kraken-usd", type=float, default=None)
    ap.add_argument("--binance-usd", type=float, default=None)
    ap.add_argument("--stake-pct", type=float, default=0.10)
    ap.add_argument("--profit-split-reinvest", type=float, default=None)
    ap.add_argument("--binance-fee-rate", type=float, default=0.0010)  # 0.10% per side
    ap.add_argument("--kraken-fee-rate", type=float, default=0.0025)    # 0.25% per side (maker baseline)
    ap.add_argument("--ibkr-fee-rate", type=float, default=0.00005)     # 0.005% per side baseline
    ap.add_argument("--kraken-bases", default="ETH")
    ap.add_argument("--binance-bases", default="SOL,PAXG,BNB,XRP,DOGE,TRX,USDC")
    ap.add_argument("--ibkr-bases", default="BTC")
    args = ap.parse_args()

    client = MongoClient(settings.MONGO_URI, tz_aware=True)
    db = client[settings.MONGO_DB]

    actions = [a.strip().lower() for a in str(args.actions).split(",") if a.strip()]
    kraken_bases = {x.strip().upper() for x in str(args.kraken_bases).split(",") if x.strip()}
    binance_bases = {x.strip().upper() for x in str(args.binance_bases).split(",") if x.strip()}
    ibkr_bases = {x.strip().upper() for x in str(args.ibkr_bases).split(",") if x.strip()}
    stake_pct = max(0.0, min(float(args.stake_pct), 1.0))
    split_reinvest = float(args.profit_split_reinvest) if args.profit_split_reinvest is not None else float(settings.PROFIT_SPLIT_REINVEST)
    split_reinvest = max(0.0, min(split_reinvest, 1.0))
    binance_fee_rate = max(0.0, float(args.binance_fee_rate))
    kraken_fee_rate = max(0.0, float(args.kraken_fee_rate))
    ibkr_fee_rate = max(0.0, float(args.ibkr_fee_rate))

    # Final starts in EUR (allow legacy --*-usd overrides)
    kraken_start = float(args.kraken_usd) if args.kraken_usd is not None else float(args.kraken_eur)
    binance_start = float(args.binance_usd) if args.binance_usd is not None else float(args.binance_eur)
    ibkr_start = float(args.ibkr_eur)

    from_iso = norm_t(args.from_iso)
    to_iso = norm_t(args.to_iso)

    run_ids = []
    if args.run_ids:
        run_ids = [x.strip() for x in str(args.run_ids).split(",") if x.strip()]
    elif args.run_id:
        run_ids = [str(args.run_id).strip()]
    if not run_ids:
        raise SystemExit("No run ids provided")

    rows = list(
        db.bot_signals.find(
            {
                "run_id": {"$in": run_ids},
                "action": {"$in": actions},
                "t": {"$gte": from_iso, "$lte": to_iso},
            },
            {"_id": 0, "symbol": 1, "side": 1, "t": 1, "action": 1},
        ).sort("t", 1)
    )

    # Dedup by (symbol, side, t)
    dedup = {}
    for r in rows:
        sym = str(r.get("symbol") or "").strip()
        side = str(r.get("side") or "").strip().upper()
        t = norm_t(str(r.get("t") or ""))
        if not sym or side not in {"BUY", "SELL"}:
            continue
        key = (sym, side, t)
        dedup[key] = r

    eval_docs = list(
        db.signal_quality_shadow_eval.find(
            {
                "run_id": {"$in": run_ids},
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
            continue

    kraken_eq = kraken_start
    binance_eq = binance_start
    ibkr_eq = ibkr_start
    kraken_buf = 0.0
    binance_buf = 0.0
    ibkr_buf = 0.0
    kraken_fees_paid = 0.0
    binance_fees_paid = 0.0
    ibkr_fees_paid = 0.0
    k_trades = b_trades = i_trades = 0
    k_wins = b_wins = i_wins = 0

    for key in sorted(dedup.keys(), key=lambda x: x[2]):
        ret = eval_map.get(key)
        if ret is None:
            continue
        sym = key[0]
        base = sym.split("/")[0].upper() if "/" in sym else sym.upper()
        is_kraken = base in kraken_bases
        is_binance = base in binance_bases
        is_ibkr = base in ibkr_bases
        if is_kraken:
            k_trades += 1
            if ret > 0:
                k_wins += 1
            stake_notional = kraken_eq * stake_pct
            fees = stake_notional * kraken_fee_rate * 2.0
            kraken_fees_paid += fees
            pnl = (stake_notional * ret) - fees
            if pnl > 0:
                reinvest = pnl * split_reinvest
                kraken_eq += reinvest
                kraken_buf += (pnl - reinvest)
            else:
                kraken_eq += pnl
        elif is_binance:
            b_trades += 1
            if ret > 0:
                b_wins += 1
            stake_notional = binance_eq * stake_pct
            fees = stake_notional * binance_fee_rate * 2.0
            binance_fees_paid += fees
            pnl = (stake_notional * ret) - fees
            if pnl > 0:
                reinvest = pnl * split_reinvest
                binance_eq += reinvest
                binance_buf += (pnl - reinvest)
            else:
                binance_eq += pnl
        elif is_ibkr:
            i_trades += 1
            if ret > 0:
                i_wins += 1
            stake_notional = ibkr_eq * stake_pct
            fees = stake_notional * ibkr_fee_rate * 2.0
            ibkr_fees_paid += fees
            pnl = (stake_notional * ret) - fees
            if pnl > 0:
                reinvest = pnl * split_reinvest
                ibkr_eq += reinvest
                ibkr_buf += (pnl - reinvest)
            else:
                ibkr_eq += pnl
        else:
            # Fallback bucket: symbols not explicitly mapped go to Binance account.
            b_trades += 1
            if ret > 0:
                b_wins += 1
            stake_notional = binance_eq * stake_pct
            fees = stake_notional * binance_fee_rate * 2.0
            binance_fees_paid += fees
            pnl = (stake_notional * ret) - fees
            if pnl > 0:
                reinvest = pnl * split_reinvest
                binance_eq += reinvest
                binance_buf += (pnl - reinvest)
            else:
                binance_eq += pnl

    out = {
        "ok": True,
        "run_ids": run_ids,
        "window": {"from": from_iso, "to": to_iso, "horizon_min": int(args.horizon_min), "actions": actions},
        "params": {
            "currency": "EUR",
            "kraken_eur_start": kraken_start,
            "binance_eur_start": binance_start,
            "ibkr_eur_start": ibkr_start,
            "stake_pct": stake_pct,
            "profit_split_reinvest": split_reinvest,
            "binance_fee_rate_per_side": binance_fee_rate,
            "kraken_fee_rate_per_side": kraken_fee_rate,
            "ibkr_fee_rate_per_side": ibkr_fee_rate,
            "kraken_bases": sorted(list(kraken_bases)),
            "binance_bases": sorted(list(binance_bases)),
            "ibkr_bases": sorted(list(ibkr_bases)),
        },
        "kraken": {
            "trades": k_trades,
            "wins": k_wins,
            "win_rate": round((k_wins / k_trades), 4) if k_trades else 0.0,
            "equity_end": round(kraken_eq, 4),
            "cash_buffer_end": round(kraken_buf, 4),
            "fees_paid_total": round(kraken_fees_paid, 4),
            "pnl_eur": round(kraken_eq - kraken_start, 4),
            "pnl_usd": round(kraken_eq - kraken_start, 4),
        },
        "binance": {
            "trades": b_trades,
            "wins": b_wins,
            "win_rate": round((b_wins / b_trades), 4) if b_trades else 0.0,
            "equity_end": round(binance_eq, 4),
            "cash_buffer_end": round(binance_buf, 4),
            "fees_paid_total": round(binance_fees_paid, 4),
            "pnl_eur": round(binance_eq - binance_start, 4),
            "pnl_usd": round(binance_eq - binance_start, 4),
        },
        "ibkr": {
            "trades": i_trades,
            "wins": i_wins,
            "win_rate": round((i_wins / i_trades), 4) if i_trades else 0.0,
            "equity_end": round(ibkr_eq, 4),
            "cash_buffer_end": round(ibkr_buf, 4),
            "fees_paid_total": round(ibkr_fees_paid, 4),
            "pnl_eur": round(ibkr_eq - ibkr_start, 4),
            "pnl_usd": round(ibkr_eq - ibkr_start, 4),
        },
    }
    out["combined"] = {
        "trades": k_trades + b_trades + i_trades,
        "equity_end": round(kraken_eq + binance_eq + ibkr_eq, 4),
        "cash_buffer_end": round(kraken_buf + binance_buf + ibkr_buf, 4),
        "fees_paid_total": round(kraken_fees_paid + binance_fees_paid + ibkr_fees_paid, 4),
        "pnl_eur": round((kraken_eq + binance_eq + ibkr_eq) - (kraken_start + binance_start + ibkr_start), 4),
        "pnl_usd": round((kraken_eq + binance_eq + ibkr_eq) - (kraken_start + binance_start + ibkr_start), 4),
    }
    print(json.dumps(out, ensure_ascii=True))


if __name__ == "__main__":
    main()
