import { useState, useCallback, useEffect, useRef } from "react";
import { usePolling } from "../../hooks/usePolling";
import { getStatus, startBot, stopBot, resetPaperAccount } from "../../api/bot";
import StatusBadge from "../shared/StatusBadge";

export default function BotControlPanel() {
  const fetcher = useCallback(() => getStatus(1500), []);
  const { data: status, error, refresh } = usePolling(fetcher, 1500, "bot_status");
  const [loading, setLoading] = useState(false);
  const wasRunningRef = useRef(false);
  const lastRunningAtRef = useRef<number | null>(null);
  const wasOfflineRef = useRef(false);
  const autoStartInFlightRef = useRef(false);
  const autoStartAttemptedForRunRef = useRef<string | null>(null);
  const toErrText = (e: unknown) => (e instanceof Error ? e.message : String(e));

  const handleStart = async () => {
    setLoading(true);
    try {
      await startBot();
      refresh();
    } catch (e) {
      alert(`Start failed: ${toErrText(e)}`);
    }
    setLoading(false);
  };

  const handleStop = async () => {
    setLoading(true);
    try {
      await stopBot();
      refresh();
    } catch (e) {
      alert(`Stop failed: ${toErrText(e)}`);
    }
    setLoading(false);
  };

  const handleResetAccount = async () => {
    if (!confirm("Reset equity, cash buffer and daily PnL for current paper run?")) return;
    setLoading(true);
    try {
      await resetPaperAccount(status?.run_id ?? undefined);
      refresh();
    } catch (e) {
      alert(`Reset failed: ${toErrText(e)}`);
    }
    setLoading(false);
  };

  useEffect(() => {
    if (status?.running) {
      wasRunningRef.current = true;
      lastRunningAtRef.current = Date.now();
      autoStartAttemptedForRunRef.current = null;
    }
  }, [status?.running]);

  useEffect(() => {
    const offline = Boolean(error);
    if (offline) {
      wasOfflineRef.current = true;
      return;
    }

    // API came back; if bot was running before outage and is now stopped, auto-start it.
    if (
      wasOfflineRef.current &&
      status &&
      !status.running &&
      wasRunningRef.current &&
      !autoStartInFlightRef.current
    ) {
      autoStartInFlightRef.current = true;
      startBot()
        .then(() => refresh())
        .catch(() => {})
        .finally(() => {
          autoStartInFlightRef.current = false;
          wasOfflineRef.current = false;
        });
      return;
    }

    if (wasOfflineRef.current && status) {
      wasOfflineRef.current = false;
    }
  }, [error, status, refresh]);

  useEffect(() => {
    if (!status || status.running || autoStartInFlightRef.current) return;
    const rid = status.run_id ?? "none";
    if (autoStartAttemptedForRunRef.current === rid) return;

    const recentlyRunning =
      lastRunningAtRef.current !== null && Date.now() - lastRunningAtRef.current < 10 * 60 * 1000;
    const restartLikeReason =
      !status.stopped_reason || status.stopped_reason === "ungraceful_stop_or_restart";

    if (recentlyRunning && restartLikeReason) {
      autoStartAttemptedForRunRef.current = rid;
      autoStartInFlightRef.current = true;
      startBot()
        .then(() => refresh())
        .catch(() => {})
        .finally(() => {
          autoStartInFlightRef.current = false;
        });
    }
  }, [status, refresh]);

  return (
    <div className="bg-gray-900 rounded-lg p-4 border border-gray-800">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-semibold text-gray-300 uppercase tracking-wide">Bot Control</h2>
          <div className="flex items-center gap-3 mt-2">
            <StatusBadge
              label={status?.running ? "RUNNING" : "STOPPED"}
              variant={status?.running ? "green" : "red"}
            />
            {status?.run_id && (
              <span className="text-xs text-gray-500">Run: {status.run_id}</span>
            )}
          </div>
          {!status?.running && status?.stopped_reason && (
            <div className="mt-1 text-xs text-amber-400">
              Stopped reason: {status.stopped_reason}
              {status?.stopped_at ? ` (${new Date(status.stopped_at).toLocaleString()})` : ""}
            </div>
          )}
        </div>
        <div className="flex gap-2">
          <button
            onClick={handleStart}
            disabled={loading || status?.running}
            className="px-4 py-2 text-sm rounded bg-green-600 hover:bg-green-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Start
          </button>
          <button
            onClick={handleStop}
            disabled={loading || !status?.running}
            className="px-4 py-2 text-sm rounded bg-red-600 hover:bg-red-700 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Stop
          </button>
          <button
            onClick={handleResetAccount}
            disabled={loading}
            className="px-4 py-2 text-sm rounded bg-amber-700 hover:bg-amber-800 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
          >
            Reset Paper
          </button>
        </div>
      </div>
    </div>
  );
}
