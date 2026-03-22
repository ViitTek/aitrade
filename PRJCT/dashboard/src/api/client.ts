const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "http://localhost:8010";
const BACKTEST_BASE = (import.meta.env.VITE_BACKTEST_API_BASE as string | undefined) ?? BASE;
const API_TIMEOUT_MS = 8000;
const API_TIMEOUT_POST_MS = 20000;

type ApiRequestInit = RequestInit & { timeoutMs?: number };

function buildUrl(base: string, path: string): string {
  if (!base) return path;
  if (base.endsWith("/") && path.startsWith("/")) return `${base.slice(0, -1)}${path}`;
  if (!base.endsWith("/") && !path.startsWith("/")) return `${base}/${path}`;
  return `${base}${path}`;
}

export async function api<T>(path: string, init?: ApiRequestInit): Promise<T> {
  const timeoutMs =
    init?.timeoutMs ??
    ((init?.method ?? "GET").toUpperCase() === "POST" ? API_TIMEOUT_POST_MS : API_TIMEOUT_MS);
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(new Error(`Request timeout after ${timeoutMs}ms`)), timeoutMs);
  const { timeoutMs: _timeoutMs, ...requestInit } = init ?? {};
  let res: Response;
  try {
    res = await fetch(buildUrl(BASE, path), {
      headers: { "Content-Type": "application/json" },
      ...requestInit,
      signal: requestInit?.signal ?? controller.signal,
    });
  } catch (e) {
    clearTimeout(t);
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(`Request timeout (${timeoutMs}ms)`);
    }
    throw e;
  }
  clearTimeout(t);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export async function backtestApi<T>(path: string, init?: ApiRequestInit): Promise<T> {
  const timeoutMs =
    init?.timeoutMs ??
    ((init?.method ?? "GET").toUpperCase() === "POST" ? API_TIMEOUT_POST_MS : API_TIMEOUT_MS);
  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(new Error(`Request timeout after ${timeoutMs}ms`)), timeoutMs);
  const { timeoutMs: _timeoutMs, ...requestInit } = init ?? {};
  let res: Response;
  try {
    res = await fetch(buildUrl(BACKTEST_BASE, path), {
      headers: { "Content-Type": "application/json" },
      ...requestInit,
      signal: requestInit?.signal ?? controller.signal,
    });
  } catch (e) {
    clearTimeout(t);
    if (e instanceof DOMException && e.name === "AbortError") {
      throw new Error(`Request timeout (${timeoutMs}ms)`);
    }
    throw e;
  }
  clearTimeout(t);
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText);
    throw new Error(`${res.status}: ${text}`);
  }
  return res.json();
}

export function getBacktestApiBase(): string {
  return BACKTEST_BASE || "(same as main API)";
}
