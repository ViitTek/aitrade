import type { Position } from "../../types";
import StatusBadge from "../shared/StatusBadge";

interface Props {
  trades: Position[];
}

export default function TradeHistoryTable({ trades }: Props) {
  if (!trades.length) {
    return <div className="text-gray-500 text-sm text-center py-6">No trades</div>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-gray-400 text-xs uppercase bg-gray-900/50">
          <tr>
            <th className="px-4 py-2 text-left">Time</th>
            <th className="px-4 py-2 text-left">Symbol</th>
            <th className="px-4 py-2 text-left">Side</th>
            <th className="px-4 py-2 text-right">Entry</th>
            <th className="px-4 py-2 text-right">Exit</th>
            <th className="px-4 py-2 text-right">Qty</th>
            <th className="px-4 py-2 text-right">PnL</th>
            <th className="px-4 py-2 text-left">Exit Reason</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={i} className="border-t border-gray-800/50 hover:bg-gray-800/30">
              <td className="px-4 py-2 text-gray-400 text-xs">
                {t.exit_time ? new Date(t.exit_time).toLocaleString("cs-CZ") : "-"}
              </td>
              <td className="px-4 py-2 font-medium">{t.symbol}</td>
              <td className="px-4 py-2">
                <StatusBadge label={t.side} variant={t.side === "BUY" ? "green" : "red"} />
              </td>
              <td className="px-4 py-2 text-right">{t.entry_price?.toFixed(2)}</td>
              <td className="px-4 py-2 text-right">{t.exit_price?.toFixed(2) ?? "-"}</td>
              <td className="px-4 py-2 text-right">{t.qty?.toFixed(6)}</td>
              <td className={`px-4 py-2 text-right font-medium ${
                (t.pnl ?? 0) >= 0 ? "text-green-400" : "text-red-400"
              }`}>
                {t.pnl != null ? `${t.pnl >= 0 ? "+" : ""}$${t.pnl.toFixed(2)}` : "-"}
              </td>
              <td className="px-4 py-2">
                <StatusBadge
                  label={t.reason_exit ?? "-"}
                  variant={
                    t.reason_exit === "take_profit" ? "green"
                    : t.reason_exit === "trailing_stop" ? "yellow"
                    : t.reason_exit === "stop_loss" ? "red"
                    : "gray"
                  }
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
