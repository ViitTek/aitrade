import { api, backtestApi } from "./client";
import type { Candle } from "../types";

export const getCandles = (symbol = "BTC/USDT", tf = 60, limit = 300) =>
  api<Candle[]>(`/market/candles?symbol=${encodeURIComponent(symbol)}&tf=${tf}&limit=${limit}`);

export const getSymbols = (tf = 60) =>
  api<{ symbols: string[] }>(`/market/symbols?tf=${tf}`);

export const getSymbolsForBacktest = (tf = 60) =>
  backtestApi<{ symbols: string[] }>(`/market/symbols?tf=${tf}`);
