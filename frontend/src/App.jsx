import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import MetricCard from "./components/MetricCard";
import BarList from "./components/BarList";
import DataTable from "./components/DataTable";
import RefinePanel from "./components/RefinePanel";
import ApprovalsPanel from "./components/ApprovalsPanel";

const REFRESH_MS = 5000;
const FAST_REFRESH_MS = 1500; // while a simulation is running

export default function App() {
  const [metrics, setMetrics] = useState(null);
  const [runs, setRuns] = useState([]);
  const [incidents, setIncidents] = useState([]);
  const [approvals, setApprovals] = useState(null);
  const [error, setError] = useState(null);
  const [updatedAt, setUpdatedAt] = useState(null);

  const load = useCallback(async () => {
    try {
      const [m, r, i, a] = await Promise.all([
        api.metrics(),
        api.runs(50),
        api.incidents(50),
        api.approvals(),
      ]);
      setApprovals(a);
      setMetrics(m);
      setRuns(r);
      setIncidents(i);
      setError(null);
      setUpdatedAt(new Date());
    } catch (e) {
      setError(e.message);
    }
  }, []);

  const simRunning = approvals?.simulation?.running;
  useEffect(() => {
    load();
    const id = setInterval(load, simRunning ? FAST_REFRESH_MS : REFRESH_MS);
    return () => clearInterval(id);
  }, [load, simRunning]);

  const avgMttr = metrics?.avg_mttr;

  return (
    <div className="app">
      <header>
        <h1>🛡️ SENTINEL</h1>
        <span className="subtitle">Self-Healing Pipeline Monitor</span>
        <div className="status">
          {error ? (
            <span className="badge crit">API offline: {error}</span>
          ) : (
            <span className="muted">
              live · updated {updatedAt ? updatedAt.toLocaleTimeString() : "…"}
            </span>
          )}
          <button onClick={load}>↻ Refresh</button>
        </div>
      </header>

      <section className="metrics">
        <MetricCard label="Incidents" value={metrics?.total ?? "—"} />
        <MetricCard label="Resolved" value={metrics?.resolved ?? "—"} accent="#22c55e" />
        <MetricCard
          label="Resolution rate"
          value={metrics ? `${Math.round(metrics.resolution_rate * 100)}%` : "—"}
        />
        <MetricCard
          label="Avg MTTR"
          value={avgMttr != null ? formatDuration(avgMttr) : "—"}
        />
      </section>

      <section className="approvals-section">
        <ApprovalsPanel data={approvals} onChange={load} />
      </section>

      <section className="charts">
        <BarList title="Failures by type" data={metrics?.by_type} />
        <BarList title="Failures by risk" data={metrics?.by_risk} colorByKey />
      </section>

      <section className="refine-section">
        <RefinePanel />
      </section>

      <section className="tables">
        <DataTable
          title="Recent incidents"
          rows={incidents}
          badges={{
            risk: (v) => v,
            resolved: (v) => (v ? "ok" : "crit"),
          }}
        />
        <DataTable
          title="Recent pipeline runs"
          rows={runs}
          badges={{ status: (v) => (v === "success" ? "ok" : "crit") }}
        />
      </section>
    </div>
  );
}

// 641763.6 -> "7.4d": raw seconds overflow the card and are hard to read.
function formatDuration(sec) {
  if (sec < 60) return `${sec.toFixed(1)}s`;
  if (sec < 3600) return `${(sec / 60).toFixed(1)}m`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)}h`;
  return `${(sec / 86400).toFixed(1)}d`;
}
