import { useCallback, useMemo } from "react";
import {
  getConfig,
  getDataCoverage,
  getMarketData,
  getPortfolio,
  getSignals,
  getTradedSymbols,
  getClosedPositions,
} from "../../api/bot";
import { getSymbols, getCandles } from "../../api/market";
import { getIntel, getRecentSentiments, getSentimentSummary } from "../../api/sentiment";
import { usePolling } from "../../hooks/usePolling";

export default function WarmupPrefetch() {
  usePolling(useCallback(() => getPortfolio(), []), 15000, "portfolio");
  usePolling(useCallback(() => getSignals(undefined, 20), []), 15000, "recent_signals");
  usePolling(useCallback(() => getConfig(), []), 120000, "config");
  usePolling(useCallback(() => getMarketData(), []), 30000, "market_data");
  usePolling(useCallback(() => getDataCoverage(60, 60), []), 60000, "data_coverage:60:60");
  const { data: traded } = usePolling(
    useCallback(() => getTradedSymbols(undefined, 720), []),
    60000,
    "traded_symbols:720"
  );
  usePolling(useCallback(() => getSymbols(60), []), 60000, "market_symbols:60");
  usePolling(useCallback(() => getClosedPositions(undefined, 100), []), 15000, "closed_positions:current:100");
  usePolling(useCallback(() => getIntel(), []), 60000, "market_intel");

  const primarySymbol = useMemo(() => {
    const first = (traded?.symbols ?? [])[0];
    return first || "BTC/USDT";
  }, [traded?.symbols]);
  const primaryBase = useMemo(() => primarySymbol.split("/")[0].toUpperCase(), [primarySymbol]);
  const chartLimit = 744; // ~1 month of H1 candles

  usePolling(
    useCallback(() => getCandles(primarySymbol, 60, chartLimit), [primarySymbol]),
    60000,
    `candles:${primarySymbol}:60:${chartLimit}`
  );
  usePolling(
    useCallback(() => getSentimentSummary(primaryBase, 120), [primaryBase]),
    30000,
    `sent_summary:${primaryBase}:120`
  );
  usePolling(
    useCallback(() => getRecentSentiments(primaryBase, 30), [primaryBase]),
    30000,
    `sent_recent:${primaryBase}:30`
  );

  return null;
}

