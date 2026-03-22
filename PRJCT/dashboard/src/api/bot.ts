import { api } from "./client";
import type {
  BotStatus,
  Portfolio,
  Position,
  EquityPoint,
  BotEvent,
  Signal,
  RunInfo,
} from "../types";

export const getStatus = (timeoutMs = 1500) => api<BotStatus>("/bot/status", { timeoutMs });
export const startBot = () =>
  api<{
    ok: boolean;
    running: boolean;
    run_id: string;
    resumed?: boolean;
    mode: string;
    workers: {
      news_worker: boolean;
      market_intel_worker: boolean;
      binance_feed: boolean;
    };
  }>("/bot/start", { method: "POST" });
export const stopBot = () => api<{ ok: boolean; run_id: string }>("/bot/stop", { method: "POST" });
export const resetPaperAccount = (runId?: string) =>
  api<{ ok: boolean; run_id: string; equity: number; cash_buffer: number; daily_pnl: number }>(
    `/bot/paper/reset-account${runId ? `?run_id=${runId}` : ""}`,
    { method: "POST" }
  );

export const getPortfolio = (runId?: string) =>
  api<Portfolio>(`/bot/portfolio${runId ? `?run_id=${runId}` : ""}`);

export const getOpenPositions = (runId?: string) =>
  api<Position[]>(`/bot/positions/open${runId ? `?run_id=${runId}` : ""}`);

export const getClosedPositions = (runId?: string, limit = 50) =>
  api<Position[]>(`/bot/positions/closed?limit=${limit}${runId ? `&run_id=${runId}` : ""}`);

export const getEquityCurve = (runId?: string, includeMtm = false, allRuntime = false, sinceRestart = false) =>
  api<EquityPoint[]>(
    `/bot/equity-curve?include_mtm=${includeMtm ? "true" : "false"}&all_runtime=${allRuntime ? "true" : "false"}&paper_resets=true&since_restart=${sinceRestart ? "true" : "false"}${runId ? `&run_id=${runId}` : ""}`
  );

export const getEvents = (runId?: string, limit = 50) =>
  api<BotEvent[]>(`/bot/events?limit=${limit}${runId ? `&run_id=${runId}` : ""}`);

export const getSignals = (runId?: string, limit = 50) =>
  api<Signal[]>(`/bot/signals?limit=${limit}${runId ? `&run_id=${runId}` : ""}`);

export interface TradedSymbolsResponse {
  run_id: string | null;
  lookback_hours: number;
  symbols: string[];
  paper_symbols: string[];
  shadow_symbols: string[];
  signal_symbols: string[];
}

export const getTradedSymbols = (runId?: string, lookbackHours = 720) =>
  api<TradedSymbolsResponse>(
    `/bot/traded-symbols?lookback_hours=${lookbackHours}${runId ? `&run_id=${runId}` : ""}`
  );

export interface ShadowHorizonItem {
  horizon_min: number;
  day: number;
  shadow_eval_samples: number;
  shadow_win_rate_h: number | null;
  shadow_profit_factor_h: number | null;
  shadow_avg_ret_h: number | null;
  total: number;
  total_dedup: number;
  shadow: number;
  policy: number;
  executed: number;
  equity_end_eur?: number;
  cash_buffer_end_eur?: number;
  total_end_eur?: number;
  pnl_vs_300_eur?: number;
}

export const getShadowHorizonSummary = (
  runId?: string,
  lookbackHours = 720,
  horizons = "720,1440,2160,2880,3600,4320,5040,5760,6480,7200,7920,8640,9360,10080"
) =>
  api<{
    run_id: string | null;
    lookback_hours: number;
    horizons: number[];
    items: ShadowHorizonItem[];
  }>(
    `/bot/shadow-horizon-summary?lookback_hours=${lookbackHours}&horizons=${encodeURIComponent(horizons)}${runId ? `&run_id=${runId}` : ""}`
  );

export const getRuns = () => api<RunInfo[]>("/bot/runs");

export const getRecommendations = () =>
  api<{
    symbols: string[];
    details: Record<string, { outlook?: string; reason?: string; candle_count?: number; ready?: boolean }>;
    created_at: string | null;
    overall: string;
    always_active: string[];
    llm_ok?: boolean | null;
    degraded?: boolean;
    last_error?: string | null;
  }>("/bot/recommendations");

