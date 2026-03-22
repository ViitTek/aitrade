import { useCallback, useState } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getEquityCurve } from "../../api/bot";
import {
  ResponsiveContainer,
  LineChart,
  Line,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from "recharts";

interface Props {
  runId?: string;
  includeMtm?: boolean;
  allRuntimeDefault?: boolean;
}

export default function EquityCurveChart({ runId, includeMtm = false, allRuntimeDefault = false }: Props) {
  const [allRuntime, setAllRuntime] = useState(allRuntimeDefault);
  const fetcher = useCallback(
    () => getEquityCurve(runId, includeMtm, allRuntime, !allRuntime),
    [runId, includeMtm, allRuntime]
  );
  const { data } = usePolling(fetcher, 10000, `equity_curve:${runId ?? "current"}:${includeMtm}:${allRuntime}`);

  if (!data || data.length < 1) {
    return (
      <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
        <div className="flex items-center justify-between mb-2">
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Equity Curve</h2>
          <button
            type="button"
            onClick={() => setAllRuntime((v) => !v)}
            className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-300 hover:bg-gray-800"
          >
            {allRuntime ? "All Runtime" : "Since Restart"}
          </button>
        </div>
        <div className="text-gray-500 text-sm text-center py-8">No trade data yet</div>
      </div>
    );
  }

  const chartData = data
    .filter((d) => d.t)
    .map((d) => ({
      t: d.t ? new Date(d.t).toLocaleString("cs-CZ", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "",
      equity: d.equity,
    }));

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Equity Curve</h2>
        <button
          type="button"
          onClick={() => setAllRuntime((v) => !v)}
          className="text-xs px-2 py-1 rounded border border-gray-700 text-gray-300 hover:bg-gray-800"
        >
          {allRuntime ? "All Runtime" : "Since Restart"}
        </button>
      </div>
      <ResponsiveContainer width="100%" height={250}>
        <LineChart data={chartData}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis dataKey="t" tick={{ fill: "#9CA3AF", fontSize: 10 }} />
          <YAxis tick={{ fill: "#9CA3AF", fontSize: 10 }} domain={["auto", "auto"]} />
          <Tooltip
            contentStyle={{ backgroundColor: "#1F2937", border: "1px solid #374151", borderRadius: 8 }}
            labelStyle={{ color: "#9CA3AF" }}
          />
          <Line type="monotone" dataKey="equity" stroke="#3B82F6" strokeWidth={2} dot={false} />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
