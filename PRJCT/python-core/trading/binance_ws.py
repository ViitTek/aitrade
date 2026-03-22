# trading/binance_ws.py
import asyncio
import json
from datetime import datetime, timezone
import websockets
from trading.config import settings

# Mapování intervalů na Binance formát
INTERVAL_MAP = {1: "1m", 5: "5m", 15: "15m", 30: "30m", 60: "1h", 240: "4h", 1440: "1d"}


def _to_binance_symbol(symbol: str) -> str:
    """'PAXG/USDT' → 'paxgusdt'"""
    return symbol.replace("/", "").lower()


def _to_our_symbol(binance_symbol: str, symbols: list[str]) -> str:
    """'PAXGUSDT' → 'PAXG/USDT' (lookup z naší symbol list)"""
    b = binance_symbol.upper()
    for s in symbols:
        if s.replace("/", "").upper() == b:
            return s
    return binance_symbol


class BinanceWS:
    def __init__(self, on_candle, symbols: list[str], interval: int = None):
        self.on_candle = on_candle
        self.symbols = list(symbols)
        self.interval = interval or settings.INTERVAL_MINUTES
        self._task = None
        self._stop = asyncio.Event()
        self._ws = None
        self._sub_id = 1

    async def start(self):
        self._stop.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self):
        self._stop.set()
        if self._task:
            try:
                await self._task
            except Exception:
                pass

    async def subscribe(self, new_symbols: list[str]):
        """Dynamicky přidá nové symboly do existujícího WS připojení."""
        interval_str = INTERVAL_MAP.get(self.interval, f"{self.interval}m")
        to_add = [s for s in new_symbols if s not in self.symbols]
        if not to_add or not self._ws:
            return

        params = [f"{_to_binance_symbol(s)}@kline_{interval_str}" for s in to_add]
        self._sub_id += 1
        msg = {"method": "SUBSCRIBE", "params": params, "id": self._sub_id}
        try:
            await self._ws.send(json.dumps(msg))
            self.symbols.extend(to_add)
            print(f"BINANCE WS: subscribed to {to_add}")
        except Exception as e:
            print(f"BINANCE WS SUBSCRIBE ERROR: {repr(e)}")

    async def unsubscribe(self, remove_symbols: list[str]):
        """Dynamicky odebere symboly z existujícího WS připojení."""
        interval_str = INTERVAL_MAP.get(self.interval, f"{self.interval}m")
        to_remove = [s for s in remove_symbols if s in self.symbols]
        if not to_remove or not self._ws:
            return

        params = [f"{_to_binance_symbol(s)}@kline_{interval_str}" for s in to_remove]
        self._sub_id += 1
        msg = {"method": "UNSUBSCRIBE", "params": params, "id": self._sub_id}
        try:
            await self._ws.send(json.dumps(msg))
            for s in to_remove:
                self.symbols.remove(s)
            print(f"BINANCE WS: unsubscribed from {to_remove}")
        except Exception as e:
            print(f"BINANCE WS UNSUBSCRIBE ERROR: {repr(e)}")

    async def _run(self):
        interval_str = INTERVAL_MAP.get(self.interval, f"{self.interval}m")

        # Binance combined streams URL
        streams = [f"{_to_binance_symbol(s)}@kline_{interval_str}" for s in self.symbols]
        url = f"{settings.BINANCE_WS_URL}/stream?streams={'/'.join(streams)}"

        while not self._stop.is_set():
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    self._ws = ws
                    print(f"BINANCE WS: connected, streams={streams}")

                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        except asyncio.TimeoutError:
                            continue  # no message in 120s, just loop back
                        msg = json.loads(raw)

                        # Combined stream format: {"stream": "...", "data": {...}}
                        data = msg.get("data", msg)

                        if data.get("e") != "kline":
                            continue

                        k = data["k"]
                        symbol = _to_our_symbol(k["s"], self.symbols)
                        t = datetime.fromtimestamp(k["t"] / 1000, tz=timezone.utc).isoformat()

                        item = {
                            "symbol": symbol,
                            "interval_begin": t,
                            "open": k["o"],
                            "high": k["h"],
                            "low": k["l"],
                            "close": k["c"],
                            "volume": k["v"],
                        }
                        await self.on_candle(symbol, self.interval, item)

            except Exception as e:
                print(f"BINANCE WS ERROR: {repr(e)}")
                self._ws = None
                await asyncio.sleep(5)
