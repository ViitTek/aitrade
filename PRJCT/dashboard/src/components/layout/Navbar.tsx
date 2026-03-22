import { NavLink } from "react-router-dom";
import { usePolling } from "../../hooks/usePolling";
import { getStatus } from "../../api/bot";
import StatusBadge from "../shared/StatusBadge";
import { useCallback, useEffect, useRef, useState } from "react";

const links = [
  { to: "/", label: "Dashboard" },
  { to: "/trades", label: "Trades" },
  { to: "/chart", label: "Chart" },
  { to: "/config", label: "Config" },
  { to: "/sentiment", label: "Sentiment" },
  { to: "/market-data", label: "Market Data" },
];

export default function Navbar() {
  const fetcher = useCallback(() => getStatus(1500), []);
  const { data: status, error } = usePolling(fetcher, 1500, "navbar_status");
  const wasOfflineRef = useRef(false);
  const [reconnectedAt, setReconnectedAt] = useState<number | null>(null);
  const [offlineAt, setOfflineAt] = useState<number | null>(null);

  useEffect(() => {
    const offline = Boolean(error);
    if (offline) {
      if (!wasOfflineRef.current) {
        setOfflineAt(Date.now());
      }
      wasOfflineRef.current = true;
      return;
    }
    if (wasOfflineRef.current && status) {
      wasOfflineRef.current = false;
      setOfflineAt(null);
      setReconnectedAt(Date.now());
      window.dispatchEvent(new Event("api:refresh"));
    }
  }, [error, status]);

  return (
    <nav className="bg-gray-900 border-b border-gray-800 px-4 py-2 flex items-center gap-6">
      <span className="text-lg font-bold text-blue-400 mr-4">AIInvest</span>

      {links.map((l) => (
        <NavLink
          key={l.to}
          to={l.to}
          className={({ isActive }) =>
            `text-sm px-2 py-1 rounded transition-colors ${
              isActive ? "bg-gray-800 text-white" : "text-gray-400 hover:text-white"
            }`
          }
        >
          {l.label}
        </NavLink>
      ))}

      <div className="ml-auto flex items-center gap-3">
        {error && (
          <StatusBadge label="API OFFLINE" variant="red" />
        )}
        {!error && reconnectedAt && Date.now() - reconnectedAt < 15000 && (
          <StatusBadge label="API RECONNECTED" variant="green" />
        )}
        {status && (
          <>
            <StatusBadge
              label={status.running ? "RUNNING" : "STOPPED"}
              variant={status.running ? "green" : "red"}
            />
            {status.run_id && (
              <span className="text-xs text-gray-500">{status.run_id}</span>
            )}
          </>
        )}
      </div>

      {offlineAt && (
        <div className="fixed right-4 top-4 z-50 rounded border border-amber-700 bg-amber-950/95 px-3 py-2 text-xs text-amber-300 shadow-lg">
          API reconnecting...
        </div>
      )}
      {!error && reconnectedAt && Date.now() - reconnectedAt < 6000 && (
        <div className="fixed right-4 top-4 z-50 rounded border border-emerald-700 bg-emerald-950/95 px-3 py-2 text-xs text-emerald-300 shadow-lg">
          API reconnected
        </div>
      )}
    </nav>
  );
}

