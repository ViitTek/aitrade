import { backtestApi } from "./client";
import type { BacktestResult, MultiBacktestResult } from "../types";

export interface BacktestParams {
  source: string;
  symbol: string;
  dt_from: string;
  dt_to?: string;
  initial_equity?: number;
  interval?: number;
  with_sentiment?: boolean;
  mode?: "exact" | "vectorized_fast";
  overrides?: Record<string, number | boolean>;
}

export const runBacktest = (params: BacktestParams) =>
  backtestApi<BacktestResult | MultiBacktestResult>("/bot/backtest", {
    method: "POST",
    body: JSON.stringify(params),
  });
