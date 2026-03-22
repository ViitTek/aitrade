import argparse
import csv
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading.config import settings  # type: ignore
from trading.mongo import get_db  # type: ignore


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "RPRTS" / "_shadow-reports" / "weekly"
HORIZONS = [15, 30, 45] + list(range(60, 10081, 60))
PRIO = {"policy": 1, "shadow": 2, "executed": 3}
BENCHMARK_HORIZONS = [30, 720, 960, 1440, 3600]

ACCOUNT_BASES = {
    "kraken": {"BTC", "ETH"},
    "binance": {"SOL", "BNB", "XRP", "DOGE", "TRX", "USDC"},
    "ibkr": {"PAXG", "EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "CL"},
}


def parse_iso_utc(value: str) -> datetime:
    s = str(value or "").strip()
    if not s:
        return datetime.now(timezone.utc)
    if "." in s:
        m = re.match(r"^(.*?\.)(\d+)([+-].*|Z)?$", s)
        if m:
            frac = m.group(2)[:6]
            suffix = m.group(3) or ""
            s = f"{m.group(1)}{frac}{suffix}"
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def norm_t(value: str) -> str:
    return parse_iso_utc(value).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify_account(symbol: str) -> str:
    base = str(symbol or "").strip().upper()
    base = base.split("/")[0] if "/" in base else base
    if base in ACCOUNT_BASES["kraken"]:
        return "kraken"
    if base in ACCOUNT_BASES["ibkr"]:
        return "ibkr"
    return "binance"


def safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def hour_value(minutes: int) -> str:
    hours = float(minutes) / 60.0
    if abs(hours - round(hours)) < 1e-9:
        return str(int(round(hours)))
    return f"{hours:.2f}"


def resolve_run_ids(run_dir: Path, from_iso: datetime) -> tuple[list[str], dict]:
    state_path = run_dir / "state.json"
    metrics_path = run_dir / "metrics.jsonl"
    extra_run_ids_path = run_dir / "extra_run_ids.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8-sig"))
        except Exception:
            state = {}

    run_ids: list[str] = []
    latest_seen = ""
    samples = 0
    health_ok = 0

    if metrics_path.exists():
        for line in metrics_path.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            samples += 1
            if str(row.get("health") or "").lower() == "ok":
                health_ok += 1
            try:
                ts = parse_iso_utc(str(row.get("t") or ""))
            except Exception:
                continue
            if ts < from_iso:
                continue
            status = row.get("status") or {}
            rid = str(status.get("run_id") or "").strip()
            if rid:
                latest_seen = rid
                if rid not in run_ids:
                    run_ids.append(rid)

    extra_run_ids = []
    if extra_run_ids_path.exists():
        try:
            extra_doc = json.loads(extra_run_ids_path.read_text(encoding="utf-8-sig"))
            if isinstance(extra_doc, dict):
                extra_run_ids = [str(x).strip() for x in extra_doc.get("run_ids", []) if str(x).strip()]
            elif isinstance(extra_doc, list):
                extra_run_ids = [str(x).strip() for x in extra_doc if str(x).strip()]
        except Exception:
            extra_run_ids = []
    for rid in extra_run_ids:
        if rid not in run_ids:
            run_ids.append(rid)

    info = {
        "run_dir": str(run_dir),
        "started_at": str(state.get("started_at") or ""),
        "completed": bool(state.get("completed")) if "completed" in state else False,
        "next_tick": int(state.get("next_tick") or 0) if str(state.get("next_tick") or "").strip() else 0,
        "samples": samples,
        "health_ok": health_ok,
        "health_ratio": round((health_ok / samples), 4) if samples else 0.0,
        "latest_seen_run_id": latest_seen,
        "extra_run_ids": extra_run_ids,
    }
    return run_ids, info


