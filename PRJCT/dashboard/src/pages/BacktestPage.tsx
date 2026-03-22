import { useState, useCallback, useEffect } from "react";
import type { BacktestResult, MultiBacktestResult } from "../types";
import BacktestForm from "../components/backtest/BacktestForm";
import BacktestResultsPanel from "../components/backtest/BacktestResultsPanel";
import EquityCurveChart from "../components/dashboard/EquityCurveChart";
import TradeHistoryTable from "../components/trades/TradeHistoryTable";
import { usePolling } from "../hooks/usePolling";
import { getClosedPositions } from "../api/bot";

type AnyResult = BacktestResult | MultiBacktestResult;
const LAST_BACKTEST_RESULT_KEY = "aiinvest.backtest.lastResult";

export default function BacktestPage() {
  const [result, setResult] = useState<AnyResult | null>(null);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(LAST_BACKTEST_RESULT_KEY);
      if (!raw) return;
      const parsed = JSON.parse(raw) as AnyResult;
      if (
        parsed &&
        typeof parsed === "object" &&
        "run_id" in parsed &&
        typeof (parsed as { run_id?: unknown }).run_id === "string"
      ) {
        setResult(parsed);
      } else {
        window.localStorage.removeItem(LAST_BACKTEST_RESULT_KEY);
      }
    } catch {
      try {
        window.localStorage.removeItem(LAST_BACKTEST_RESULT_KEY);
      } catch {
        // ignore broken local cache
      }
    }
  }, []);

  const handleResult = useCallback((next: AnyResult) => {
    setResult(next);
    try {
      window.localStorage.setItem(LAST_BACKTEST_RESULT_KEY, JSON.stringify(next));
    } catch {
      // storage may be unavailable in private mode
    }
  }, []);

  // Oba typy výsledků mají run_id — single i multi (sdílené portfolio)
  const runId = result?.run_id ?? null;

  const fetcher = useCallback(
    () => (runId ? getClosedPositions(runId, 200) : Promise.resolve([])),
    [runId]
  );
  const { data: trades } = usePolling(fetcher, 999999);

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold">Backtest</h1>
      <BacktestForm onResult={handleResult} lastResult={result} />
      {result && (
        <>
          <BacktestResultsPanel result={result} />
          {runId && (
            <>
              <EquityCurveChart runId={runId} includeMtm={false} />
              <div className="bg-gray-900 rounded-lg border border-gray-800 overflow-hidden">
                <div className="px-4 py-3 border-b border-gray-800">
                  <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Backtest Trades</h2>
                </div>
                <TradeHistoryTable trades={trades ?? []} />
              </div>
            </>
          )}
        </>
      )}
    </div>
  );
}
