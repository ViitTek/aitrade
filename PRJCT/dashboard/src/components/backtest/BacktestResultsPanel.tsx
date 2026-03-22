import type { BacktestResult, MultiBacktestResult, PerSymbolStats } from "../../types";
import MetricCard from "../shared/MetricCard";

function n(value: unknown, fallback = 0): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

function money(value: unknown): string {
  return `$${n(value).toFixed(2)}`;
}

function pct01(value: unknown): string {
  return `${(n(value) * 100).toFixed(1)}%`;
}

function pf(value: unknown): string {
  if (value === Infinity) return "∞";
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(2) : "-";
}

function SingleResult({ result }: { result: BacktestResult }) {
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
          Backtest Results
        </h2>
        <span className="text-xs text-gray-500">
          {result.symbol} | {result.source} | Run: {result.run_id}
        </span>
      </div>

      <div className="grid grid-cols-4 gap-3">
        <MetricCard label="Total Trades" value={result.total_trades} />
        <MetricCard
          label="Win Rate"
          value={pct01(result.win_rate)}
          color={n(result.win_rate) >= 0.5 ? "text-green-400" : "text-red-400"}
        />
        <MetricCard
          label="Total PnL"
          value={money(result.total_pnl)}
          color={n(result.total_pnl) >= 0 ? "text-green-400" : "text-red-400"}
        />
        <MetricCard
          label="Profit Factor"
          value={pf(result.profit_factor)}
          color={n(result.profit_factor) >= 1 ? "text-green-400" : "text-red-400"}
        />
        <MetricCard label="Max Drawdown" value={money(result.max_drawdown)} color="text-red-400" />
        <MetricCard label="Avg Win" value={money(result.avg_win)} color="text-green-400" />
        <MetricCard label="Avg Loss" value={money(result.avg_loss)} color="text-red-400" />
        <MetricCard label="Final Equity" value={money(result.final_equity)} />
        <MetricCard label="Cash Buffer" value={money(result.cash_buffer)} color="text-yellow-400" />
      </div>
    </div>
  );
}

function PerSymbolRow({ data }: { data: PerSymbolStats }) {
  if (!data.ok) {
    return (
      <tr className="border-t border-gray-800">
        <td className="px-3 py-2 text-xs font-mono text-gray-300">{data.symbol}</td>
        <td colSpan={6} className="px-3 py-2 text-xs text-red-400">{data.error}</td>
      </tr>
    );
  }
  return (
    <tr className="border-t border-gray-800 hover:bg-gray-800/50">
      <td className="px-3 py-2 text-xs font-mono text-gray-300">{data.symbol}</td>
      <td className="px-3 py-2 text-xs text-gray-400 text-right">{data.total_candles}</td>
      <td className="px-3 py-2 text-xs text-gray-400 text-right">{data.total_trades}</td>
      <td className={`px-3 py-2 text-xs text-right font-semibold ${data.win_rate >= 0.5 ? "text-green-400" : "text-red-400"}`}>
        {pct01(data.win_rate)}
      </td>
      <td className={`px-3 py-2 text-xs text-right font-semibold ${n(data.total_pnl) >= 0 ? "text-green-400" : "text-red-400"}`}>
        {money(data.total_pnl)}
      </td>
      <td className={`px-3 py-2 text-xs text-right ${n(data.profit_factor) >= 1 ? "text-green-400" : "text-red-400"}`}>
        {pf(data.profit_factor)}
      </td>
      <td className="px-3 py-2 text-xs text-green-400 text-right">{money(data.avg_win)}</td>
      <td className="px-3 py-2 text-xs text-red-400 text-right">{money(data.avg_loss)}</td>
    </tr>
  );
}

function MultiResult({ data }: { data: MultiBacktestResult }) {
  const s = data.summary;
  return (
    <div className="space-y-4">
      {/* Agregované výsledky — sdílené portfolio */}
      <div className="bg-gray-900 rounded-lg border border-blue-800 p-4">
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-sm font-semibold text-blue-400 uppercase tracking-wide">
            Multi-Symbol Summary ({s.symbols} symbols, shared portfolio)
          </h2>
          <span className="text-xs text-gray-500">Run: {data.run_id}</span>
        </div>
        <div className="grid grid-cols-5 gap-3">
          <MetricCard label="Total Trades" value={s.total_trades} />
          <MetricCard
            label="Win Rate"
            value={pct01(s.win_rate)}
            color={n(s.win_rate) >= 0.5 ? "text-green-400" : "text-red-400"}
          />
          <MetricCard
            label="Total PnL"
            value={money(s.total_pnl)}
            color={n(s.total_pnl) >= 0 ? "text-green-400" : "text-red-400"}
          />
          <MetricCard
            label="Profit Factor"
            value={pf(s.profit_factor)}
            color={n(s.profit_factor) >= 1 ? "text-green-400" : "text-red-400"}
          />
          <MetricCard label="Max Drawdown" value={money(s.max_drawdown)} color="text-red-400" />
          <MetricCard label="Avg Win" value={money(s.avg_win)} color="text-green-400" />
          <MetricCard label="Avg Loss" value={money(s.avg_loss)} color="text-red-400" />
          <MetricCard label="Final Equity" value={money(s.final_equity)} />
          <MetricCard label="Cash Buffer" value={money(s.cash_buffer)} color="text-yellow-400" />
          <MetricCard label="Total Candles" value={s.total_candles} />
        </div>
      </div>

      {/* Per-symbol tabulka */}
      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800">
          <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide">Per-Symbol Breakdown</h3>
        </div>
        <table className="w-full">
          <thead>
            <tr className="text-[10px] text-gray-500 uppercase">
              <th className="px-3 py-2 text-left">Symbol</th>
              <th className="px-3 py-2 text-right">Candles</th>
              <th className="px-3 py-2 text-right">Trades</th>
              <th className="px-3 py-2 text-right">Win Rate</th>
              <th className="px-3 py-2 text-right">PnL</th>
              <th className="px-3 py-2 text-right">PF</th>
              <th className="px-3 py-2 text-right">Avg Win</th>
              <th className="px-3 py-2 text-right">Avg Loss</th>
            </tr>
          </thead>
          <tbody>
            {data.results.map((r) => (
              <PerSymbolRow key={r.symbol} data={r} />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

interface Props {
  result: BacktestResult | MultiBacktestResult;
}

export default function BacktestResultsPanel({ result }: Props) {
  if ("multi" in result && result.multi) {
    return <MultiResult data={result as MultiBacktestResult} />;
  }
  return <SingleResult result={result as BacktestResult} />;
}
