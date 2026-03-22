import { api } from "./client";
import type { SentimentDoc, SentimentSummary, MarketIntel } from "../types";

export const getRecentSentiments = (symbol = "BTC", limit = 20) =>
  api<SentimentDoc[]>(`/sentiment/recent?symbol=${symbol}&limit=${limit}`);

export const getSentimentSummary = (symbol = "BTC", window = 60) =>
  api<SentimentSummary>(`/sentiment/summary?symbol=${symbol}&window=${window}`);

export const getIntel = () => api<MarketIntel>("/sentiment/intel");
