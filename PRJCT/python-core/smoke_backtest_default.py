"""
Smoke test for /bot/backtest default behavior when "symbol" is omitted.

Expected behavior:
- backend default symbol is ALL
- response is multi-backtest payload with "multi": true

Usage:
    python smoke_backtest_default.py
    python smoke_backtest_default.py --base-url http://127.0.0.1:8000
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from typing import Any

import requests


def build_payload() -> dict[str, Any]:
    # Intentionally omit "symbol" to verify API default = ALL.
    return {
        "source": "mongo",
        "dt_from": "2026-01-01",
        "dt_to": dt.date.today().isoformat(),
        "interval": 60,
        "with_sentiment": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test /bot/backtest default symbol=ALL")
    parser.add_argument("--base-url", default="http://localhost:8000", help="FastAPI base URL")
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds")
    args = parser.parse_args()

    url = f"{args.base_url.rstrip('/')}/bot/backtest"
    payload = build_payload()

    print(f"[SMOKE] POST {url}")
    print(f"[SMOKE] payload: {json.dumps(payload)}")

    try:
        r = requests.post(url, json=payload, timeout=args.timeout)
    except Exception as e:
        print(f"[FAIL] Request failed: {e}")
        return 1

    if r.status_code != 200:
        print(f"[FAIL] HTTP {r.status_code}: {r.text[:800]}")
        return 1

    try:
        body = r.json()
    except Exception as e:
        print(f"[FAIL] Invalid JSON response: {e}")
        print(r.text[:800])
        return 1

    if not body.get("ok"):
        print(f"[FAIL] Response ok=false: {body}")
        return 1

    if not body.get("multi"):
        print(f"[FAIL] Expected multi=true, got: {body.get('multi')}")
        return 1

    if "results" not in body or "summary" not in body:
        print("[FAIL] Expected multi payload keys: results + summary")
        return 1

    print("[PASS] /bot/backtest default symbol behavior is correct (multi=true).")
    print(
        "[PASS] Summary:",
        json.dumps(
            {
                "symbols": body.get("summary", {}).get("symbols"),
                "total_trades": body.get("summary", {}).get("total_trades"),
                "total_pnl": body.get("summary", {}).get("total_pnl"),
            }
        ),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

