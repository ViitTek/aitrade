import { useEffect, useRef, useState, useCallback } from "react";
import { createChart, type IChartApi, type ISeriesApi, CandlestickSeries, LineSeries } from "lightweight-charts";
import { getCandles } from "../../api/market";
import { getClosedPositions } from "../../api/bot";
import type { Candle } from "../../types";
import { usePolling } from "../../hooks/usePolling";

interface Props {
  symbol: string;
  tf?: number;
  months?: number;
}

function toTimestamp(iso: string): number {
  return Math.floor(new Date(iso).getTime() / 1000);
}

function calcEMA(candles: Candle[], period: number): { time: number; value: number }[] {
  const k = 2 / (period + 1);
  const result: { time: number; value: number }[] = [];
  let ema = candles[0]?.c ?? 0;

  for (let i = 0; i < candles.length; i++) {
    if (i === 0) {
      ema = candles[i].c;
    } else {
      ema = candles[i].c * k + ema * (1 - k);
    }
    if (i >= period - 1) {
      result.push({ time: toTimestamp(candles[i].t), value: ema });
    }
  }
  return result;
}

export default function CandlestickChart({ symbol, tf = 60, months = 3 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleSeriesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const emaSeriesRef = useRef<ISeriesApi<"Line"> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [rangeLabel, setRangeLabel] = useState<string>("");
  const days = Math.max(1, Math.round(months * 30.4));
  const limit = Math.max(200, Math.ceil((days * 24 * 60) / tf));
  const candlesFetcher = useCallback(() => getCandles(symbol, tf, limit), [symbol, tf, limit]);
  const { data: candles } = usePolling(candlesFetcher, 60000, `candles:${symbol}:${tf}:${limit}`);
  const closedFetcher = useCallback(() => getClosedPositions(undefined, 100), []);
  const { data: closedPositions } = usePolling(closedFetcher, 15000, "closed_positions:current:100");

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      width: containerRef.current.clientWidth,
      height: 400,
      layout: { background: { color: "#111827" }, textColor: "#9CA3AF" },
      grid: { vertLines: { color: "#1F2937" }, horzLines: { color: "#1F2937" } },
      crosshair: { mode: 0 },
      timeScale: { timeVisible: true, secondsVisible: false },
    });

    const candleSeries = chart.addSeries(CandlestickSeries, {
      upColor: "#22C55E",
      downColor: "#EF4444",
      borderUpColor: "#22C55E",
      borderDownColor: "#EF4444",
      wickUpColor: "#22C55E",
      wickDownColor: "#EF4444",
    });

    const emaSeries = chart.addSeries(LineSeries, {
      color: "#F59E0B",
      lineWidth: 2,
      priceLineVisible: false,
    });

    chartRef.current = chart;
    candleSeriesRef.current = candleSeries;
    emaSeriesRef.current = emaSeries;

    const handleResize = () => {
      if (containerRef.current) {
        chart.applyOptions({ width: containerRef.current.clientWidth });
      }
    };
    window.addEventListener("resize", handleResize);

    return () => {
      window.removeEventListener("resize", handleResize);
      chart.remove();
    };
  }, []);

  const loadData = useCallback(async () => {
    try {
      if (!candles || !candles.length) {
        setError("No candle data");
        return;
      }
      setError(null);

      // Deduplicate by timestamp (keep last occurrence) and sort
      const seen = new Map<number, typeof candles[0]>();
      for (const c of candles) {
        seen.set(toTimestamp(c.t), c);
      }
      const uniqueCandles = [...seen.values()].sort((a, b) => toTimestamp(a.t) - toTimestamp(b.t));

      const candleData = uniqueCandles.map((c) => ({
        time: toTimestamp(c.t) as any,
        open: c.o,
        high: c.h,
        low: c.l,
        close: c.c,
      }));
      candleSeriesRef.current?.setData(candleData);
      const from = uniqueCandles[0]?.t ? new Date(uniqueCandles[0].t).toLocaleString("cs-CZ") : "";
      const to = uniqueCandles[uniqueCandles.length - 1]?.t
        ? new Date(uniqueCandles[uniqueCandles.length - 1].t).toLocaleString("cs-CZ")
        : "";
      setRangeLabel(from && to ? `Period: ${from} -> ${to}` : "");

      const emaData = calcEMA(uniqueCandles, 50);
      emaSeriesRef.current?.setData(emaData as any);

      // Load trade markers
      try {
        const trades = closedPositions ?? [];
        const markers = trades
          .filter((t) => t.symbol === symbol && t.entry_time)
          .flatMap((t) => {
            const m = [];
            if (t.entry_time) {
              m.push({
                time: toTimestamp(t.entry_time) as any,
                position: t.side === "BUY" ? "belowBar" as const : "aboveBar" as const,
                color: t.side === "BUY" ? "#22C55E" : "#EF4444",
                shape: "arrowUp" as const,
                text: `${t.side} ${t.entry_price?.toFixed(0)}`,
              });
            }
            if (t.exit_time) {
              m.push({
                time: toTimestamp(t.exit_time) as any,
                position: t.side === "BUY" ? "aboveBar" as const : "belowBar" as const,
                color: "#F59E0B",
                shape: "arrowDown" as const,
                text: `Exit ${t.exit_price?.toFixed(0)}`,
              });
            }
            return m;
          })
          .sort((a, b) => (a.time as number) - (b.time as number));

        if (markers.length) {
          (candleSeriesRef.current as any)?.setMarkers?.(markers);
        }
      } catch {
        // trades might not be available
      }

      chartRef.current?.timeScale().fitContent();
    } catch (e: any) {
      setError(e.message);
    }
  }, [candles, closedPositions, symbol]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      {error && <div className="text-red-400 text-sm mb-2">{error}</div>}
      {rangeLabel && <div className="text-xs text-gray-400 mb-2">{rangeLabel}</div>}
      <div ref={containerRef} />
    </div>
  );
}
