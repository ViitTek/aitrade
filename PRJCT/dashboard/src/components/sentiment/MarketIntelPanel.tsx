import { useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getIntel } from "../../api/sentiment";
import StatusBadge from "../shared/StatusBadge";

export default function MarketIntelPanel() {
  const fetcher = useCallback(() => getIntel(), []);
  const { data } = usePolling(fetcher, 60000, "market_intel");

  if (!data || !data.created_at) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide mb-2">Market Intel</h2>
        <div className="text-gray-500 text-sm">No intel data available</div>
      </div>
    );
  }

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Market Intel</h2>
        <span className="text-xs text-gray-500">
          {new Date(data.created_at).toLocaleString("cs-CZ")}
        </span>
      </div>

      <div className="flex items-center gap-3 mb-4">
        <span className="text-xs text-gray-400">Overall:</span>
        <StatusBadge
          label={data.overall}
          variant={data.overall === "RISK-ON" ? "green" : data.overall === "RISK-OFF" ? "red" : "yellow"}
        />
        {data.degraded && <StatusBadge label="LLM FALLBACK" variant="yellow" />}
      </div>
      {data.degraded && data.last_error && (
        <div className="mb-4 rounded border border-yellow-800 bg-yellow-950/30 px-2 py-1">
          <p className="text-[10px] text-yellow-300 truncate">{data.last_error}</p>
        </div>
      )}

      {data.assets && Object.entries(data.assets).length > 0 && (
        <div className="space-y-2">
          {Object.entries(data.assets).map(([asset, info]) => (
            <div key={asset} className="flex items-center gap-3 text-sm">
              <span className="text-gray-300 font-medium w-12">{asset}</span>
              <StatusBadge
                label={info.outlook}
                variant={info.outlook === "BULLISH" ? "green" : info.outlook === "BEARISH" ? "red" : "yellow"}
              />
              <StatusBadge label={info.confidence} variant="gray" />
              <span className="text-xs text-gray-500 truncate">{info.reason}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