export const getFunding = (symbol?: string) =>
  api<{
    latest: { funding_rate: number; open_interest: number; open_interest_usdt: number; mark_price: number; timestamp: string } | null;
    history: { funding_rate: number; open_interest: number; timestamp: string }[];
  }>(`/bot/funding${symbol ? `?symbol=${symbol}` : ""}`);

export interface MarketMetrics {
  timestamp: string;
  funding_rates: Record<string, number>;
  open_interest: Record<string, { oi: number; oi_usdt: number }>;
  long_short_ratio: Record<string, { long_pct: number; short_pct: number; ratio: number }>;
  order_book: Record<string, { bid_vol: number; ask_vol: number; imbalance: number }>;
  exchange_flow: { btc_total_supply: number | null };
  btc_dominance: number | null;
  stablecoin_mcap: { USDT: number | null; USDC: number | null; total: number };
  tradfi: { sp500?: number; sp500_change?: number; dxy?: number; dxy_change?: number };
}

export const getMarketData = () =>
  api<{ latest: MarketMetrics | null; history: MarketMetrics[] }>("/bot/market-data");

export interface DataCoverageRow {
  symbol: string;
  candle_hours: number;
  intel_hours: number;
  funding_hours: number;
  sentiment_hours: number;
  news_items: number;
  sentiment_items: number;
  intel_coverage_pct: number;
  funding_coverage_pct: number;
  sentiment_coverage_pct: number;
  last_intel_at: string | null;
  last_funding_at: string | null;
  last_sentiment_at: string | null;
  last_news_at: string | null;
  intel_staleness_h: number | null;
  funding_staleness_h: number | null;
  sentiment_staleness_h: number | null;
  news_staleness_h: number | null;
}

export const getDataCoverage = (days = 60, tf = 60) =>
  api<{ lookback_days: number; tf: number; symbols: DataCoverageRow[] }>(
    `/bot/data-coverage?days=${days}&tf=${tf}`
  );

export const getConfig = () => api<Record<string, unknown>>("/bot/config");
export const updateConfig = (updates: Record<string, unknown>) =>
  api<{ ok: boolean; updated: Record<string, unknown> }>("/bot/config", {
    method: "PUT",
    body: JSON.stringify(updates),
  });

export const saveCurrentConfigAsDefaults = () =>
  api<{ ok: boolean; saved: number; path: string }>("/bot/config/defaults/save-current", {
    method: "POST",
  });

export const exportCurrentConfig = () =>
  api<{ ok: boolean; saved: number; filename: string; path: string }>("/bot/config/export-current", {
    method: "POST",
  });

export const listConfigPresets = () =>
  api<{ ok: boolean; files: string[]; path: string }>("/bot/config/presets/list");

export const getCredentialsStatus = () =>
  api<{
    kraken: { configured: boolean };
    binance: { configured: boolean };
    mode: string;
    source?: string;
    env_path?: string;
  }>("/bot/credentials/status");

export const reloadCredentialsEnv = () =>
  api<{
    ok: boolean;
    kraken_configured: boolean;
    binance_configured: boolean;
    env_path: string;
  }>("/bot/credentials/reload-env", {
    method: "POST",
  });

export const testCredentials = (exchange: "kraken" | "binance") =>
  api<{
    ok: boolean;
    exchange: "kraken" | "binance";
    message: string;
    mode: string;
  }>(`/bot/credentials/test?exchange=${exchange}`, {
    method: "POST",
  });

export const runLiveDryRun = () =>
  api<{
    ok: boolean;
    mode: string;
    orders_placed: number;
    kraken: { ok: boolean; message: string; snapshot: Record<string, unknown> };
    binance: { ok: boolean; message: string; snapshot: Record<string, unknown> };
    note: string;
  }>("/bot/live/dry-run", {
    method: "POST",
  });

export const getLatestConfigRecommendation = () =>
  api<{
    latest: null | {
      created_at: string;
      selected?: {
        overrides?: Record<string, number | string | boolean>;
        summary?: {
          win_rate?: number;
          profit_factor?: number;
          final_equity?: number;
          cash_buffer?: number;
          total_trades?: number;
        };
        score?: number;
        llm_reason?: string;
      };
      apply_guard_passed?: boolean;
      auto_apply_enabled?: boolean;
      applied?: Record<string, unknown>;
    };
  }>("/bot/config-recommendations/latest");

export const applyLatestConfigRecommendation = () =>
  api<{ ok: boolean; applied: Record<string, unknown> }>("/bot/config-recommendations/apply-latest", {
    method: "POST",
  });
