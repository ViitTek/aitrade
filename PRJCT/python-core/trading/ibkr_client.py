from __future__ import annotations

import asyncio
from typing import Any, Dict

from trading.config import settings
from trading.ibkr_connection import connect_ibkr_with_fallback, normalize_gateway_trading_mode


def _safe_int(value, default: int) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def get_ibkr_status(timeout_sec: int = 8) -> Dict[str, Any]:
    host = str(getattr(settings, "IBKR_TWS_HOST", "127.0.0.1") or "127.0.0.1").strip()
    port = _safe_int(getattr(settings, "IBKR_TWS_PORT", 7497), 7497)
    client_id = _safe_int(getattr(settings, "IBKR_CLIENT_ID", 77), 77)
    readonly = bool(getattr(settings, "IBKR_READONLY_API", True))
    trading_mode = normalize_gateway_trading_mode(getattr(settings, "IBKR_GATEWAY_TRADING_MODE", "paper"))

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from ib_insync import IB
    except Exception as e:
        try:
            loop.close()
        except Exception:
            pass
        return {
            "ok": False,
            "connected": False,
            "host": host,
            "port": port,
            "client_id": client_id,
            "readonly": readonly,
            "error": f"ib_insync_import_failed: {e}",
        }

    ib = IB()
    try:
        connected_host, connected_port = connect_ibkr_with_fallback(
            ib,
            host=host,
            configured_port=port,
            client_id=client_id,
            readonly=readonly,
            timeout_sec=timeout_sec,
            trading_mode=trading_mode,
        )
        accounts = ib.managedAccounts() or []
        summary = ib.accountSummary() or []
        net_liq = None
        currency = None
        for row in summary:
            if getattr(row, "tag", "") == "NetLiquidation" and net_liq is None:
                net_liq = getattr(row, "value", None)
                currency = getattr(row, "currency", None)
        return {
            "ok": True,
            "connected": bool(ib.isConnected()),
            "host": connected_host,
            "port": connected_port,
            "configured_port": port,
            "client_id": client_id,
            "readonly": readonly,
            "trading_mode": trading_mode,
            "accounts": accounts,
            "account_summary_items": len(summary),
            "net_liquidation": net_liq,
            "net_liquidation_currency": currency,
        }
    except Exception as e:
        return {
            "ok": False,
            "connected": False,
            "host": host,
            "port": port,
            "configured_port": port,
            "client_id": client_id,
            "readonly": readonly,
            "trading_mode": trading_mode,
            "error": str(e),
        }
    finally:
        try:
            if ib.isConnected():
                ib.disconnect()
        except Exception:
            pass
        try:
            loop.close()
        except Exception:
            pass
