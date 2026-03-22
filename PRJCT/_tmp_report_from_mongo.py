import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path('python-core').resolve()))

from trading.mongo import get_db  # type: ignore

RUN_DIR = Path(r"C:\aiinvest\_shadow_tests\shadow-suite-20260305-152706")
CSV_PATH = RUN_DIR / "report_all_h_full.csv"
MD_PATH = RUN_DIR / "report_all_h_full.md"

state = json.loads((RUN_DIR / "state.json").read_text(encoding="utf-8-sig"))
run_id = "72208ec2578f"
raw_started = state["started_at"]
if "." in raw_started:
    m = re.match(r"^(.*?\.)(\d+)([+-].*|Z)$", raw_started)
    if m:
        raw_started = f"{m.group(1)}{m.group(2)[:6]}{m.group(3)}"
started = datetime.fromisoformat(raw_started.replace("Z", "+00:00"))
now = datetime.now(timezone.utc)

horizons = [15, 30, 45] + list(range(60, 10081, 60))

# Mongo-only metrics
lookback_h = 720
since_iso = (datetime.now(timezone.utc) - timedelta(hours=lookback_h)).isoformat()
actions = {"shadow", "policy", "executed"}

db = get_db()

rows = list(db.bot_signals.find({"run_id": run_id, "t": {"$gte": since_iso}}, {"_id": 0}).sort("t", -1).limit(10000))
policy = [x for x in rows if str(x.get("action", "")).lower() == "policy"]
blocked = [x for x in rows if str(x.get("action", "")).lower() == "blocked"]
shadow = [x for x in rows if str(x.get("action", "")).lower() == "shadow"]
executed = [x for x in rows if str(x.get("action", "")).lower() == "executed"]

prio = {"policy": 1, "shadow": 2, "executed": 3}
dedup = {}
for r in rows:
    a = str(r.get("action", "")).lower()
    if a not in actions:
        continue
    key = (str(r.get("symbol") or "").strip(), str(r.get("side") or "").strip().upper(), str(r.get("t") or ""))
    if not key[0] or key[1] not in {"BUY", "SELL"} or not key[2]:
        continue
    prev = dedup.get(key)
    if prev is None or prio.get(a, 0) > prio.get(str(prev.get("action", "")).lower(), 0):
        dedup[key] = r

dedup_rows = list(dedup.values())

