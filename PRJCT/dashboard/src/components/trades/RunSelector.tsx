import { useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getRuns } from "../../api/bot";

interface Props {
  value: string;
  onChange: (runId: string) => void;
}

export default function RunSelector({ value, onChange }: Props) {
  const fetcher = useCallback(() => getRuns(), []);
  const { data: runs } = usePolling(fetcher, 30000, "runs");

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200"
    >
      <option value="">Current Run</option>
      {runs?.map((r) => (
        <option key={r.run_id} value={r.run_id}>
          {r.is_backtest ? "[BT] " : ""}{r.run_id} — {r.trade_count} trades
          {r.started_at ? ` (${new Date(r.started_at).toLocaleDateString("cs-CZ")})` : ""}
        </option>
      ))}
    </select>
  );
}
