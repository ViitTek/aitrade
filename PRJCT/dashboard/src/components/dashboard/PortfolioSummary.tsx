import { useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getPortfolio } from "../../api/bot";
import MetricCard from "../shared/MetricCard";

export default function PortfolioSummary() {
  const fetcher = useCallback(() => getPortfolio(), []);
  const { data } = usePolling(fetcher, 5000, "portfolio");

  if (!data) return null;

  const equity = data.equity_mtm ?? data.equity;
  const daily = data.daily_pnl_mtm ?? data.daily_pnl;

  return (
    <div className="space-y-2">
      <div className="grid grid-cols-3 gap-4">
      <MetricCard
        label={data.equity_mtm != null ? "Equity (MTM)" : "Equity"}
        value={`${equity.toFixed(2)} EUR`}
        color="text-white"
      />
      <MetricCard
        label="Cash Buffer"
        value={`${data.cash_buffer.toFixed(2)} EUR`}
        color="text-gray-300"
      />
      <MetricCard
        label="Daily PnL"
        value={`${daily >= 0 ? "+" : ""}${daily.toFixed(2)} EUR`}
        color={daily >= 0 ? "text-green-400" : "text-red-400"}
      />
      </div>
    </div>
  );
}
