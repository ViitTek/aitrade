# trading/kraken_ws.py
import asyncio
import json
import websockets
from trading.config import settings


class KrakenWS:
    def __init__(self, on_candle, interval: int = None, symbols: list[str] = None):
        self.on_candle = on_candle
        self.interval = interval or settings.INTERVAL_MINUTES
        self.symbols = list(symbols) if symbols else [s.strip() for s in settings.SYMBOLS.split(",") if s.strip()]
        self._task = None
        self._stop = asyncio.Event()

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

    async def _run(self):
        sub_msg = {
            "method": "subscribe",
            "params": {
                "channel": "ohlc",
                "symbol": self.symbols,
                "interval": self.interval
            }
        }

        while not self._stop.is_set():
            try:
                async with websockets.connect(
                    settings.KRAKEN_WS_URL,
                    ping_interval=20,
                    ping_timeout=20
                ) as ws:
                    await ws.send(json.dumps(sub_msg))
                    print("KRAKEN WS: subscribed", self.symbols, "interval", self.interval)

                    while not self._stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=120)
                        except asyncio.TimeoutError:
                            continue  # no message in 120s, just loop back
                        msg = json.loads(raw)

                        # log non-data messages (skip heartbeat)
                        if isinstance(msg, dict) and msg.get("channel") not in ("ohlc", "heartbeat", None):
                            print("KRAKEN WS: msg", msg)

                        # data messages
                        if isinstance(msg, dict) and msg.get("channel") == "ohlc" and "data" in msg:
                            for item in msg["data"]:
                                await self.on_candle(item.get("symbol"), self.interval, item)

            except Exception as e:
                print("KRAKEN WS ERROR:", repr(e))
                await asyncio.sleep(5)
