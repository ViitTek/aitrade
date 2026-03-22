import { useCallback } from "react";
import { getDataCoverage, getMarketData, getTradedSymbols, type DataCoverageRow, type MarketMetrics } from "../api/bot";
import { usePolling } from "../hooks/usePolling";

const ASSET_LONG_NAMES: Record<string, string> = {
  BTC: "Bitcoin",
  ETH: "Ethereum",
  SOL: "Solana",
  XRP: "Ripple",
  DOGE: "Dogecoin",
  BNB: "BNB",
  TRX: "TRON",
  PAXG: "PAX Gold",
  USDC: "USD Coin",
};

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="bg-gray-900 rounded-lg border border-gray-800 p-4">
      <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wide mb-3">
        {title}
      </h3>
      {children}
    </div>
  );
}

function NoData() {
  return <p className="text-xs text-gray-600">N/A</p>;
}

function formatNum(n: number | null | undefined, decimals = 2): string {
  if (n == null) return "N/A";
  if (Math.abs(n) >= 1e9) return `$${(n / 1e9).toFixed(1)}B`;
  if (Math.abs(n) >= 1e6) return `$${(n / 1e6).toFixed(1)}M`;
  if (Math.abs(n) >= 1e3) return `$${(n / 1e3).toFixed(1)}K`;
  return n.toFixed(decimals);
}

function frColor(rate: number): string {
  if (Math.abs(rate) > 0.01) return rate > 0 ? "text-red-400" : "text-green-400";
  if (Math.abs(rate) > 0.001) return rate > 0 ? "text-yellow-400" : "text-blue-400";
  return "text-gray-300";
}

function symbolLabel(sym: string): { base: string; long: string } {
  const base = sym.split("/")[0]?.toUpperCase() || sym;
  return { base, long: ASSET_LONG_NAMES[base] || base };
}

function covColor(v: number): string {
  if (v >= 80) return "text-green-400";
  if (v >= 40) return "text-yellow-400";
  return "text-red-400";
}

function staleColor(hours: number | null | undefined): string {
  if (hours == null) return "text-gray-500";
  if (hours <= 2) return "text-green-400";
  if (hours <= 12) return "text-yellow-400";
  return "text-red-400";
}

