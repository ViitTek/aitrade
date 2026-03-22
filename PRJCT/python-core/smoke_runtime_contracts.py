"""
Smoke checks for runtime API contracts:
1) /bot/start response consistency when bot is already running.
2) /bot/portfolio MTM identity: daily_pnl_mtm == daily_pnl + daily_unrealized_pnl.
3) /bot/equity-curve include_mtm switch appends exactly one current MTM point.

Usage:
    python smoke_runtime_contracts.py
    python smoke_runtime_contracts.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import math
import sys
from typing import Any

import requests


def req_json(method: str, url: str, timeout: int = 30, **kwargs) -> tuple[int, Any]:
    r = requests.request(method, url, timeout=timeout, **kwargs)
    try:
        body = r.json()
    except Exception:
        body = r.text
    return r.status_code, body


def has_start_shape(payload: dict) -> bool:
    return (
        isinstance(payload, dict)
        and "ok" in payload
        and "running" in payload
        and "run_id" in payload
        and "mode" in payload
        and isinstance(payload.get("workers"), dict)
        and "news_worker" in payload["workers"]
        and "market_intel_worker" in payload["workers"]
        and "binance_feed" in payload["workers"]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke runtime contracts")
    parser.add_argument("--base-url", default="http://localhost:8000", help="FastAPI base URL")
    parser.add_argument("--timeout", type=int, default=30, help="Request timeout seconds")
    args = parser.parse_args()

    base = args.base_url.rstrip("/")

    s1, start1 = req_json("POST", f"{base}/bot/start", timeout=args.timeout)
    if s1 != 200 or not has_start_shape(start1):
        print(f"[FAIL] First /bot/start invalid response: status={s1} body={start1}")
        return 1

    s2, start2 = req_json("POST", f"{base}/bot/start", timeout=args.timeout)
    if s2 != 200 or not has_start_shape(start2):
        print(f"[FAIL] Second /bot/start invalid response: status={s2} body={start2}")
        return 1

    rid = start2.get("run_id")
    if not rid:
        print("[FAIL] Missing run_id from /bot/start")
        return 1

    sp, p = req_json("GET", f"{base}/bot/portfolio?run_id={rid}", timeout=args.timeout)
    if sp != 200 or not isinstance(p, dict):
        print(f"[FAIL] /bot/portfolio failed: status={sp} body={p}")
        return 1
    lhs = float(p.get("daily_pnl_mtm", 0.0))
    rhs = float(p.get("daily_pnl", 0.0)) + float(p.get("daily_unrealized_pnl", 0.0))
    if not math.isclose(lhs, rhs, abs_tol=0.02):
        print(f"[FAIL] MTM identity mismatch: daily_pnl_mtm={lhs} vs daily_pnl+daily_unrealized={rhs}")
        return 1

    sf, curve_false = req_json("GET", f"{base}/bot/equity-curve?run_id={rid}&include_mtm=false", timeout=args.timeout)
    st, curve_true = req_json("GET", f"{base}/bot/equity-curve?run_id={rid}&include_mtm=true", timeout=args.timeout)
    if sf != 200 or st != 200 or not isinstance(curve_false, list) or not isinstance(curve_true, list):
        print(f"[FAIL] /bot/equity-curve failed: false=({sf},{curve_false}) true=({st},{curve_true})")
        return 1
    if len(curve_true) != len(curve_false) + 1:
        print(
            "[FAIL] include_mtm contract failed: "
            f"len(false)={len(curve_false)} len(true)={len(curve_true)} expected +1"
        )
        return 1

    print("[PASS] Runtime contracts are consistent:")
    print("       /bot/start shape stable, portfolio MTM identity valid, include_mtm works.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
