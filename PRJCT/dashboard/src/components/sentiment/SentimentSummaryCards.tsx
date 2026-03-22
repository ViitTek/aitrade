import { useState, useCallback } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getSentimentSummary } from "../../api/sentiment";
import MetricCard from "../shared/MetricCard";

interface Props {
  bases?: string[];
  symbol: string;
  onSymbolChange: (symbol: string) => void;
}

export default function SentimentSummaryCards({ bases, symbol, onSymbolChange }: Props) {
  const fetcher = useCallback(() => getSentimentSummary(symbol, 120), [symbol]);
  const { data } = usePolling(fetcher, 30000, `sent_summary:${symbol}:120`);
  const symbolButtons = bases && bases.length > 0 ? bases : ["BTC", "ETH", "SOL", "PAXG"];

  return (
    <div>
      <div className="flex flex-wrap gap-2 mb-3">
        {symbolButtons.map((s) => (
          <button key={s} onClick={() => onSymbolChange(s)}
            className={`px-3 py-1 text-xs rounded ${s === symbol ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-400"}`}>
            {s}
          </button>
        ))}
      </div>
      {data && (
        <div className="grid grid-cols-4 gap-3">
          <MetricCard label="Positive" value={data.Positive} color="text-green-400" />
          <MetricCard label="Neutral" value={data.Neutral} color="text-gray-400" />
          <MetricCard label="Negative" value={data.Negative} color="text-red-400" />
          <MetricCard label="Dominant" value={data.dominant}
            color={data.dominant === "Positive" ? "text-green-400" : data.dominant === "Negative" ? "text-red-400" : "text-gray-400"} />
        </div>
      )}
    </div>
  );
}