function CoverageCard({ rows, days, tf }: { rows: DataCoverageRow[]; days: number; tf: number }) {
  if (!rows || rows.length === 0) return <Card title="0. Data Coverage"><NoData /></Card>;
  return (
    <Card title="0. Data Coverage (Intel/Funding/Sentiment/News)">
      <div className="space-y-1.5 max-h-64 overflow-auto pr-1">
        {rows.map((r) => {
          const lbl = symbolLabel(r.symbol);
          return (
            <div key={r.symbol} className="flex items-center justify-between text-xs">
              <span className="text-gray-300">
                <span className="font-mono">{lbl.base}</span>
                <span className="text-gray-500"> - {lbl.long}</span>
              </span>
              <span className="text-gray-500">H {r.candle_hours}</span>
              <span className={covColor(r.intel_coverage_pct)}>I {r.intel_coverage_pct.toFixed(0)}%</span>
              <span className={covColor(r.funding_coverage_pct)}>F {r.funding_coverage_pct.toFixed(0)}%</span>
              <span className={covColor(r.sentiment_coverage_pct)}>S {r.sentiment_coverage_pct.toFixed(0)}%</span>
              <span className={staleColor(r.news_staleness_h)}>N {r.news_staleness_h == null ? "N/A" : `${r.news_staleness_h.toFixed(1)}h`}</span>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-gray-600 mt-2">
        Lookback {days}d, tf={tf}m, I=intel, F=funding, S=sentiment coverage, N=news staleness.
      </p>
    </Card>
  );
}

// â”€â”€â”€ 1. Funding Rates â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function FundingRatesCard({ data }: { data: MarketMetrics }) {
  const rates = data.funding_rates;
  if (!rates || Object.keys(rates).length === 0) return <Card title="1. Funding Rates"><NoData /></Card>;

  return (
    <Card title="1. Funding Rates">
      <div className="space-y-1.5">
        {Object.entries(rates).map(([sym, rate]) => {
          const pct = (rate * 100).toFixed(4);
          const lbl = symbolLabel(sym);
          return (
            <div key={sym} className="flex items-center justify-between">
              <span className="text-xs text-gray-300">
                <span className="font-mono">{lbl.base}</span>
                <span className="text-gray-500"> - {lbl.long}</span>
              </span>
              <div className="flex items-center gap-2">
                <div className="w-20 bg-gray-800 rounded-full h-1.5 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${rate > 0 ? "bg-red-500" : "bg-green-500"}`}
                    style={{ width: `${Math.min(Math.abs(rate) / 0.01 * 100, 100)}%` }}
                  />
                </div>
                <span className={`text-xs font-mono w-16 text-right ${frColor(rate)}`}>
                  {rate > 0 ? "+" : ""}{pct}%
                </span>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-gray-600 mt-2">ExtrĂ©mnĂ­ FR (&gt;1%) = overleveraged, reversal risk</p>
    </Card>
  );
}

// â”€â”€â”€ 2. Open Interest â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function OpenInterestCard({ data }: { data: MarketMetrics }) {
  const oi = data.open_interest;
  if (!oi || Object.keys(oi).length === 0) return <Card title="2. Open Interest"><NoData /></Card>;

  return (
    <Card title="2. Open Interest">
      <div className="space-y-1.5">
        {Object.entries(oi).map(([sym, info]) => (
          <div key={sym} className="flex items-center justify-between">
            <span className="text-xs text-gray-300 font-mono">{sym.split("/")[0]}</span>
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-400">{info.oi.toLocaleString()} coins</span>
              <span className="text-xs text-gray-300 font-semibold">
                {formatNum(info.oi_usdt)}
              </span>
            </div>
          </div>
        ))}
      </div>
      <p className="text-[10px] text-gray-600 mt-2">RostoucĂ­ OI + breakout = silnĂ˝ trend</p>
    </Card>
  );
}

// â”€â”€â”€ 3. Long/Short Ratio â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function LongShortRatioCard({ data }: { data: MarketMetrics }) {
  const ls = data.long_short_ratio;
  if (!ls || Object.keys(ls).length === 0) return <Card title="3. Long/Short Ratio"><NoData /></Card>;

  return (
    <Card title="3. Long/Short Ratio">
      <div className="space-y-2">
        {Object.entries(ls).map(([sym, info]) => {
          const isLongDominant = info.ratio > 1.2;
          const isShortDominant = info.ratio < 0.8;
          return (
            <div key={sym}>
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-xs text-gray-300 font-mono">{sym.split("/")[0]}</span>
                <span className={`text-xs font-semibold ${isLongDominant ? "text-green-400" : isShortDominant ? "text-red-400" : "text-gray-400"}`}>
                  {info.ratio}x
                </span>
              </div>
              <div className="flex h-2 rounded-full overflow-hidden bg-gray-800">
                <div className="bg-green-600 transition-all" style={{ width: `${info.long_pct}%` }} />
                <div className="bg-red-600 transition-all" style={{ width: `${info.short_pct}%` }} />
              </div>
              <div className="flex justify-between mt-0.5">
                <span className="text-[9px] text-green-500">Long {info.long_pct}%</span>
                <span className="text-[9px] text-red-500">Short {info.short_pct}%</span>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-gray-600 mt-2">Ratio &gt;1.5 = overleveraged longs, reversal risk</p>
    </Card>
  );
}

// â”€â”€â”€ 4. Order Book â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function OrderBookCard({ data }: { data: MarketMetrics }) {
  const ob = data.order_book;
  if (!ob || Object.keys(ob).length === 0) return <Card title="4. Order Book Depth"><NoData /></Card>;

  return (
    <Card title="4. Order Book Depth">
      <div className="space-y-2">
        {Object.entries(ob).map(([sym, info]) => {
          const total = info.bid_vol + info.ask_vol;
          const bidPct = total > 0 ? (info.bid_vol / total) * 100 : 50;
          return (
            <div key={sym}>
              <div className="flex items-center justify-between mb-0.5">
                <span className="text-xs text-gray-300 font-mono">{sym.split("/")[0]}</span>
                <span className={`text-xs font-semibold ${info.imbalance > 1.2 ? "text-green-400" : info.imbalance < 0.8 ? "text-red-400" : "text-gray-400"}`}>
                  {info.imbalance.toFixed(2)}x
                </span>
              </div>
              <div className="flex h-2 rounded-full overflow-hidden bg-gray-800">
                <div className="bg-green-600 transition-all" style={{ width: `${bidPct}%` }} />
                <div className="bg-red-600 transition-all" style={{ width: `${100 - bidPct}%` }} />
              </div>
              <div className="flex justify-between mt-0.5">
                <span className="text-[9px] text-green-500">Bid {info.bid_vol.toFixed(2)}</span>
                <span className="text-[9px] text-red-500">Ask {info.ask_vol.toFixed(2)}</span>
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-[10px] text-gray-600 mt-2">Imbalance &gt;1.2 = buying pressure</p>
    </Card>
  );
}

// â”€â”€â”€ 5. Exchange Flow â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function ExchangeFlowCard({ data }: { data: MarketMetrics }) {
  const flow = data.exchange_flow;
  return (
    <Card title="5. On-Chain / Exchange Flow">
      {flow?.btc_total_supply != null ? (
        <div>
          <div className="flex items-baseline gap-2">
            <span className="text-2xl font-bold text-gray-200">
              {(flow.btc_total_supply / 1e6).toFixed(2)}M
            </span>
            <span className="text-xs text-gray-500">BTC total supply</span>
          </div>
          <p className="text-[10px] text-gray-600 mt-2">VelkĂ˝ inflow na exchange = sell pressure</p>
        </div>
      ) : (
        <NoData />
      )}
    </Card>
  );
}

// â”€â”€â”€ 6. BTC Dominance â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function BtcDominanceCard({ data }: { data: MarketMetrics }) {
  const dom = data.btc_dominance;
  if (dom == null) return <Card title="6. BTC Dominance"><NoData /></Card>;

  const isAltseason = dom < 50;
  return (
    <Card title="6. BTC Dominance">
      <div className="flex items-baseline gap-3">
        <span className="text-3xl font-bold text-gray-200">{dom}%</span>
        <span className={`text-xs font-bold px-2 py-0.5 rounded ${isAltseason ? "bg-purple-900 text-purple-300" : "bg-orange-900 text-orange-300"}`}>
          {isAltseason ? "ALTSEASON" : "BTC SEASON"}
        </span>
      </div>
      <div className="mt-2 w-full bg-gray-800 rounded-full h-3 overflow-hidden">
        <div className="bg-orange-500 h-full transition-all" style={{ width: `${dom}%` }} />
      </div>
      <div className="flex justify-between mt-1">
        <span className="text-[9px] text-orange-400">BTC {dom}%</span>
        <span className="text-[9px] text-purple-400">Alts {(100 - dom).toFixed(1)}%</span>
      </div>
    </Card>
  );
}

// â”€â”€â”€ 7. Stablecoin Supply â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function StablecoinCard({ data }: { data: MarketMetrics }) {
  const sc = data.stablecoin_mcap;
  if (!sc) return <Card title="7. Stablecoin Supply"><NoData /></Card>;

  return (
    <Card title="7. Stablecoin Supply">
      <div className="space-y-2">
        <div className="flex items-baseline gap-2">
          <span className="text-2xl font-bold text-gray-200">{formatNum(sc.total)}</span>
          <span className="text-xs text-gray-500">celkem</span>
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div className="bg-gray-800 rounded p-2">
            <span className="text-[10px] text-gray-500">USDT</span>
            <p className="text-sm font-semibold text-green-400">{formatNum(sc.USDT)}</p>
          </div>
          <div className="bg-gray-800 rounded p-2">
            <span className="text-[10px] text-gray-500">USDC</span>
            <p className="text-sm font-semibold text-blue-400">{formatNum(sc.USDC)}</p>
          </div>
        </div>
      </div>
      <p className="text-[10px] text-gray-600 mt-2">RostoucĂ­ supply = novĂ˝ kapitĂˇl v krypto</p>
    </Card>
  );
}

// â”€â”€â”€ 8. TradFi â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

function TradFiCard({ data }: { data: MarketMetrics }) {
  const tf = data.tradfi;
  if (!tf || (!tf.sp500 && !tf.dxy)) return <Card title="8. TradFi Korelace"><NoData /></Card>;

  return (
    <Card title="8. TradFi Korelace">
      <div className="grid grid-cols-2 gap-3">
        {tf.sp500 != null && (
          <div className="bg-gray-800 rounded p-3">
            <span className="text-[10px] text-gray-500">S&P 500</span>
            <p className="text-lg font-bold text-gray-200">{tf.sp500.toLocaleString()}</p>
            {tf.sp500_change != null && (
              <span className={`text-xs font-semibold ${tf.sp500_change >= 0 ? "text-green-400" : "text-red-400"}`}>
                {tf.sp500_change >= 0 ? "+" : ""}{tf.sp500_change}%
              </span>
            )}
          </div>
        )}
        {tf.dxy != null && (
          <div className="bg-gray-800 rounded p-3">
            <span className="text-[10px] text-gray-500">DXY (Dollar)</span>
            <p className="text-lg font-bold text-gray-200">{tf.dxy}</p>
            {tf.dxy_change != null && (
              <span className={`text-xs font-semibold ${tf.dxy_change >= 0 ? "text-red-400" : "text-green-400"}`}>
                {tf.dxy_change >= 0 ? "+" : ""}{tf.dxy_change}%
              </span>
            )}
            <p className="text-[9px] text-gray-600 mt-0.5">SilnĂ˝ $ = medvÄ›dĂ­ pro krypto</p>
          </div>
        )}
      </div>
    </Card>
  );
}

// â”€â”€â”€ Page â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

export default function MarketDataPage() {
  const fetcher = useCallback(() => getMarketData(), []);
  const { data, error } = usePolling(fetcher, 30000, "market_data");
  const covFetcher = useCallback(() => getDataCoverage(60, 60), []);
  const { data: covData } = usePolling(covFetcher, 60000, "data_coverage:60:60");
  const { data: traded } = usePolling(() => getTradedSymbols(undefined, 720), 60000, "traded_symbols:720");

  const metrics = data?.latest;
  const tradedSet = new Set((traded?.symbols ?? []).map((s) => s.toUpperCase()));
  const filterByTraded = <T extends Record<string, any>>(obj?: T): T =>
    Object.fromEntries(
      Object.entries(obj ?? {}).filter(([k]) => tradedSet.size === 0 || tradedSet.has(k.toUpperCase()))
    ) as T;
  const filteredMetrics = metrics
    ? {
        ...metrics,
        funding_rates: filterByTraded(metrics.funding_rates),
        open_interest: filterByTraded(metrics.open_interest),
        long_short_ratio: filterByTraded(metrics.long_short_ratio),
        order_book: filterByTraded(metrics.order_book),
      }
    : null;
  const age = metrics?.timestamp
    ? Math.round((Date.now() - new Date(metrics.timestamp).getTime()) / 60000)
    : null;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold">Market Data</h1>
        <div className="flex items-center gap-3">
          {age !== null && (
            <span className="text-xs text-gray-500">
              {age < 60 ? `${age}m ago` : `${Math.round(age / 60)}h ago`}
            </span>
          )}
          {error && <span className="text-xs text-red-400">{error}</span>}
        </div>
      </div>

      {!filteredMetrics ? (
        <div className="bg-gray-900 rounded-lg border border-gray-800 p-8 text-center">
          <p className="text-gray-500 text-sm">
            Zatím žádná data. Po startu projektu probíhá automatický sběr na pozadí, první data se zobrazí po prvním cyklu.
          </p>
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <FundingRatesCard data={filteredMetrics} />
          <OpenInterestCard data={filteredMetrics} />
          <LongShortRatioCard data={filteredMetrics} />
          <OrderBookCard data={filteredMetrics} />
          <CoverageCard rows={(covData?.symbols ?? []).filter((x) => tradedSet.size === 0 || tradedSet.has(x.symbol.toUpperCase()))} days={covData?.lookback_days ?? 60} tf={covData?.tf ?? 60} />
          <ExchangeFlowCard data={filteredMetrics} />
          <StablecoinCard data={filteredMetrics} />
          <TradFiCard data={filteredMetrics} />
          <BtcDominanceCard data={filteredMetrics} />
        </div>
      )}
    </div>
  );
}

