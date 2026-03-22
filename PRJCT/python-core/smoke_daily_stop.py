"""
Smoke test for PaperExecutor DAILY_STOP enforcement.

Usage:
    python smoke_daily_stop.py
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable

from trading.paper import PaperExecutor


class SimpleCursor:
    def __init__(self, docs: Iterable[dict]):
        self.docs = [dict(doc) for doc in docs]

    def sort(self, key, direction=None):
        if isinstance(key, list):
            for item in reversed(key):
                if not isinstance(item, (tuple, list)) or len(item) != 2:
                    continue
                field, item_direction = item
                reverse = int(item_direction) < 0
                self.docs.sort(key=lambda d: d.get(field), reverse=reverse)
            return self
        reverse = int(direction or 1) < 0
        self.docs.sort(key=lambda d: d.get(key), reverse=reverse)
        return self

    def limit(self, value: int):
        self.docs = self.docs[: max(0, int(value))]
        return self

    def __iter__(self):
        return iter(self.docs)


class SimpleCollection:
    def __init__(self):
        self.docs = []

    def update_one(self, filt, upd, upsert=False):
        doc = self.find_one(filt)
        if doc is None:
            if not upsert:
                return
            doc = dict(filt)
            self.docs.append(doc)
        if "$setOnInsert" in upd and doc == dict(filt):
            for k, v in upd["$setOnInsert"].items():
                doc.setdefault(k, v)
        if "$set" in upd:
            doc.update(upd["$set"])

    def find_one(self, filt, _proj=None, sort=None):
        results = list(self.find(filt, _proj))
        if sort:
            results = list(SimpleCursor(results).sort(sort))
        return dict(results[0]) if results else None

    def insert_one(self, doc):
        self.docs.append(dict(doc))

    def find(self, filt, _proj=None):
        out = []
        for d in self.docs:
            matched = True
            for k, v in filt.items():
                current = d.get(k)
                if isinstance(v, dict) and "$not" in v:
                    not_filter = v["$not"]
                    if isinstance(not_filter, dict) and "$regex" in not_filter:
                        pattern = str(not_filter["$regex"])
                        if pattern.startswith("^") and str(current or "").startswith(pattern[1:]):
                            matched = False
                            break
                        continue
                if current != v:
                    matched = False
                    break
            if matched:
                out.append(dict(d))
        return SimpleCursor(out)


@dataclass
class FakeDB:
    portfolio: SimpleCollection
    positions: SimpleCollection
    trades: SimpleCollection


async def run() -> int:
    db = FakeDB(SimpleCollection(), SimpleCollection(), SimpleCollection())
    ex = PaperExecutor(db, run_id="test-run")
    ex.daily_stop = 10.0

    t = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    day = t[:10]

    # Simulate day start equity = 1000 and current equity already down to 989.
    ex._day_state[day] = {"start_equity": 1000.0, "stopped": False}
    ex._set_portfolio(989.0, 0.0)

    opened = await ex.on_signal(
        symbol="BTC/USDT",
        tf=5,
        t=t,
        close=50000.0,
        side="BUY",
        reason="smoke",
    )

    if opened:
        print("[FAIL] Signal was executed although DAILY_STOP should block it.")
        return 1
    if not ex._day_state[day]["stopped"]:
        print("[FAIL] Day state was not switched to stopped=True.")
        return 1
    if db.positions.find_one({"run_id": "test-run", "status": "OPEN"}) is not None:
        print("[FAIL] OPEN position exists despite daily stop.")
        return 1

    print("[PASS] DAILY_STOP blocks new signals after daily loss threshold.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
