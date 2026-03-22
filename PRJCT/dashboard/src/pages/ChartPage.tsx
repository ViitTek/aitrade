import { useState } from "react";
import CandlestickChart from "../components/chart/CandlestickChart";
import SymbolSelector from "../components/chart/SymbolSelector";

export default function ChartPage() {
  const [symbol, setSymbol] = useState("BTC/USDT");
  const [months, setMonths] = useState(1);
  const ranges = [12, 6, 3, 1];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Market Chart</h1>
        <div className="flex items-center gap-2">
          <div className="flex gap-1">
            {ranges.map((m) => (
              <button
                key={m}
                onClick={() => setMonths(m)}
                className={`px-2 py-1 text-xs rounded ${
                  months === m ? "bg-blue-600 text-white" : "bg-gray-800 text-gray-300"
                }`}
              >
                {m}M
              </button>
            ))}
          </div>
          <SymbolSelector value={symbol} onChange={setSymbol} />
        </div>
      </div>
      <CandlestickChart key={`${symbol}-${months}`} symbol={symbol} months={months} />
    </div>
  );
}
