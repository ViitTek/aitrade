import { useCallback, useState } from "react";
import { usePolling } from "../../hooks/usePolling";
import { applyLatestConfigRecommendation, getLatestConfigRecommendation } from "../../api/bot";

export default function AutoTunePanel() {
  const [msg, setMsg] = useState<string>("");
  const [applying, setApplying] = useState(false);
  const fetcher = useCallback(() => getLatestConfigRecommendation(), []);
  const { data, refresh } = usePolling(fetcher, 30000);
  const latest = data?.latest;
  const selected = latest?.selected;
  const summary = selected?.summary;
  const overrides = selected?.overrides || {};
  const keys = Object.keys(overrides);

  const onApply = async () => {
    setApplying(true);
    setMsg("");
    try {
      const res = await applyLatestConfigRecommendation();
      setMsg(`Applied ${Object.keys(res.applied || {}).length} params.`);
      refresh();
    } catch (e: any) {
      setMsg(`Apply failed: ${e?.message || String(e)}`);
    }
    setApplying(false);
  };

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Auto-Tune Recommendation</h2>
        {latest?.created_at && (
          <span className="text-xs text-gray-500">{new Date(latest.created_at).toLocaleString("cs-CZ")}</span>
        )}
      </div>

      {!latest ? (
        <div className="text-sm text-gray-500">No recommendation yet.</div>
      ) : (
        <div className="space-y-3">
          <div className="text-xs text-gray-400">
            Guard:{" "}
            <span className={latest.apply_guard_passed ? "text-green-400" : "text-red-400"}>
              {latest.apply_guard_passed ? "Application conditions met" : "Application conditions not met"}
            </span>
            {" | "}Auto-apply:{" "}
            <span className={latest.auto_apply_enabled ? "text-yellow-300" : "text-gray-400"}>
              {latest.auto_apply_enabled ? "ON" : "OFF"}
            </span>
          </div>

          <div className="grid grid-cols-2 sm:grid-cols-5 gap-2 text-xs">
            <div className="bg-gray-800 rounded px-2 py-1">WR: {(summary?.win_rate ?? 0).toFixed(2)}</div>
            <div className="bg-gray-800 rounded px-2 py-1">PF: {(summary?.profit_factor ?? 0).toFixed(2)}</div>
            <div className="bg-gray-800 rounded px-2 py-1">Equity: {(summary?.final_equity ?? 0).toFixed(2)}</div>
            <div className="bg-gray-800 rounded px-2 py-1">Buffer: {(summary?.cash_buffer ?? 0).toFixed(2)}</div>
            <div className="bg-gray-800 rounded px-2 py-1">Trades: {summary?.total_trades ?? 0}</div>
          </div>

          <div className="bg-gray-950 border border-gray-800 rounded p-2">
            <div className="text-xs text-gray-500 mb-1">Overrides ({keys.length})</div>
            <div className="text-xs font-mono text-gray-300 break-words">
              {keys.length ? keys.map((k) => `${k}=${String(overrides[k])}`).join(", ") : "none"}
            </div>
          </div>

          {selected?.llm_reason && <div className="text-xs text-blue-300">LLM: {selected.llm_reason}</div>}

          <div className="flex items-center gap-3">
            <button
              onClick={onApply}
              disabled={applying || !latest.apply_guard_passed || keys.length === 0}
              className="px-4 py-2 bg-blue-600 hover:bg-blue-700 rounded text-sm disabled:opacity-40 disabled:cursor-not-allowed"
            >
              {applying ? "Applying..." : "Apply Latest Recommendation"}
            </button>
            {msg && <span className="text-xs text-gray-300">{msg}</span>}
          </div>
        </div>
      )}
    </div>
  );
}


