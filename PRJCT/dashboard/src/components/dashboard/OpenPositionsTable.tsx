import { useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getOpenPositions } from "../../api/bot";
import StatusBadge from "../shared/StatusBadge";

export default function OpenPositionsTable() {
  const fetcher = useCallback(() => getOpenPositions(), []);
  const { data: positions } = usePolling(fetcher, 5000, "open_positions");

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
          Open Positions
        </h2>
      </div>
      {!positions || positions.length === 0 ? (
        <div className="px-4 py-6 text-center text-gray-500 text-sm">
          No open positions
        </div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-gray-400 text-xs uppercase bg-gray-900/50">
            <tr>
              <th className="px-4 py-2 text-left">Symbol</th>
              <th className="px-4 py-2 text-left">Side</th>
              <th className="px-4 py-2 text-right">Entry</th>
              <th className="px-4 py-2 text-right">Current</th>
              <th className="px-4 py-2 text-right">Qty</th>
              <th className="px-4 py-2 text-right">SL</th>
              <th className="px-4 py-2 text-right">TP</th>
              <th className="px-4 py-2 text-right">PnL</th>
            </tr>
          </thead>
          <tbody>
            {positions.map((p, i) => (
              <tr key={i} className="border-t border-gray-800/50 hover:bg-gray-800/30">
                <td className="px-4 py-2 font-medium">{p.symbol}</td>
                <td className="px-4 py-2">
                  <StatusBadge
                    label={p.side}
                    variant={p.side === "BUY" ? "green" : "red"}
                  />
                </td>
                <td className="px-4 py-2 text-right">{p.entry_price?.toFixed(2)}</td>
                <td className="px-4 py-2 text-right">{p.current_price?.toFixed(2) ?? "-"}</td>
                <td className="px-4 py-2 text-right">{p.qty?.toFixed(6)}</td>
                <td className="px-4 py-2 text-right text-red-400">{p.sl?.toFixed(2)}</td>
                <td className="px-4 py-2 text-right text-green-400">{p.tp?.toFixed(2)}</td>
                <td className={`px-4 py-2 text-right font-medium ${
                  (p.unrealized_pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"
                }`}>
                  {p.unrealized_pnl != null
                    ? `${p.unrealized_pnl >= 0 ? "+" : ""}$${p.unrealized_pnl.toFixed(2)}`
                    : "-"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
