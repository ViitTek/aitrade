import BotControlPanel from "../components/dashboard/BotControlPanel";
import AutoTunePanel from "../components/dashboard/AutoTunePanel";
import EquityCurveChart from "../components/dashboard/EquityCurveChart";
import PortfolioSummary from "../components/dashboard/PortfolioSummary";
import OpenPositionsTable from "../components/dashboard/OpenPositionsTable";
import RecommendedAssetsPanel from "../components/dashboard/RecommendedAssetsPanel";
import RecentSignalsPanel from "../components/dashboard/RecentSignalsPanel";

export default function DashboardPage() {
  return (
    <div className="space-y-4">
      <BotControlPanel />
      <RecommendedAssetsPanel />
      <AutoTunePanel />
      <PortfolioSummary />
      <EquityCurveChart />
      <OpenPositionsTable />
      <RecentSignalsPanel />
    </div>
  );
}
