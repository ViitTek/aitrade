from __future__ import annotations

import socket
from typing import Iterable


def _safe_int(value, default: int) -> int:
    try:
        if value is None:
            return int(default)
        return int(value)
    except Exception:
        return int(default)


def normalize_gateway_trading_mode(value, default: str = "paper") -> str:
    raw = str(value or default or "paper").strip().lower()
    if raw in {"live", "l"}:
        return "live"
    return "paper"


def is_local_ibkr_host(host: str | None) -> bool:
    raw = str(host or "127.0.0.1").strip().lower()
    return raw in {"127.0.0.1", "localhost", "::1"}


def get_ibkr_port_candidates(configured_port, trading_mode: str = "paper") -> list[int]:
    configured = _safe_int(configured_port, 0)
    mode = normalize_gateway_trading_mode(trading_mode)
    preferred = [4002, 7497] if mode == "paper" else [4001, 7496]
    fallback = [4001, 7496] if mode == "paper" else [4002, 7497]
    ports: list[int] = []
    for port in [configured, *preferred, *fallback]:
        if port <= 0 or port in ports:
            continue
        ports.append(port)
    if not ports:
        ports.append(4002 if mode == "paper" else 4001)
    return ports


def test_ibkr_tcp_port(host: str, port: int, timeout_sec: float = 1.5) -> bool:
    try:
        with socket.create_connection((host, int(port)), timeout=max(0.25, float(timeout_sec))):
            return True
    except Exception:
        return False


def resolve_listening_ibkr_port(host: str, configured_port, trading_mode: str = "paper", timeout_sec: float = 1.0) -> int:
    host_clean = str(host or "127.0.0.1").strip() or "127.0.0.1"
    ports = get_ibkr_port_candidates(configured_port, trading_mode)
    if not is_local_ibkr_host(host_clean):
        return ports[0]
    for port in ports:
        if test_ibkr_tcp_port(host_clean, port, timeout_sec=timeout_sec):
            return port
    return ports[0]


def connect_ibkr_with_fallback(
    ib,
    host: str,
    configured_port,
    client_id: int,
    readonly: bool = True,
    timeout_sec: int = 8,
    trading_mode: str = "paper",
):
    host_clean = str(host or "127.0.0.1").strip() or "127.0.0.1"
    ports: Iterable[int]
    if is_local_ibkr_host(host_clean):
        ports = get_ibkr_port_candidates(configured_port, trading_mode)
    else:
        ports = [_safe_int(configured_port, 7497)]

    errors: list[str] = []
    for port in ports:
        try:
            ib.connect(
                host_clean,
                int(port),
                clientId=int(client_id),
                readonly=bool(readonly),
                timeout=max(2, int(timeout_sec)),
            )
            return host_clean, int(port)
        except Exception as exc:
            errors.append(f"{port}: {exc}")
            try:
                if ib.isConnected():
                    ib.disconnect()
            except Exception:
                pass

    joined = "; ".join(errors) if errors else "no candidate ports"
    raise RuntimeError(f"IBKR connect failed on {host_clean}: {joined}")
