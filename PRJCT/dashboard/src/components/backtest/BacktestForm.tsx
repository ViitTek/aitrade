import { useCallback, useState } from "react";
import { runBacktest, type BacktestParams } from "../../api/backtest";
import { getSymbolsForBacktest } from "../../api/market";
import { getBacktestApiBase } from "../../api/client";
import { usePolling } from "../../hooks/usePolling";
import type { BacktestResult, MultiBacktestResult } from "../../types";

interface Props {
  onResult: (result: BacktestResult | MultiBacktestResult) => void;
  lastResult?: BacktestResult | MultiBacktestResult | null;
}

const OVERRIDE_PARAMS = [
  { key: "BREAKOUT_N", label: "Breakout N", default: 10, type: "number" as const, step: 1 },
  { key: "EMA_PERIOD", label: "EMA Period", default: 50, type: "number" as const, step: 1 },
  { key: "SL_ATR_MULT", label: "SL (ATR×)", default: 1.5, type: "number" as const, step: 0.1 },
  { key: "TP_ATR_MULT", label: "TP (ATR×)", default: 4.0, type: "number" as const, step: 0.5 },
  { key: "TRAIL_ATR_MULT", label: "Trail (ATR×)", default: 1.0, type: "number" as const, step: 0.1 },
  { key: "TRAIL_ACTIVATION_ATR", label: "Trail Activation (ATR×)", default: 2.0, type: "number" as const, step: 0.5 },
  { key: "RISK_PER_TRADE", label: "Risk/Trade", default: 0.005, type: "number" as const, step: 0.001 },
  { key: "PROFIT_SPLIT_REINVEST", label: "Profit Reinvest", default: 0.6, type: "number" as const, step: 0.05 },
  { key: "VOL_MULT", label: "Vol Mult", default: 1.5, type: "number" as const, step: 0.1 },
  { key: "COOLDOWN_CANDLES", label: "Cooldown", default: 2, type: "number" as const, step: 1 },
  { key: "FEE_RATE", label: "Fee Rate", default: 0.0008, type: "number" as const, step: 0.0001 },
];