def load_signals_and_dedup(db, run_ids: list[str], from_iso: datetime, to_iso: datetime) -> tuple[list[dict], dict, dict]:
    query = {
        "run_id": {"$in": run_ids},
        "action": {"$in": ["shadow", "policy", "executed"]},
        "t": {"$gte": from_iso.isoformat(), "$lte": to_iso.isoformat()},
    }
    rows = list(
        db.bot_signals.find(
            query,
            {"_id": 0, "symbol": 1, "side": 1, "t": 1, "action": 1, "run_id": 1},
        ).sort("t", 1)
    )
    counts = {"shadow": 0, "policy": 0, "executed": 0}
    dedup: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        action = str(row.get("action") or "").lower()
        if action not in PRIO:
            continue
        counts[action] += 1
        key = (
            str(row.get("symbol") or "").strip(),
            str(row.get("side") or "").strip().upper(),
            norm_t(str(row.get("t") or "")),
        )
        if not key[0] or key[1] not in {"BUY", "SELL"}:
            continue
        prev = dedup.get(key)
        if prev is None or PRIO[action] > PRIO.get(str(prev.get("action") or "").lower(), 0):
            dedup[key] = row
    return rows, dedup, counts


def gross_stats_from_eval(dedup: dict, eval_map: dict) -> dict:
    gross_profit = 0.0
    gross_loss = 0.0
    trade_count = 0
    win_count = 0

    for key in sorted(dedup.keys(), key=lambda item: item[2]):
        ret = eval_map.get(key)
        if ret is None:
            continue
        trade_count += 1
        if ret > 0:
            win_count += 1
            gross_profit += ret
        elif ret < 0:
            gross_loss += abs(ret)

    pf_raw = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
    wr = (win_count / trade_count) if trade_count else 0.0
    return {
        "eval": trade_count,
        "wins": win_count,
        "losses": max(0, trade_count - win_count),
        "wr": wr,
        "pf_raw": pf_raw,
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
    }


