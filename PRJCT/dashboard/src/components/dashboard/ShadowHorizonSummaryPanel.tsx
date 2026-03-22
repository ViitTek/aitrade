import { useCallback } from "react";
import { getShadowHorizonSummary } from "../../api/bot";
import { usePolling } from "../../hooks/usePolling";

const HORIZONS = "720,1440,2160,2880,3600,4320,5040,5760,6480,7200,7920,8640,9360,10080";

export default function ShadowHorizonSummaryPanel() {
  const fetcher = useCallback(() => getShadowHorizonSummary(undefined, 720, HORIZONS), []);
  const { data } = usePolling(fetcher, 15 * 60 * 1000);

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">
          Shadow/Paper Horizon Summary
        </h3>
      </div>
      {!data?.items?.length ? (
        <div className="text-sm text-gray-500">No horizon data.</div>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full text-xs">
            <thead className="text-gray-400 uppercase">
              <tr>
                <th className="px-2 py-1 text-left">h</th>
                <th className="px-2 py-1 text-right">eval</th>
                <th className="px-2 py-1 text-right">WR</th>
                <th className="px-2 py-1 text-right">PF</th>
                <th className="px-2 py-1 text-right">AvgRet</th>
                <th className="px-2 py-1 text-right">Equity</th>
                <th className="px-2 py-1 text-right">Buffer</th>
                <th className="px-2 py-1 text-right">Daily PnL</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((x) => (
                <tr key={x.horizon_min} className="border-t border-gray-800/60">
                  <td className="px-2 py-1 font-mono">{x.horizon_min}</td>
                  <td className="px-2 py-1 text-right">{x.shadow_eval_samples}</td>
                  <td className="px-2 py-1 text-right">{x.shadow_win_rate_h == null ? "-" : x.shadow_win_rate_h.toFixed(4)}</td>
                  <td className="px-2 py-1 text-right">{x.shadow_profit_factor_h == null ? "-" : x.shadow_profit_factor_h.toFixed(4)}</td>
                  <td className="px-2 py-1 text-right">{x.shadow_avg_ret_h == null ? "-" : x.shadow_avg_ret_h.toFixed(6)}</td>
                  <td className="px-2 py-1 text-right">{x.equity_end_eur == null ? "-" : `${x.equity_end_eur.toFixed(2)} EUR`}</td>
                  <td className="px-2 py-1 text-right">{x.cash_buffer_end_eur == null ? "-" : `${x.cash_buffer_end_eur.toFixed(2)} EUR`}</td>
                  <td className={`px-2 py-1 text-right ${(x.pnl_vs_300_eur ?? 0) >= 0 ? "text-green-400" : "text-red-400"}`}>
                    {x.pnl_vs_300_eur == null ? "-" : `${x.pnl_vs_300_eur >= 0 ? "+" : ""}${x.pnl_vs_300_eur.toFixed(2)} EUR`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <p className="text-[10px] text-gray-500 mt-2">Refresh 15 min. Horizon stats are shadow/paper only.</p>
    </div>
  );
}