export default function BacktestForm({ onResult, lastResult = null }: Props) {
  const todayIsoDate = () => new Date().toISOString().slice(0, 10);
  const fromDefaultIsoDate = () => {
    const d = new Date();
    d.setDate(d.getDate() - 14);
    return d.toISOString().slice(0, 10);
  };

  const [source, setSource] = useState("mongo");
  const [symbol, setSymbol] = useState("ALL");
  const [dtFrom, setDtFrom] = useState(fromDefaultIsoDate());
  const [dtTo, setDtTo] = useState(todayIsoDate());
  const [initialEquity, setInitialEquity] = useState(1000);
  const [interval, setInterval] = useState(60);
  const [withSentiment, setWithSentiment] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastRunMeta, setLastRunMeta] = useState<{
    started_at?: string;
    finished_at?: string;
    duration_sec?: number;
  } | null>(null);
  const [showOverrides, setShowOverrides] = useState(false);
  const [overrides, setOverrides] = useState<Record<string, number>>({});
  const symbolsFetcher = useCallback(() => getSymbolsForBacktest(interval), [interval]);
  const { data: symbolsData } = usePolling(symbolsFetcher, 30000);
  const symbolOptions = symbolsData?.symbols ?? [];
  const displayMeta = lastRunMeta ?? (lastResult
    ? {
        started_at: lastResult.started_at,
        finished_at: lastResult.finished_at,
        duration_sec: lastResult.duration_sec,
      }
    : null);

  const handleOverride = (key: string, value: string) => {
    const num = parseFloat(value);
    if (isNaN(num)) {
      const next = { ...overrides };
      delete next[key];
      setOverrides(next);
    } else {
      setOverrides({ ...overrides, [key]: num });
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setLoading(true);
    setError(null);
    try {
      const params: BacktestParams = {
        source,
        symbol,
        dt_from: dtFrom,
        dt_to: dtTo || undefined,
        initial_equity: initialEquity,
        interval,
        with_sentiment: withSentiment,
        mode: "exact",
      };
      if (Object.keys(overrides).length > 0) {
        params.overrides = overrides;
      }
      const result = await runBacktest(params);
      setLastRunMeta({
        started_at: result.started_at,
        finished_at: result.finished_at,
        duration_sec: result.duration_sec,
      });
      onResult(result);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
    setLoading(false);
  };

  return (
    <form onSubmit={handleSubmit} className="bg-gray-900 rounded-lg border border-gray-800 p-4 space-y-4">
      <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Backtest Runner</h2>
      <div className="text-[11px] text-gray-500">Backtest API: {getBacktestApiBase()}</div>
      {displayMeta && (
        <div className="text-[11px] text-gray-500">
          Last run:
          {" "}
          start {displayMeta.started_at ? new Date(displayMeta.started_at).toLocaleString() : "-"}
          {" | "}
          end {displayMeta.finished_at ? new Date(displayMeta.finished_at).toLocaleString() : "-"}
          {" | "}
          duration {typeof displayMeta.duration_sec === "number" ? `${displayMeta.duration_sec.toFixed(2)}s` : "-"}
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div>
          <label className="block text-xs text-gray-400 mb-1">Source</label>
          <select value={source} onChange={(e) => setSource(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            <option value="mongo">MongoDB</option>
            <option value="kraken">Kraken</option>
            <option value="binance">Binance</option>
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Symbol</label>
          <select value={symbol} onChange={(e) => setSymbol(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            <option value="ALL">All Symbols</option>
            {symbolOptions.map((s) => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">From</label>
          <input type="date" value={dtFrom} onChange={(e) => setDtFrom(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm" required />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">To</label>
          <input type="date" value={dtTo} onChange={(e) => setDtTo(e.target.value)}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm" />
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Interval (min)</label>
          <select value={interval} onChange={(e) => setInterval(Number(e.target.value))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm">
            {[5, 15, 60, 240].map((v) => (
              <option key={v} value={v}>{v === 60 ? "H1" : v === 240 ? "H4" : `M${v}`}</option>
            ))}
          </select>
        </div>
        <div>
          <label className="block text-xs text-gray-400 mb-1">Initial Equity (USDT)</label>
          <input
            type="number"
            min={1}
            step="1"
            value={initialEquity}
            onChange={(e) => setInitialEquity(Math.max(1, Number(e.target.value) || 1000))}
            className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm"
          />
        </div>
        <div className="flex items-end">
          <label className="flex items-center gap-2 text-sm text-gray-300">
            <input type="checkbox" checked={withSentiment} onChange={(e) => setWithSentiment(e.target.checked)}
              className="rounded bg-gray-800 border-gray-700" />
            Sentiment filter
          </label>
        </div>
      </div>

      {/* Strategy Parameter Overrides */}
      <div>
        <button type="button" onClick={() => setShowOverrides(!showOverrides)}
          className="text-xs text-blue-400 hover:text-blue-300 transition-colors">
          {showOverrides ? "Hide" : "Show"} Strategy Parameters
        </button>

        {showOverrides && (
          <div className="mt-3 grid grid-cols-5 gap-3">
            {OVERRIDE_PARAMS.map((p) => (
              <div key={p.key}>
                <label className="block text-[10px] text-gray-500 mb-0.5">{p.label}</label>
                <input
                  type="number"
                  step={p.step}
                  placeholder={String(p.default)}
                  value={overrides[p.key] ?? ""}
                  onChange={(e) => handleOverride(p.key, e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-xs font-mono placeholder-gray-600"
                />
              </div>
            ))}
          </div>
        )}
      </div>

      {error && <div className="text-red-400 text-sm">{error}</div>}

      <button type="submit" disabled={loading || !dtFrom}
        className="px-6 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed transition-colors">
        {loading ? "Running..." : "Run Backtest"}
      </button>
    </form>
  );
}
