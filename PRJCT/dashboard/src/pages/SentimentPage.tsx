import { useEffect, useMemo, useState } from "react";
import SentimentSummaryCards from "../components/sentiment/SentimentSummaryCards";
import NewsTable from "../components/sentiment/NewsTable";
import MarketIntelPanel from "../components/sentiment/MarketIntelPanel";
import { usePolling } from "../hooks/usePolling";
import { getTradedSymbols } from "../api/bot";

export default function SentimentPage() {
  const { data: traded } = usePolling(() => getTradedSymbols(undefined, 720), 30000, "traded_symbols:720");
  const bases = useMemo(
    () =>
      (traded?.symbols ?? [])
        .map((s) => s.split("/")[0].toUpperCase())
        .filter((v, i, a) => a.indexOf(v) === i)
        .sort(),
    [traded?.symbols]
  );
  const [symbol, setSymbol] = useState("BTC");
  useEffect(() => {
    if (bases.length > 0 && !bases.includes(symbol)) {
      setSymbol(bases[0]);
    }
  }, [bases, symbol]);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold">Sentiment & Intel</h1>
      <SentimentSummaryCards bases={bases} symbol={symbol} onSymbolChange={setSymbol} />
      <MarketIntelPanel />
      <NewsTable symbol={symbol} />
    </div>
  );
}
