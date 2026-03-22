import { useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getSignals } from "../../api/bot";
import StatusBadge from "../shared/StatusBadge";

export default function RecentSignalsPanel() {
  const fetcher = useCallback(() => getSignals(undefined, 20), []);
  const { data: signals } = usePolling(fetcher, 5000, "recent_signals");

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
          Recent Signals
        </h2>
      </div>
      {!signals || signals.length === 0 ? (
        <div className="px-4 py-6 text-center text-gray-500 text-sm">
          No signals yet
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-gray-400 text-xs uppercase bg-gray-900/50">
            <tr>
              <th className="px-4 py-2 text-left">Time</th>
              <th className="px-4 py-2 text-left">Symbol</th>
              <th className="px-4 py-2 text-left">Side</th>
              <th className="px-4 py-2 text-left">Action</th>
              <th className="px-4 py-2 text-right">Price</th>
              <th className="px-4 py-2 text-left">Detail</th>
            </tr>
          </thead>
          <tbody>
            {signals.map((s, i) => (
              <tr key={i} className="border-t border-gray-800/50 hover:bg-gray-800/30">
                <td className="px-4 py-2 text-xs text-gray-400">
                  {s.t ? new Date(s.t).toLocaleString("cs-CZ") : "-"}
                </td>
                <td className="px-4 py-2 font-medium">{s.symbol}</td>
                <td className="px-4 py-2">
                  <StatusBadge label={s.side} variant={s.side === "BUY" ? "green" : "red"} />
                </td>
                <td className="px-4 py-2">
                  <StatusBadge
                    label={s.action}
                    variant={s.action === "executed" ? "green" : "yellow"}
                  />
                </td>
                <td className="px-4 py-2 text-right">{Number(s.price).toFixed(2)}</td>
                <td className="px-4 py-2 text-xs text-gray-400">{s.detail || "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
