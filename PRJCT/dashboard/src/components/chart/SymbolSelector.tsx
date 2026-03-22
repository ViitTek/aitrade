import { useCallback, useEffect } from "react";
import { getSymbols } from "../../api/market";
import { usePolling } from "../../hooks/usePolling";

interface Props {
  value: string;
  onChange: (symbol: string) => void;
  symbols?: string[];
}

export default function SymbolSelector({ value, onChange, symbols: externalSymbols }: Props) {
  const fetcher = useCallback(() => getSymbols(60), []);
  const { data } = usePolling(fetcher, 30000, "market_symbols:60");
  const symbols = (externalSymbols && externalSymbols.length > 0) ? externalSymbols : (data?.symbols ?? []);
  useEffect(() => {
    if (symbols.length > 0 && !symbols.includes(value)) {
      onChange(symbols[0]);
    }
  }, [symbols, value, onChange]);

  return (
    <select
      value={value}
      onChange={(e) => onChange(e.target.value)}
      className="bg-gray-800 border border-gray-700 rounded px-3 py-2 text-sm min-w-[180px]"
    >
      {symbols.length === 0 ? (
        <>
          <option value="BTC/USDT">BTC/USDT</option>
          <option value="ETH/USDT">ETH/USDT</option>
          <option value="SOL/USDT">SOL/USDT</option>
          <option value="PAXG/USDT">PAXG/USDT</option>
        </>
      ) : (
        symbols.map((s) => (
          <option key={s} value={s}>
            {s}
          </option>
        ))
      )}
    </select>
  );
}