out_rows = []
for h in horizons:
    # eval from cached collection only (Mongo)
    q = {"run_id": run_id, "horizon_min": int(h)}
    eval_docs = list(db.signal_quality_shadow_eval.find(q, {"_id": 0, "symbol": 1, "side": 1, "t": 1, "ret_h": 1}))
    emap = {}
    for e in eval_docs:
        try:
            k = (str(e.get("symbol") or "").strip(), str(e.get("side") or "").strip().upper(), str(e.get("t") or ""))
            emap[k] = float(e.get("ret_h"))
        except Exception:
            pass

    eval_pool = [s for s in dedup_rows if str(s.get("action", "")).lower() in actions]
    rets = []
    for s in eval_pool:
        k = (str(s.get("symbol") or "").strip(), str(s.get("side") or "").strip().upper(), str(s.get("t") or ""))
        if k in emap:
            rets.append(emap[k])

    n = len(rets)
    wins = sum(1 for x in rets if x > 0)
    wr = (wins / n) if n else 0.0
    gp = sum(x for x in rets if x > 0)
    gl = abs(sum(x for x in rets if x < 0))
    pf = (gp / gl) if gl > 0 else (float("inf") if gp > 0 else 0.0)
    avg_ret = (sum(rets) / n) if n else 0.0

    # Local pnl from Mongo via existing script
    cmd = [
        r"C:\aiinvest\python-core\venv\Scripts\python.exe",
        r"C:\aiinvest\python-core\shadow_local_pnl.py",
        "--run-id", run_id,
        "--from-iso", started.isoformat(),
        "--to-iso", now.isoformat(),
        "--horizon-min", str(h),
        "--actions", "shadow,executed",
        "--kraken-eur", "100", "--binance-eur", "100", "--ibkr-eur", "100",
        "--stake-pct", "0.10",
        "--binance-fee-rate", "0.001", "--kraken-fee-rate", "0.0025", "--ibkr-fee-rate", "0.0",
        "--kraken-bases", "BTC,ETH",
        "--binance-bases", "SOL,BNB,DOGE,TRX,XRP,PAXG,USDC",
        "--ibkr-bases", "",
    ]
    p = subprocess.run(cmd, capture_output=True, text=True, check=True)
    pnl = json.loads(p.stdout)

    out_rows.append({
        "day": 1 if h <= 1440 else int((h + 1439) // 1440),
        "h": h,
        "total": len(rows),
        "dedup": len(dedup_rows),
        "shadow": len(shadow),
        "policy": len(policy),
        "executed": len(executed),
        "eval": n,
        "wr": wr,
        "pf": pf if pf != float("inf") else "inf",
        "avg_ret": avg_ret,
        "kraken_eq": float(pnl["kraken"]["equity_end"]),
        "binance_eq": float(pnl["binance"]["equity_end"]),
        "ibkr_eq": float(pnl["ibkr"]["equity_end"]),
        "total_eq": float(pnl["combined"]["equity_end"]),
        "total_pnl_eur": float(pnl["combined"]["pnl_eur"]),
        "fees": float(pnl["combined"]["fees_paid_total"]),
    })

def fmt(v, d=4):
    if isinstance(v, str):
        return v
    return f"{v:.{d}f}".replace(".", ",")

# CSV
header = ["day","h","total","dedup","shadow","policy","executed","eval","wr","pf","avg_ret","kraken_eq","binance_eq","ibkr_eq","total_eq","total_pnl_eur","fees"]
lines = ["\"" + "\",\"".join(header) + "\""]
for r in out_rows:
    vals = [
        str(r["day"]), str(r["h"]), str(r["total"]), str(r["dedup"]), str(r["shadow"]), str(r["policy"]), str(r["executed"]), str(r["eval"]),
        fmt(r["wr"],4), fmt(r["pf"],4) if r["pf"] != "inf" else "inf", fmt(r["avg_ret"],6),
        fmt(r["kraken_eq"],4), fmt(r["binance_eq"],4), fmt(r["ibkr_eq"],4), fmt(r["total_eq"],4), fmt(r["total_pnl_eur"],4), fmt(r["fees"],4)
    ]
    lines.append("\"" + "\",\"".join(vals) + "\"")
CSV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8-sig")

# MD
md = []
md.append("# Full Report All Horizons")
md.append("")
md.append(f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}")
md.append(f"RunDir: {str(RUN_DIR).lower()}")
md.append(f"RunId: {run_id}")
md.append(f"Window: {started.isoformat()} -> {now.isoformat()}")
md.append("Horizons: 15,30,45,60..10080 (step 60)")
md.append("")
md.append("| day | h | total | dedup | shadow | policy | executed | eval | WR | PF | avg_ret | KrakenEqEUR | BinanceEqEUR | IBKREqEUR | TotalEqEUR | TotalPnLEUR | FeesEUR |")
md.append("|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|")
for r in out_rows:
    md.append(
        f"| {r['day']} | {r['h']} | {r['total']} | {r['dedup']} | {r['shadow']} | {r['policy']} | {r['executed']} | {r['eval']} | {fmt(r['wr'],4)} | {('inf' if r['pf']=='inf' else fmt(r['pf'],4))} | {fmt(r['avg_ret'],6)} | {fmt(r['kraken_eq'],4)} | {fmt(r['binance_eq'],4)} | {fmt(r['ibkr_eq'],4)} | {fmt(r['total_eq'],4)} | {fmt(r['total_pnl_eur'],4)} | {fmt(r['fees'],4)} |"
    )
MD_PATH.write_text("\n".join(md) + "\n", encoding="utf-8-sig")

print(f"UPDATED: {CSV_PATH}")
print(f"UPDATED: {MD_PATH}")
print(f"ROWS: {len(out_rows)}")
print(f"COUNTS: total={len(rows)} dedup={len(dedup_rows)} shadow={len(shadow)} policy={len(policy)} executed={len(executed)} blocked={len(blocked)}")


