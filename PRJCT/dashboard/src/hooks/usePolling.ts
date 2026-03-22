import { useState, useEffect, useCallback, useRef } from "react";

type CacheEntry = {
  data: unknown;
  error: string | null;
  ts: number;
};

const pollingCache = new Map<string, CacheEntry>();
const pollingInflight = new Map<string, Promise<unknown>>();

export function usePolling<T>(
  fetcher: () => Promise<T>,
  intervalMs = 5000,
  cacheKey?: string
): { data: T | null; error: string | null; refresh: () => void } {
  const [data, setData] = useState<T | null>(() => {
    if (!cacheKey) return null;
    const c = pollingCache.get(cacheKey);
    return (c?.data as T | null) ?? null;
  });
  const [error, setError] = useState<string | null>(() => {
    if (!cacheKey) return null;
    const c = pollingCache.get(cacheKey);
    return c?.error ?? null;
  });
  const inFlightRef = useRef<Promise<T> | null>(null);

  const refresh = useCallback(() => {
    if (cacheKey) {
      const shared = pollingInflight.get(cacheKey) as Promise<T> | undefined;
      if (shared) {
        inFlightRef.current = shared;
        shared
          .then((d) => {
            setData(d);
            setError(null);
          })
          .catch((e) => {
            setError(e?.message ?? "Request failed");
          })
          .finally(() => {
            inFlightRef.current = null;
          });
        return;
      }
    }

    if (inFlightRef.current) return;

    const req = fetcher();
    inFlightRef.current = req;
    if (cacheKey) pollingInflight.set(cacheKey, req as Promise<unknown>);

    req
      .then((d) => {
        setData(d);
        setError(null);
        if (cacheKey) {
          pollingCache.set(cacheKey, { data: d, error: null, ts: Date.now() });
        }
      })
      .catch((e) => {
        const msg = e?.message ?? "Request failed";
        setError(msg);
        if (cacheKey) {
          const prev = pollingCache.get(cacheKey);
          pollingCache.set(cacheKey, {
            data: prev?.data ?? null,
            error: msg,
            ts: Date.now(),
          });
        }
      })
      .finally(() => {
        if (cacheKey) {
          const shared = pollingInflight.get(cacheKey);
          if (shared === req) pollingInflight.delete(cacheKey);
        }
        inFlightRef.current = null;
      });
  }, [fetcher, cacheKey]);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, intervalMs);
    return () => clearInterval(id);
  }, [refresh, intervalMs]);

  useEffect(() => {
    const onGlobalRefresh = () => refresh();
    const onVisibility = () => {
      if (!document.hidden) refresh();
    };
    const onFocus = () => refresh();
    const onOnline = () => refresh();

    window.addEventListener("api:refresh", onGlobalRefresh as EventListener);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("focus", onFocus);
    window.addEventListener("online", onOnline);

    return () => {
      window.removeEventListener("api:refresh", onGlobalRefresh as EventListener);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("online", onOnline);
    };
  }, [refresh]);

  return { data, error, refresh };
}
