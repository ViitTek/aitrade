import argparse
import json
import re
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading.mongo import get_db  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "RPRTS" / "_shadow-reports" / "bin_krak"
SHADOW_TESTS_ROOT = REPO_ROOT / "RPRTS" / "_shadow_tests"
HORIZONS = [15, 30, 45] + list(range(60, 10081, 60))


SUITE_CONFIG = {
    "bin_krak": {
        "title": "Binance + Kraken Report All Horizons",
        "timeline_title": "Trade Timeline Report",
        "accounts": ["kraken", "binance"],
        "base_capital": 200.0,
        "capital_suffix": "200",
        "pnl_bases": {
            "kraken_start": 100.0,
            "binance_start": 100.0,
            "ibkr_start": 0.0,
            "kraken_bases": "BTC,ETH",
            "binance_bases": "SOL,BNB,XRP,DOGE,TRX,USDC",
            "ibkr_bases": "",
            "ibkr_fee_rate": 0.0,
        },
    },
    "ibkr": {
        "title": "IBKR Report All Horizons",
        "timeline_title": "IBKR Trade Timeline Report",
        "accounts": ["ibkr"],
        "base_capital": 100.0,
        "capital_suffix": "100",
        "pnl_bases": {
            "kraken_start": 0.0,
            "binance_start": 0.0,
            "ibkr_start": 100.0,
            "kraken_bases": "",
            "binance_bases": "",
            "ibkr_bases": "PAXG,EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL",
            "ibkr_fee_rate": 0.0,
        },
    },
}


