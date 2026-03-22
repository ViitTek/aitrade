import { Suspense, lazy } from "react";
import { Routes, Route } from "react-router-dom";
import Navbar from "./components/layout/Navbar";

const DashboardPage = lazy(() => import("./pages/DashboardPage"));
const TradesPage = lazy(() => import("./pages/TradesPage"));
const ChartPage = lazy(() => import("./pages/ChartPage"));
const BacktestPage = lazy(() => import("./pages/BacktestPage"));
const ConfigPage = lazy(() => import("./pages/ConfigPage"));
const SentimentPage = lazy(() => import("./pages/SentimentPage"));
const MarketDataPage = lazy(() => import("./pages/MarketDataPage"));

export default function App() {
  return (
    <div className="min-h-screen bg-gray-950 text-gray-100">
      <Navbar />
      <main className="max-w-7xl mx-auto px-4 py-6">
        <Suspense
          fallback={
            <div className="rounded-lg border border-gray-800 bg-gray-900 p-6 text-sm text-gray-400">
              Loading page...
            </div>
          }
        >
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/trades" element={<TradesPage />} />
            <Route path="/chart" element={<ChartPage />} />
            <Route path="/backtest" element={<BacktestPage />} />
            <Route path="/config" element={<ConfigPage />} />
            <Route path="/sentiment" element={<SentimentPage />} />
            <Route path="/market-data" element={<MarketDataPage />} />
          </Routes>
        </Suspense>
      </main>
    </div>
  );
}
