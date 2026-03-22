import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path('python-core').resolve()))
from trading.mongo import get_db  # type: ignore

RUN_DIR = Path(r"C:\aiinvest\_shadow_tests\shadow-suite-20260305-152706")
state = json.loads((RUN_DIR / "state.json").read_text(encoding="utf-8-sig"))
run_id = "72208ec2578f"
raw_started = state["started_at"]
m = re.match(r"^(.*?\.)(\d+)([+-].*|Z)$", raw_started)
if m:
    raw_started = f"{m.group(1)}{m.group(2)[:6]}{m.group(3)}"
started = datetime.fromisoformat(raw_started.replace('Z','+00:00'))
now = datetime.now(timezone.utc)

horizons = [15,30,45] + list(range(60,10081,60))
MIN_PF_EVAL_SAMPLES = 120
MIN_PF_GROSS_LOSS = 0.5

db = get_db()
since_iso = started.astimezone(timezone.utc).isoformat()
rows = list(db.bot_signals.find({"run_id": run_id, "t": {"$gte": since_iso}}, {"_id": 0}).sort("t", -1))
policy = [x for x in rows if str(x.get("action", "")).lower() == "policy"]
shadow = [x for x in rows if str(x.get("action", "")).lower() == "shadow"]
executed = [x for x in rows if str(x.get("action", "")).lower() == "executed"]

prio = {"policy":1,"shadow":2,"executed":3}
dedup = {}
for r in rows:
    a = str(r.get('action','')).lower()
    if a not in prio:
        continue
    k=(str(r.get('symbol') or '').strip(), str(r.get('side') or '').strip().upper(), str(r.get('t') or ''))
    if not k[0] or k[1] not in {'BUY','SELL'} or not k[2]:
        continue
    p=dedup.get(k)
    if p is None or prio[a] > prio.get(str(p.get('action','')).lower(),0):
        dedup[k]=r

dedup_keys=set(dedup.keys())