def _parse_iso_utc(value: str) -> datetime:
    s = str(value or "").strip()
    if "." in s:
        m = re.match(r"^(.*?\.)(\d+)([+-].*|Z)$", s)
        if m:
            s = f"{m.group(1)}{m.group(2)[:6]}{m.group(3)}"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _safe_float(v, default=0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _cs(v, d=4) -> str:
    if isinstance(v, str):
        return v
    return f"{float(v):.{d}f}".replace(".", ",")


def _hour_value(minutes: int) -> str:
    hours = float(minutes) / 60.0
    if abs(hours - round(hours)) < 1e-9:
        return str(int(round(hours)))
    return f"{hours:.2f}".replace(".", ",")


def _infer_suite(explicit_suite: str | None, output_dir: Path) -> str:
    if explicit_suite:
        suite = explicit_suite.strip().lower()
        if suite in SUITE_CONFIG:
            return suite
    name = output_dir.name.strip().lower()
    if name == "ibkr":
        return "ibkr"
    return "bin_krak"


def _resolve_state_path(run_dir: Path | None) -> Path | None:
    if run_dir is not None:
        state_path = run_dir / "state.json"
        if state_path.exists():
            return state_path
        return None
    if not SHADOW_TESTS_ROOT.exists():
        return None
    dirs = sorted(
        [p for p in SHADOW_TESTS_ROOT.glob("shadow-suite-*") if p.is_dir()],
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    for run_dir in dirs:
        state_path = run_dir / "state.json"
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            if state.get("completed") is False:
                return state_path
        except Exception:
            continue
    for run_dir in dirs:
        state_path = run_dir / "state.json"
        if state_path.exists():
            return state_path
    return None


def _resolve_from_iso(explicit_from_iso: str | None, run_dir: Path | None) -> datetime:
    if explicit_from_iso:
        return _parse_iso_utc(explicit_from_iso)
    state_path = _resolve_state_path(run_dir)
    if state_path and state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
            return _parse_iso_utc(str(state.get("started_at") or ""))
        except Exception:
            pass
    return datetime.now(timezone.utc) - timedelta(days=30)


def _resolve_latest_run_id(api_base: str) -> str:
    last_err = None
    for _ in range(6):
        try:
            r = requests.get(f"{api_base.rstrip('/')}/bot/status", timeout=5)
            if r.status_code == 200:
                rid = str((r.json() or {}).get("run_id") or "").strip()
                if rid:
                    return rid
            last_err = f"status={r.status_code}"
        except Exception as exc:
            last_err = str(exc)
        time.sleep(2)
    raise RuntimeError(f"Unable to resolve run_id from {api_base.rstrip('/')}/bot/status ({last_err})")


def _resolve_suite_run_ids(
    api_base: str,
    from_iso: datetime,
    run_dir: Path | None,
    explicit_run_ids: list[str] | None = None,
) -> list[str]:
    if explicit_run_ids:
        return [x for x in explicit_run_ids if x]
    run_ids: list[str] = []
    if run_dir is not None:
        metrics_path = run_dir / "metrics.jsonl"
        if metrics_path.exists():
            for line in metrics_path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                status = row.get("status") or {}
                rid = str(status.get("run_id") or "").strip()
                if not rid:
                    continue
                try:
                    ts = _parse_iso_utc(str(row.get("t") or ""))
                except Exception:
                    continue
                if ts < from_iso:
                    continue
                if status.get("running") is True:
                    run_ids.append(rid)
    latest_run = _resolve_latest_run_id(api_base)
    if latest_run not in run_ids:
        run_ids.append(latest_run)
    unique: list[str] = []
    seen = set()
    for rid in run_ids:
        if rid in seen:
            continue
        seen.add(rid)
        unique.append(rid)
    return unique




def _classify_account(symbol: str) -> str:
    base = str(symbol or "").strip().upper()
    base = base.split("/")[0] if "/" in base else base
    if base in {"BTC", "ETH"}:
        return "kraken"
    if base in {"SOL", "BNB", "XRP", "DOGE", "TRX", "USDC"}:
        return "binance"
    if base in {"PAXG", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "CL"}:
        return "ibkr"
    return "binance"


def _fee_rate_for_account(account: str) -> float:
    if account == "kraken":
        return 0.0025
    if account == "binance":
        return 0.001
    if account == "ibkr":
        return 0.0
    return 0.001


def _gross_stats_from_eval(dedup_rows: dict, eval_map: dict, suite: str) -> dict:
    active_accounts = set(SUITE_CONFIG[suite]["accounts"])
    gross_profit = 0.0
    gross_loss = 0.0
    trade_count = 0
    win_count = 0

    ordered_keys = sorted(dedup_rows.keys(), key=lambda x: x[2])
    for key in ordered_keys:
        ret = eval_map.get(key)
        if ret is None:
            continue
        account = _classify_account(key[0])
        if account not in active_accounts:
            continue
        trade_count += 1
        if float(ret) > 0:
            win_count += 1
            gross_profit += float(ret)
        elif float(ret) < 0:
            gross_loss += abs(float(ret))

    pf_raw = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    wr = (win_count / trade_count) if trade_count else 0.0
    return {
        "eval": trade_count,
        "wr": wr,
        "pf_raw": pf_raw,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


def _compute_pnl(run_ids: list[str], from_iso: datetime, to_iso: datetime, horizon: int, suite: str) -> dict:
    cfg = SUITE_CONFIG[suite]["pnl_bases"]
    cmd = [
        sys.executable,
        str(Path(__file__).resolve().parent / "shadow_local_pnl.py"),
        "--run-ids",
        ",".join(run_ids),
        "--from-iso",
        from_iso.isoformat(),
        "--to-iso",
        to_iso.isoformat(),
        "--horizon-min",
        str(horizon),
        "--actions",
        "shadow,executed",
        "--kraken-eur",
        str(cfg["kraken_start"]),
        "--binance-eur",
        str(cfg["binance_start"]),
        "--ibkr-eur",
        str(cfg["ibkr_start"]),
        "--stake-pct",
        "0.10",
        "--binance-fee-rate",
        "0.001",
        "--kraken-fee-rate",
        "0.0025",
        "--ibkr-fee-rate",
        str(cfg["ibkr_fee_rate"]),
        "--kraken-bases",
        str(cfg["kraken_bases"]),
        "--binance-bases",
        str(cfg["binance_bases"]),
        "--ibkr-bases",
        str(cfg["ibkr_bases"]),
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _row_header(suite: str, capital_suffix: str) -> list[str]:
    common = ["day", "h", "eval", "WR", "PF_raw", "GP", "GL"]
    if suite == "bin_krak":
        return common + ["K_eq", "K_buf", "B_eq", "B_buf", "C_eq", "C_buf", "C_total", f"C_pnl_vs{capital_suffix}", "Fees", "C_pnl/h"]
    return common + ["I_eq", "I_buf", "C_eq", "C_buf", "C_total", f"C_pnl_vs{capital_suffix}", "Fees", "C_pnl/h"]


def _row_values(r: dict, suite: str, capital_suffix: str) -> list[str]:
    hours = max(0.25, float(r["h"]) / 60.0)
    c_pnl_per_hour = float(r["combined_pnl_vs_base"]) / hours
    common = [
        str(r["day"]),
        _hour_value(int(r["h"])),
        str(r["eval"]),
        _cs(r["wr"], 4),
        _cs(r["pf_raw"], 4),
        _cs(r["gross_profit"], 4),
        _cs(r["gross_loss"], 4),
    ]
    if suite == "bin_krak":
        extra = [
            _cs(r["kraken_equity"]),
            _cs(r["kraken_buffer"]),
            _cs(r["binance_equity"]),
            _cs(r["binance_buffer"]),
            _cs(r["combined_equity"]),
            _cs(r["combined_buffer"]),
            _cs(r["combined_total"]),
            _cs(r["combined_pnl_vs_base"]),
            _cs(r["fees"]),
            _cs(c_pnl_per_hour),
        ]
    else:
        extra = [
            _cs(r["ibkr_equity"]),
            _cs(r["ibkr_buffer"]),
            _cs(r["combined_equity"]),
            _cs(r["combined_buffer"]),
            _cs(r["combined_total"]),
            _cs(r["combined_pnl_vs_base"]),
            _cs(r["fees"]),
            _cs(c_pnl_per_hour),
        ]
    return common + extra


def _md_table(lines: list[list[str]]) -> list[str]:
    header = lines[0]
    out = []
    out.append("| " + " | ".join(header) + " |")
    out.append("|" + "|".join("---:" for _ in header) + "|")
    for row in lines[1:]:
        out.append("| " + " | ".join(row) + " |")
    return out


def _write_md_report(rows: list[dict], meta: dict, output_dir: Path, title: str, suffix: str = "") -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    latest_run_id = meta["latest_run_id"]
    suite = meta["suite"]
    name_suffix = f"_{suffix}" if suffix else ""
    path = output_dir / f"report_all_h_{suite}_{latest_run_id}_{stamp}{name_suffix}.md"
    header = _row_header(meta["suite"], meta["capital_suffix"])
    lines = [[*header]]
    for r in rows:
        lines.append(_row_values(r, meta["suite"], meta["capital_suffix"]))

    md = []
    md.append(f"# {title}")
    md.append("")
    md.append(f"Generated: {meta['generated_at']}")
    md.append(f"Suite: {meta['suite']}")
    md.append(f"Latest RunId: {meta['latest_run_id']}")
    md.append(f"Suite RunIds: {', '.join(meta['run_ids'])}")
    md.append(f"Window: {meta['window_from']} -> {meta['window_to']}")
    md.append(f"Initial capital: {int(meta['base_capital'])} EUR")
    md.append("")
    md.extend(_md_table(lines))
    md.append("")
    path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return path


def _write_special_reports(rows: list[dict], meta: dict, output_dir: Path, state_dir: Path) -> list[Path]:
    return []


def build_report(run_ids: list[str], from_iso: datetime, suite: str, output_dir: Path, latest_run_id: str) -> tuple[list[dict], dict]:
    db = get_db()
    now = datetime.now(timezone.utc)

    rows = list(
        db.bot_signals.find(
            {"run_id": {"$in": run_ids}, "t": {"$gte": from_iso.isoformat()}},
            {"_id": 0},
        ).sort("t", -1)
    )
    policy = [x for x in rows if str(x.get("action", "")).lower() == "policy"]
    shadow = [x for x in rows if str(x.get("action", "")).lower() == "shadow"]
    executed = [x for x in rows if str(x.get("action", "")).lower() == "executed"]

    prio = {"policy": 1, "shadow": 2, "executed": 3}
    dedup = {}
    for r in rows:
        a = str(r.get("action", "")).lower()
        if a not in prio:
            continue
        k = (
            str(r.get("symbol") or "").strip(),
            str(r.get("side") or "").strip().upper(),
            str(r.get("t") or ""),
        )
        if not k[0] or k[1] not in {"BUY", "SELL"} or not k[2]:
            continue
        p = dedup.get(k)
        if p is None or prio[a] > prio.get(str(p.get("action", "")).lower(), 0):
            dedup[k] = r
    dedup_keys = set(dedup.keys())

    out = []
    base_capital = SUITE_CONFIG[suite]["base_capital"]
    for h in HORIZONS:
        eval_docs = db.signal_quality_shadow_eval.find(
            {"run_id": {"$in": run_ids}, "horizon_min": int(h)},
            {"_id": 0, "symbol": 1, "side": 1, "t": 1, "ret_h": 1},
        )
        eval_map = {}
        for e in eval_docs:
            try:
                k = (
                    str(e.get("symbol") or "").strip(),
                    str(e.get("side") or "").strip().upper(),
                    str(e.get("t") or ""),
                )
                if k in dedup_keys:
                    eval_map[k] = float(e.get("ret_h"))
            except Exception:
                pass
        net_stats = _gross_stats_from_eval(dedup, eval_map, suite)
        pnl = _compute_pnl(run_ids, from_iso, now, int(h), suite)
        combined_equity = _safe_float(pnl["combined"]["equity_end"])
        combined_buffer = _safe_float(pnl["combined"]["cash_buffer_end"])
        combined_total = combined_equity + combined_buffer
        out.append(
            {
                "day": 1 if h <= 1440 else int((h + 1439) // 1440),
                "h": h,
                "eval": int(net_stats["eval"]),
                "wr": float(net_stats["wr"]),
                "pf_raw": float(net_stats["pf_raw"]),
                "gross_profit": float(net_stats["gross_profit"]),
                "gross_loss": float(net_stats["gross_loss"]),
                "kraken_equity": _safe_float(pnl["kraken"]["equity_end"]),
                "kraken_buffer": _safe_float(pnl["kraken"]["cash_buffer_end"]),
                "binance_equity": _safe_float(pnl["binance"]["equity_end"]),
                "binance_buffer": _safe_float(pnl["binance"]["cash_buffer_end"]),
                "ibkr_equity": _safe_float(pnl["ibkr"]["equity_end"]),
                "ibkr_buffer": _safe_float(pnl["ibkr"]["cash_buffer_end"]),
                "combined_equity": combined_equity,
                "combined_buffer": combined_buffer,
                "combined_total": combined_total,
                "combined_pnl_vs_base": combined_total - base_capital,
                "fees": _safe_float(pnl["combined"]["fees_paid_total"]),
            }
        )

    meta = {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "latest_run_id": latest_run_id,
        "run_ids": run_ids,
        "window_from": from_iso.isoformat(),
        "window_to": now.isoformat(),
        "rows_total": len(rows),
        "rows_dedup": len(dedup_keys),
        "shadow_count": len(shadow),
        "policy_count": len(policy),
        "executed_count": len(executed),
        "suite": suite,
        "title": SUITE_CONFIG[suite]["title"],
        "base_capital": base_capital,
        "capital_suffix": SUITE_CONFIG[suite]["capital_suffix"],
        "output_dir": str(output_dir),
    }
    return out, meta


def write_report(rows: list[dict], meta: dict, from_iso: datetime, to_iso: datetime) -> list[Path]:
    output_dir = Path(meta["output_dir"]).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    generated = []
    generated.append(_write_md_report(rows, meta, output_dir, meta["title"]))
    generated.extend(_write_special_reports(rows, meta, output_dir, output_dir))
    return generated


def run_once(run_ids: list[str] | None, from_iso: str | None, api_base: str, run_dir: str | None, output_dir: str | None, suite: str | None):
    run_dir_path = Path(run_dir).expanduser() if run_dir else None
    output_dir_path = Path(output_dir).expanduser() if output_dir else DEFAULT_OUTPUT_DIR
    resolved_suite = _infer_suite(suite, output_dir_path)
    f_iso = _resolve_from_iso(from_iso, run_dir_path)
    resolved_run_ids = _resolve_suite_run_ids(api_base, f_iso, run_dir_path, run_ids)
    latest_run_id = _resolve_latest_run_id(api_base)
    now = datetime.now(timezone.utc)
    rows, meta = build_report(resolved_run_ids, f_iso, resolved_suite, output_dir_path, latest_run_id)
    generated = write_report(rows, meta, f_iso, now)
    for path in generated:
        print(f"OK {path}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-ids", default=None)
    ap.add_argument("--from-iso", default=None)
    ap.add_argument("--api-base", default="http://127.0.0.1:8010")
    ap.add_argument("--run-dir", default=None)
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--state-dir", default=None)
    ap.add_argument("--suite", default=None)
    args = ap.parse_args()

    run_ids = None
    if args.run_ids:
        run_ids = [x.strip() for x in str(args.run_ids).split(",") if x.strip()]
    run_once(run_ids, args.from_iso, args.api_base, args.run_dir, args.output_dir, args.suite)


if __name__ == "__main__":
    main()