def compute_pnl(run_ids: list[str], from_iso: datetime, to_iso: datetime, horizon: int) -> dict:
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
        "100",
        "--binance-eur",
        "100",
        "--ibkr-eur",
        "100",
        "--stake-pct",
        "0.10",
        "--binance-fee-rate",
        "0.001",
        "--kraken-fee-rate",
        "0.0025",
        "--ibkr-fee-rate",
        "0.0",
        "--kraken-bases",
        "BTC,ETH",
        "--binance-bases",
        "SOL,BNB,XRP,DOGE,TRX,USDC",
        "--ibkr-bases",
        "PAXG,EURUSD,GBPUSD,USDJPY,XAUUSD,XAGUSD,CL",
    ]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def build_rows(db, run_ids: list[str], from_iso: datetime, to_iso: datetime) -> tuple[list[dict], dict]:
    raw_rows, dedup, counts = load_signals_and_dedup(db, run_ids, from_iso, to_iso)
    dedup_keys = set(dedup.keys())
    rows: list[dict] = []

    for horizon in HORIZONS:
        eval_docs = db.signal_quality_shadow_eval.find(
            {
                "run_id": {"$in": run_ids},
                "horizon_min": int(horizon),
                "t": {"$gte": from_iso.isoformat(), "$lte": to_iso.isoformat()},
            },
            {"_id": 0, "symbol": 1, "side": 1, "t": 1, "ret_h": 1},
        )
        eval_map: dict[tuple[str, str, str], float] = {}
        for doc in eval_docs:
            try:
                key = (
                    str(doc.get("symbol") or "").strip(),
                    str(doc.get("side") or "").strip().upper(),
                    norm_t(str(doc.get("t") or "")),
                )
                if key in dedup_keys:
                    eval_map[key] = float(doc.get("ret_h"))
            except Exception:
                continue

        stats = gross_stats_from_eval(dedup, eval_map)
        pnl = compute_pnl(run_ids, from_iso, to_iso, int(horizon))
        combined_equity = safe_float(pnl["combined"]["equity_end"])
        combined_buffer = safe_float(pnl["combined"]["cash_buffer_end"])
        rows.append(
            {
                "day": 1 if horizon <= 1440 else int((horizon + 1439) // 1440),
                "h": int(horizon),
                "eval": int(stats["eval"]),
                "wins": int(stats["wins"]),
                "losses": int(stats["losses"]),
                "wr": float(stats["wr"]),
                "pf_raw": float(stats["pf_raw"]),
                "gross_profit": float(stats["gross_profit"]),
                "gross_loss": float(stats["gross_loss"]),
                "kraken_equity": safe_float(pnl["kraken"]["equity_end"]),
                "kraken_buffer": safe_float(pnl["kraken"]["cash_buffer_end"]),
                "kraken_fees": safe_float(pnl["kraken"]["fees_paid_total"]),
                "binance_equity": safe_float(pnl["binance"]["equity_end"]),
                "binance_buffer": safe_float(pnl["binance"]["cash_buffer_end"]),
                "binance_fees": safe_float(pnl["binance"]["fees_paid_total"]),
                "ibkr_equity": safe_float(pnl["ibkr"]["equity_end"]),
                "ibkr_buffer": safe_float(pnl["ibkr"]["cash_buffer_end"]),
                "ibkr_fees": safe_float(pnl["ibkr"]["fees_paid_total"]),
                "combined_equity": combined_equity,
                "combined_buffer": combined_buffer,
                "combined_total": combined_equity + combined_buffer,
                "combined_pnl_vs_base": (combined_equity + combined_buffer) - 300.0,
                "fees": safe_float(pnl["combined"]["fees_paid_total"]),
                "kraken_trades": int(pnl["kraken"]["trades"]),
                "binance_trades": int(pnl["binance"]["trades"]),
                "ibkr_trades": int(pnl["ibkr"]["trades"]),
            }
        )

    meta = {
        "rows_total": len(raw_rows),
        "rows_dedup": len(dedup_keys),
        "shadow_count": counts["shadow"],
        "policy_count": counts["policy"],
        "executed_count": counts["executed"],
        "profit_split_reinvest": float(settings.PROFIT_SPLIT_REINVEST),
    }
    return rows, meta


def rank_rows(rows: list[dict], min_eval: int) -> list[dict]:
    ranked = [row for row in rows if int(row["eval"]) >= min_eval]
    ranked.sort(key=lambda row: (float(row["combined_total"]), float(row["pf_raw"]), int(row["eval"])), reverse=True)
    return ranked


def format_num(value: float, digits: int = 4) -> str:
    return f"{float(value):.{digits}f}"


def table_lines(headers: list[str], body: list[list[str]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---:" for _ in headers) + "|"]
    for row in body:
        lines.append("| " + " | ".join(row) + " |")
    return lines


def rows_to_csv(path: Path, rows: list[dict]) -> None:
    headers = [
        "day", "h_min", "h_hours", "eval", "wins", "losses", "wr", "pf_raw",
        "kraken_eq", "kraken_buf", "binance_eq", "binance_buf", "ibkr_eq", "ibkr_buf",
        "combined_eq", "combined_buf", "combined_total", "combined_pnl_vs_300", "fees",
        "kraken_trades", "binance_trades", "ibkr_trades",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        for row in rows:
            writer.writerow([
                row["day"], row["h"], hour_value(int(row["h"])), row["eval"], row["wins"], row["losses"],
                format_num(row["wr"]), format_num(row["pf_raw"]),
                format_num(row["kraken_equity"]), format_num(row["kraken_buffer"]),
                format_num(row["binance_equity"]), format_num(row["binance_buffer"]),
                format_num(row["ibkr_equity"]), format_num(row["ibkr_buffer"]),
                format_num(row["combined_equity"]), format_num(row["combined_buffer"]),
                format_num(row["combined_total"]), format_num(row["combined_pnl_vs_base"]),
                format_num(row["fees"]), row["kraken_trades"], row["binance_trades"], row["ibkr_trades"],
            ])


def write_report(output_dir: Path, label: str, kind: str, run_info: dict, rows: list[dict], meta: dict) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    base = output_dir / f"{kind}_weekly_shadow_{label}_{stamp}"
    json_path = base.with_suffix(".json")
    csv_path = base.with_suffix(".csv")
    md_path = base.with_suffix(".md")

    ranked_any = rank_rows(rows, 1)[:10]
    ranked_20 = rank_rows(rows, 20)[:10]
    ranked_50 = rank_rows(rows, 50)[:10]
    benchmark_map = {int(row["h"]): row for row in rows}

    payload = {
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z"),
        "kind": kind,
        "label": label,
        "run_info": run_info,
        "meta": meta,
        "top_any": ranked_any,
        "top_eval_ge20": ranked_20,
        "top_eval_ge50": ranked_50,
        "benchmark_horizons": [benchmark_map[h] for h in BENCHMARK_HORIZONS if h in benchmark_map],
        "rows": rows,
    }
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    rows_to_csv(csv_path, rows)

    md: list[str] = []
    md.append(f"# Weekly Shadow {kind.title()} Report")
    md.append("")
    md.append(f"Generated: {payload['generated_at']}")
    md.append(f"Label: {label}")
    md.append(f"Window: {run_info['window_from']} -> {run_info['window_to']}")
    md.append(f"Run IDs: {', '.join(run_info['all_run_ids'])}")
    md.append(f"Signals raw/dedup: {meta['rows_total']} / {meta['rows_dedup']}")
    md.append(
        f"Actions: shadow={meta['shadow_count']}, policy={meta['policy_count']}, executed={meta['executed_count']}"
    )
    md.append(f"Profit split reinvest: {meta['profit_split_reinvest']}")
    md.append("")
    md.append("## Suite Health")
    md.append("")
    health_headers = ["suite", "started_at", "completed", "samples", "health_ok", "health_ratio", "latest_run_id"]
    health_rows = []
    for suite_name in ("bin_krak", "ibkr"):
        info = run_info[suite_name]
        health_rows.append([
            suite_name,
            info.get("started_at") or "-",
            str(info.get("completed", False)),
            str(info.get("samples", 0)),
            str(info.get("health_ok", 0)),
            format_num(info.get("health_ratio", 0.0)),
            info.get("latest_seen_run_id") or "-",
        ])
    md.extend(table_lines(health_headers, health_rows))
    md.append("")

    def add_ranked_section(title: str, ranked: list[dict]) -> None:
        md.append(f"## {title}")
        md.append("")
        headers = ["h", "eval", "WR", "PF_raw", "K_total", "B_total", "I_total", "C_total", "Fees"]
        body = []
        for row in ranked:
            body.append([
                hour_value(int(row["h"])),
                str(row["eval"]),
                format_num(row["wr"]),
                format_num(row["pf_raw"]),
                format_num(float(row["kraken_equity"]) + float(row["kraken_buffer"])),
                format_num(float(row["binance_equity"]) + float(row["binance_buffer"])),
                format_num(float(row["ibkr_equity"]) + float(row["ibkr_buffer"])),
                format_num(row["combined_total"]),
                format_num(row["fees"]),
            ])
        if body:
            md.extend(table_lines(headers, body))
        else:
            md.append("No rows for this filter.")
        md.append("")

    add_ranked_section("Top Horizons Any Eval", ranked_any)
    add_ranked_section("Top Horizons Eval >= 20", ranked_20)
    add_ranked_section("Top Horizons Eval >= 50", ranked_50)

    md.append("## Benchmark Horizons")
    md.append("")
    benchmark_headers = [
        "h", "eval", "wins", "losses", "WR", "PF_raw",
        "Kraken_total", "Binance_total", "IBKR_total", "Combined_total", "Combined_buffer",
    ]
    benchmark_rows = []
    for horizon in BENCHMARK_HORIZONS:
        row = benchmark_map.get(horizon)
        if not row:
            continue
        benchmark_rows.append([
            hour_value(int(row["h"])),
            str(row["eval"]),
            str(row["wins"]),
            str(row["losses"]),
            format_num(row["wr"]),
            format_num(row["pf_raw"]),
            format_num(float(row["kraken_equity"]) + float(row["kraken_buffer"])),
            format_num(float(row["binance_equity"]) + float(row["binance_buffer"])),
            format_num(float(row["ibkr_equity"]) + float(row["ibkr_buffer"])),
            format_num(row["combined_total"]),
            format_num(row["combined_buffer"]),
        ])
    md.extend(table_lines(benchmark_headers, benchmark_rows))
    md.append("")

    if ranked_20:
        best = ranked_20[0]
        md.append("## Recommendation Snapshot")
        md.append("")
        md.append(
            f"- Current robust leader: `h={hour_value(int(best['h']))} h` with `eval={best['eval']}`, `WR={format_num(best['wr'])}`, `PF_raw={format_num(best['pf_raw'])}`, `combined_total={format_num(best['combined_total'])}`."
        )
        md.append(
            f"- Account state at leader: Kraken `{format_num(float(best['kraken_equity']) + float(best['kraken_buffer']))}`, Binance `{format_num(float(best['binance_equity']) + float(best['binance_buffer']))}`, IBKR `{format_num(float(best['ibkr_equity']) + float(best['ibkr_buffer']))}`."
        )
        md.append(
            f"- Buffer at leader: Kraken `{format_num(best['kraken_buffer'])}`, Binance `{format_num(best['binance_buffer'])}`, IBKR `{format_num(best['ibkr_buffer'])}`, combined `{format_num(best['combined_buffer'])}`."
        )
        md.append("")

    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
    return [md_path, csv_path, json_path]


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate combined weekly shadow report across Binance/Kraken and IBKR suites")
    ap.add_argument("--main-run-dir", required=True)
    ap.add_argument("--ibkr-run-dir", required=True)
    ap.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    ap.add_argument("--label", default="weekly")
    ap.add_argument("--kind", choices=["daily", "final", "manual"], default="manual")
    ap.add_argument("--from-iso", default=None)
    ap.add_argument("--to-iso", default=None)
    args = ap.parse_args()

    main_run_dir = Path(args.main_run_dir).expanduser()
    ibkr_run_dir = Path(args.ibkr_run_dir).expanduser()
    output_dir = Path(args.output_dir).expanduser()

    main_run_ids, main_info = resolve_run_ids(main_run_dir, datetime.min.replace(tzinfo=timezone.utc))
    ibkr_run_ids, ibkr_info = resolve_run_ids(ibkr_run_dir, datetime.min.replace(tzinfo=timezone.utc))

    state_starts = []
    for item in (main_info, ibkr_info):
        if item.get("started_at"):
            try:
                state_starts.append(parse_iso_utc(item["started_at"]))
            except Exception:
                pass
    from_iso = parse_iso_utc(args.from_iso) if args.from_iso else (min(state_starts) if state_starts else datetime.now(timezone.utc))
    to_iso = parse_iso_utc(args.to_iso) if args.to_iso else datetime.now(timezone.utc)

    main_run_ids, main_info = resolve_run_ids(main_run_dir, from_iso)
    ibkr_run_ids, ibkr_info = resolve_run_ids(ibkr_run_dir, from_iso)
    all_run_ids = []
    for rid in main_run_ids + ibkr_run_ids:
        if rid and rid not in all_run_ids:
            all_run_ids.append(rid)

    if not all_run_ids:
        raise SystemExit("No run ids found in provided run dirs")

    db = get_db()
    rows, meta = build_rows(db, all_run_ids, from_iso, to_iso)

    run_info = {
        "window_from": from_iso.isoformat(),
        "window_to": to_iso.isoformat(),
        "all_run_ids": all_run_ids,
        "bin_krak": main_info,
        "ibkr": ibkr_info,
    }
    written = write_report(output_dir, args.label, args.kind, run_info, rows, meta)
    print(json.dumps({"ok": True, "written": [str(path) for path in written]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