out=[]
for h in horizons:
    eval_docs = db.signal_quality_shadow_eval.find({"run_id":run_id,"horizon_min":int(h)}, {"_id":0,"symbol":1,"side":1,"t":1,"ret_h":1})
    rets=[]
    for e in eval_docs:
        try:
            k=(str(e.get('symbol') or '').strip(), str(e.get('side') or '').strip().upper(), str(e.get('t') or ''))
            if k in dedup_keys:
                rets.append(float(e.get('ret_h')))
        except Exception:
            pass
    n=len(rets)
    wr=(sum(1 for x in rets if x>0)/n) if n else 0.0
    gp=sum(x for x in rets if x>0)
    gl=abs(sum(x for x in rets if x<0))
    pf_raw=(gp/gl) if gl>0 else (float('inf') if gp>0 else 0.0)
    pf=pf_raw
    if n < MIN_PF_EVAL_SAMPLES or gl < MIN_PF_GROSS_LOSS:
        pf="n/a"

    cmd=[
        r"C:\aiinvest\python-core\venv\Scripts\python.exe", r"C:\aiinvest\python-core\shadow_local_pnl.py",
        "--run-id", run_id, "--from-iso", started.isoformat(), "--to-iso", now.isoformat(),
        "--horizon-min", str(h), "--actions", "shadow,executed",
        "--kraken-eur","100","--binance-eur","100","--ibkr-eur","100",
        "--stake-pct","0.10",
        "--binance-fee-rate","0.001","--kraken-fee-rate","0.0025","--ibkr-fee-rate","0.0",
        "--kraken-bases","BTC,ETH",
        "--binance-bases","SOL,BNB,XRP,DOGE,TRX,USDC",
        "--ibkr-bases","PAXG,EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL",
    ]
    p=subprocess.run(cmd,capture_output=True,text=True,check=True)
    pnl=json.loads(p.stdout)

    row={
        "day": 1 if h<=1440 else int((h+1439)//1440),
        "h": h,
        "eval": n,
        "wr": wr,
        "pf": pf,
        "pf_raw": pf_raw,
        "gross_profit": gp,
        "gross_loss": gl,
        "kraken_equity": float(pnl['kraken']['equity_end']),
        "kraken_buffer": float(pnl['kraken']['cash_buffer_end']),
        "kraken_total": float(pnl['kraken']['equity_end']) + float(pnl['kraken']['cash_buffer_end']),
        "kraken_pnl_vs_100": (float(pnl['kraken']['equity_end']) + float(pnl['kraken']['cash_buffer_end']) - 100.0),
        "binance_equity": float(pnl['binance']['equity_end']),
        "binance_buffer": float(pnl['binance']['cash_buffer_end']),
        "binance_total": float(pnl['binance']['equity_end']) + float(pnl['binance']['cash_buffer_end']),
        "binance_pnl_vs_100": (float(pnl['binance']['equity_end']) + float(pnl['binance']['cash_buffer_end']) - 100.0),
        "ibkr_equity": float(pnl['ibkr']['equity_end']),
        "ibkr_buffer": float(pnl['ibkr']['cash_buffer_end']),
        "ibkr_total": float(pnl['ibkr']['equity_end']) + float(pnl['ibkr']['cash_buffer_end']),
        "ibkr_pnl_vs_100": (float(pnl['ibkr']['equity_end']) + float(pnl['ibkr']['cash_buffer_end']) - 100.0),
        "combined_equity": float(pnl['combined']['equity_end']),
        "combined_buffer": float(pnl['combined']['cash_buffer_end']),
        "combined_total": float(pnl['combined']['equity_end']) + float(pnl['combined']['cash_buffer_end']),
        "combined_pnl_vs_300": (float(pnl['combined']['equity_end']) + float(pnl['combined']['cash_buffer_end']) - 300.0),
        "fees": float(pnl['combined']['fees_paid_total']),
    }
    out.append(row)

headers=["day","h","eval","wr","pf","pf_raw","gross_profit","gross_loss","kraken_equity","kraken_buffer","kraken_total","kraken_pnl_vs_100","binance_equity","binance_buffer","binance_total","binance_pnl_vs_100","ibkr_equity","ibkr_buffer","ibkr_total","ibkr_pnl_vs_100","combined_equity","combined_buffer","combined_total","combined_pnl_vs_300","fees"]

def cs(v,d=4):
    if isinstance(v,str): return v
    return f"{v:.{d}f}".replace('.',',')

def row_to_csv(r):
    vals=[]
    for h in headers:
        v=r[h]
        if h in {"day","h","eval"}:
            vals.append(str(int(v)))
        elif h in {"wr","pf","pf_raw","gross_profit","gross_loss"}:
            vals.append(cs(v,4) if not isinstance(v,str) else v)
        else:
            vals.append(cs(v,4))
    return '"'+'","'.join(vals)+'"'

csv_lines=['"'+'","'.join(headers)+'"']+[row_to_csv(r) for r in out]
md=[]
md.append('# Full Report All Horizons (Accounts)')
md.append('')
md.append(f"Generated: {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}")
md.append(f"RunDir: {str(RUN_DIR).lower()}")
md.append(f"RunId: {run_id}")
md.append(f"Window: {started.isoformat()} -> {now.isoformat()}")
md.append('Initial capital: 3x100 EUR = 300 EUR')
md.append('')
md.append('| day | h | eval | WR | PF | PF_raw | GP | GL | K_eq | K_buf | K_total | K_pnl_vs100 | B_eq | B_buf | B_total | B_pnl_vs100 | I_eq | I_buf | I_total | I_pnl_vs100 | C_eq | C_buf | C_total | C_pnl_vs300 | Fees |')
md.append('|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|')
for r in out:
    pfv = r['pf'] if isinstance(r['pf'],str) else cs(r['pf'],4)
    md.append(f"| {r['day']} | {r['h']} | {r['eval']} | {cs(r['wr'],4)} | {pfv} | {cs(r['pf_raw'],4)} | {cs(r['gross_profit'],4)} | {cs(r['gross_loss'],4)} | {cs(r['kraken_equity'])} | {cs(r['kraken_buffer'])} | {cs(r['kraken_total'])} | {cs(r['kraken_pnl_vs_100'])} | {cs(r['binance_equity'])} | {cs(r['binance_buffer'])} | {cs(r['binance_total'])} | {cs(r['binance_pnl_vs_100'])} | {cs(r['ibkr_equity'])} | {cs(r['ibkr_buffer'])} | {cs(r['ibkr_total'])} | {cs(r['ibkr_pnl_vs_100'])} | {cs(r['combined_equity'])} | {cs(r['combined_buffer'])} | {cs(r['combined_total'])} | {cs(r['combined_pnl_vs_300'])} | {cs(r['fees'])} |")

for stem in ('report_all_h_full_accounts','report_all_h_full'):
    (RUN_DIR / f'{stem}.csv').write_text('\n'.join(csv_lines)+'\n', encoding='utf-8')
    (RUN_DIR / f'{stem}.md').write_text('\n'.join(md)+'\n', encoding='utf-8')

print('UPDATED report_all_h_full_accounts + report_all_h_full')
print(f'ROWS {len(out)}')
print(f'COUNTS total={len(rows)} dedup={len(dedup_keys)} shadow={len(shadow)} policy={len(policy)} executed={len(executed)}')
print('UNIQUE_PAIRS ' + ','.join(sorted({k[0] for k in dedup_keys})))
ibkr_bases = {"PAXG","EURUSD","GBPUSD","USDJPY","XAUUSD","XAGUSD","CL"}
ibkr_pairs = sorted({k[0] for k in dedup_keys if str(k[0]).split('/')[0].upper() in ibkr_bases})
print('IBKR_UNIQUE_PAIRS ' + (','.join(ibkr_pairs) if ibkr_pairs else 'NONE'))

