import sys
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parent))
from trading.mongo import get_db


db = get_db()
symbols = ["EURUSD", "GBPUSD", "USDJPY", "XAUUSD", "XAGUSD", "CL"]
tf = 60


for sym in symbols:
    docs = list(db.market_candles.find({"symbol": sym, "tf": tf}).sort("t", 1))
    buckets = {}
    for doc in docs:
        t_str = str(doc["t"]).replace("Z", "+00:00")
        t = datetime.fromisoformat(t_str).astimezone(timezone.utc)
        key = t.replace(minute=0, second=0, microsecond=0).isoformat()
        buckets.setdefault(key, []).append(doc)

    dupes = {k: v for k, v in buckets.items() if len(v) > 1}
    removed = 0
    for _, bucket_docs in dupes.items():
        keep = sorted(bucket_docs, key=lambda d: str(d["t"]))[-1]
        ids_to_remove = [d["_id"] for d in bucket_docs if d["_id"] != keep["_id"]]
        if ids_to_remove:
            result = db.market_candles.delete_many({"_id": {"$in": ids_to_remove}})
            removed += int(result.deleted_count)

    print(f"{sym}: {len(docs)} záznamů, {len(dupes)} duplik. bucketů, odstraněno {removed}")

print("Hotovo.")
