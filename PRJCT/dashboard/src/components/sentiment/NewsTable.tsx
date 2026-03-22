import { useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getRecentSentiments } from "../../api/sentiment";
import StatusBadge from "../shared/StatusBadge";

interface Props {
  symbol: string;
}

export default function NewsTable({ symbol }: Props) {
  const fetcher = useCallback(() => getRecentSentiments(symbol, 30), [symbol]);
  const { data } = usePolling(fetcher, 30000, `sent_recent:${symbol}:30`);

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
      <div className="px-4 py-3 border-b border-gray-800">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Recent Sentiments</h2>
      </div>
      {!data || data.length === 0 ? (
        <div className="px-4 py-6 text-center text-gray-500 text-sm">No sentiment data</div>
      ) : (
        <table className="w-full text-sm">
          <thead className="text-gray-400 text-xs uppercase bg-gray-900/50">
            <tr>
              <th className="px-4 py-2 text-left">Time</th>
              <th className="px-4 py-2 text-left">Text</th>
              <th className="px-4 py-2 text-left">Sentiment</th>
              <th className="px-4 py-2 text-left">Source</th>
            </tr>
          </thead>
          <tbody>
            {data.map((d, i) => (
              <tr key={i} className="border-t border-gray-800/50 hover:bg-gray-800/30">
                <td className="px-4 py-2 text-gray-400 text-xs whitespace-nowrap">
                  {d.created_at ? new Date(d.created_at).toLocaleString("cs-CZ") : "-"}
                </td>
                <td className="px-4 py-2 text-gray-300 max-w-md truncate">{d.text}</td>
                <td className="px-4 py-2">
                  <StatusBadge
                    label={d.sentiment}
                    variant={d.sentiment === "Positive" ? "green" : d.sentiment === "Negative" ? "red" : "gray"}
                  />
                </td>
                <td className="px-4 py-2 text-gray-500 text-xs">{d.source}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
