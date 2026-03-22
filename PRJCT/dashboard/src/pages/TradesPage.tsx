import { useState, useCallback } from "react";
import { usePolling } from "../hooks/usePolling";
import { getClosedPositions, getSignals } from "../api/bot";
import TradeHistoryTable from "../components/trades/TradeHistoryTable";
import RunSelector from "../components/trades/RunSelector";

export default function TradesPage() {
  const [runId, setRunId] = useState("");
  const fetcher = useCallback(
    () => getClosedPositions(runId || undefined, 100),
    [runId]
  );
  const { data: trades } = usePolling(fetcher, 10000, `closed_positions:${runId || "current"}`);
  const sigFetcher = useCallback(() => getSignals(runId || undefined, 100), [runId]);
  const { data: signals } = usePolling(sigFetcher, 10000, `signals:${runId || "current"}:100`);
  const paperSignals = (signals ?? []).filter((s) => ["shadow", "executed", "policy"].includes((s.action || "").toLowerCase()));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Trade History</h1>
        <RunSelector value={runId} onChange={setRunId} />
      </div>
      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <TradeHistoryTable trades={trades ?? []} />
      </div>
      <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
        <div className="px-4 py-3 border-b border-gray-800">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Paper/Shadow Signals</h2>
        </div>
        {!paperSignals.length ? (
          <div className="text-gray-500 text-sm text-center py-6">No paper/shadow signals</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-gray-400 text-xs uppercase bg-gray-900/50">
                <tr>
                  <th className="px-4 py-2 text-left">Time</th>
                  <th className="px-4 py-2 text-left">Symbol</th>
                  <th className="px-4 py-2 text-left">Side</th>
                  <th className="px-4 py-2 text-left">Action</th>
                  <th className="px-4 py-2 text-left">Detail</th>
                </tr>
              </thead>
              <tbody>
                {paperSignals.map((s, i) => (
                  <tr key={`${s.t}-${s.symbol}-${i}`} className="border-t border-gray-800/50 hover:bg-gray-800/30">
                    <td className="px-4 py-2 text-gray-400 text-xs">{new Date(s.t).toLocaleString("cs-CZ")}</td>
                    <td className="px-4 py-2">{s.symbol}</td>
                    <td className="px-4 py-2">{s.side}</td>
                    <td className="px-4 py-2">{s.action}</td>
                    <td className="px-4 py-2 text-gray-400 text-xs">{s.detail || "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
