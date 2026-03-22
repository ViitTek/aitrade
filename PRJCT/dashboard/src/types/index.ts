export interface Portfolio {
  run_id: string;
  equity: number;
  equity_mtm?: number;
  cash_buffer: number;
  daily_pnl: number;
  daily_pnl_mtm?: number;
  daily_unrealized_pnl?: number;
  unrealized_pnl?: number;
}

export interface Position {
  symbol: string;
  side: string;
  entry_price: number;
  qty: number;
  sl: number;
  tp: number;
  original_sl?: number;
  entry_time: string;
  exit_time?: string;
  exit_price?: number;
  pnl?: number;
  reason: string;
  reason_exit?: string;
  status: string;
  current_price?: number;
  unrealized_pnl?: number;
}

export interface BotStatus {
  running: boolean;
  run_id: string | null;
  stopped_at?: string | null;
  stopped_reason?: string | null;
  workers?: {
    news_worker: boolean;
    market_intel_worker: boolean;
    binance_feed?: boolean;
  };
}

export interface EquityPoint {
  t: string | null;
  equity: number;
}

export interface Candle {
  t: string;
  o: number;
  h: number;
  l: number;
  c: number;
  v: number;
}

export interface BacktestResult {
  ok: boolean;
  run_id: string;
  symbol: string;
  source: string;
  started_at?: string;
  finished_at?: string;
  duration_sec?: number;
  total_candles: number;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  max_drawdown: number;
  profit_factor: number;
  avg_win: number;
  avg_loss: number;
  final_equity: number;
  cash_buffer: number;
}

export interface PerSymbolStats {
  ok: boolean;
  symbol: string;
  source: string;
  total_candles: number;
  total_trades: number;
  win_rate: number;
  total_pnl: number;
  avg_win: number;
  avg_loss: number;
  profit_factor: number;
  error?: string;
}

export interface MultiBacktestResult {
  ok: boolean;
  multi: true;
  run_id: string;
  started_at?: string;
  finished_at?: string;
  duration_sec?: number;
  results: PerSymbolStats[];
  summary: {
    symbols: number;
    total_candles: number;
    total_trades: number;
    win_rate: number;
    total_pnl: number;
    max_drawdown: number;
    profit_factor: number;
    avg_win: number;
    avg_loss: number;
    final_equity: number;
    cash_buffer: number;
  };
}

export interface BotEvent {
  run_id: string;
  t: string;
  level: string;
  msg: string;
  data?: Record<string, unknown>;
}

export interface Signal {
  run_id: string;
  t: string;
  symbol: string;
  side: string;
  price: number;
  action: string;
  reason: string;
  detail: string;
}

export interface RunInfo {
  run_id: string;
  started_at: string;
  trade_count: number;
  is_backtest: boolean;
}

export interface SentimentDoc {
  text: string;
  sentiment: string;
  created_at: string;
  source: string;
  symbols: string[];
}

export interface SentimentSummary {
  Positive: number;
  Neutral: number;
  Negative: number;
  total: number;
  dominant: string;
}

export interface MarketIntel {
  overall: string;
  assets: Record<string, { outlook: string; confidence: string; reason: string }>;
  created_at: string | null;
  llm_ok?: boolean | null;
  degraded?: boolean;
  last_error?: string | null;
}
