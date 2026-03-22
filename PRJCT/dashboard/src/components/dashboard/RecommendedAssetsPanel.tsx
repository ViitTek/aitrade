import { getRecommendations } from "../../api/bot";
import { usePolling } from "../../hooks/usePolling";

export default function RecommendedAssetsPanel() {
  const { data } = usePolling(getRecommendations, 30000);
  const symbols = data?.symbols ?? [];
  const details = data?.details ?? {};
  const always = new Set(data?.always_active ?? []);

  if (!symbols.length) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide mb-2">LLM Recommended Assets</h3>
        <p className="text-xs text-gray-500">No recommendations yet.</p>
      </div>
    );
  }

  const age = data?.created_at
    ? Math.round((Date.now() - new Date(data.created_at).getTime()) / 60000)
    : null;

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">LLM Recommended Assets</h3>
        <div className="flex items-center gap-3">
          {data?.degraded && (
            <span className="text-[10px] font-bold px-2 py-0.5 rounded bg-yellow-900 text-yellow-300">LLM FALLBACK</span>
          )}
          <span
            className={`text-[10px] font-bold px-2 py-0.5 rounded ${
              data?.overall === "RISK-ON"
                ? "bg-green-900 text-green-300"
                : data?.overall === "RISK-OFF"
                ? "bg-red-900 text-red-300"
                : "bg-gray-700 text-gray-300"
            }`}
          >
            {data?.overall ?? "N/A"}
          </span>
          {age !== null && (
            <span className="text-[10px] text-gray-500">{age < 60 ? `${age}m ago` : `${Math.round(age / 60)}h ago`}</span>
          )}
        </div>
      </div>

      {data?.degraded && data?.last_error && (
        <div className="mb-3 rounded border border-yellow-800 bg-yellow-950/30 px-2 py-1">
          <p className="text-[10px] text-yellow-300 truncate">{data.last_error}</p>
        </div>
      )}

      <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-2">
        {symbols.map((sym: string) => {
          const base = sym.split("/")[0].toUpperCase();
          const detail = details[base] || details[sym] || {};
          const isAlways = always.has(sym);
          const outlook = detail.outlook;
          const ready = detail.ready;
          const candles = detail.candle_count ?? 0;

          return (
            <div
              key={sym}
              className={`rounded-lg border p-2 ${
                isAlways ? "border-blue-800 bg-blue-950/30" : "border-gray-700 bg-gray-800/50"
              }`}
            >
              <div className="flex items-center justify-between mb-1">
                <span className="text-xs font-bold text-gray-200">{base}</span>
                {outlook && (
                  <span
                    className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                      outlook === "BULLISH" ? "bg-green-900 text-green-300" : "bg-red-900 text-red-300"
                    }`}
                  >
                    {outlook}
                  </span>
                )}
              </div>

              {detail.reason && (
                <p className="text-[10px] text-gray-500 leading-tight mb-1 line-clamp-2">{detail.reason}</p>
              )}

              <div className="flex items-center justify-between">
                {isAlways ? (
                  <span className="text-[9px] text-blue-400">always active</span>
                ) : (
                  <span className="text-[9px] text-gray-500">recommended</span>
                )}
                {!isAlways && (
                  <span className={`text-[9px] ${ready ? "text-green-400" : "text-yellow-500"}`}>
                    {ready ? "ready" : `${candles}/50 candles`}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
