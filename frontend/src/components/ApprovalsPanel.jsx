import { useState } from "react";
import { api } from "../api";

const OUTCOME_BADGE = { resolved: "ok", unresolved: "crit", rejected: "medium", error: "crit" };

// Medium/high-risk fixes wait here until a human clicks Approve or Reject.
// Critical incidents never appear: they escalate without any fix.
export default function ApprovalsPanel({ data, onChange }) {
  const [busy, setBusy] = useState({}); // approval_id | "all" | "sim" -> true
  const [error, setError] = useState(null);

  const pending = data?.pending ?? [];
  const recent = data?.recent ?? [];
  const sim = data?.simulation;

  const act = async (key, fn) => {
    setBusy((b) => ({ ...b, [key]: true }));
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy((b) => ({ ...b, [key]: false }));
      onChange();
    }
  };

  const anyBusy = Object.values(busy).some(Boolean);

  return (
    <div className="card approvals">
      <div className="approvals-head">
        <h3>
          🛑 Waiting for your approval
          <span className={`badge ${pending.length ? "high" : "ok"}`}>{pending.length}</span>
        </h3>
        <div className="approvals-actions">
          <button
            className="ghost"
            disabled={sim?.running || busy.sim}
            onClick={() => act("sim", api.simulate)}
          >
            {sim?.running ? `Simulating… ${sim.done}/${sim.total}` : "⚡ Simulate failures"}
          </button>
          <button
            className="approve"
            disabled={!pending.length || anyBusy}
            onClick={() => act("all", api.approveAll)}
          >
            {busy.all ? "Fixing…" : `✓ Approve all (${pending.length})`}
          </button>
        </div>
      </div>
      <p className="muted approvals-help">
        Low-risk failures are fixed automatically. Medium and high-risk fixes pause
        here for a human. Critical failures are escalated and never auto-fixed.
      </p>

      {error && <div className="badge crit refine-error">Error: {error}</div>}
      {sim?.error && <div className="badge crit refine-error">Simulation error: {sim.error}</div>}

      {pending.length === 0 ? (
        <p className="muted">Nothing waiting. Click “Simulate failures” to inject some.</p>
      ) : (
        <div className="approval-list">
          {pending.map((a) => (
            <div key={a.approval_id} className="approval-card">
              <div className="approval-title">
                <b>{a.pipeline_name}</b>
                <span className="muted">· {a.failure_type}</span>
                <span className={`badge ${a.risk_level}`}>{a.risk_level}</span>
                {a.confidence != null && (
                  <span className="muted">confidence {Math.round(a.confidence * 100)}%</span>
                )}
                <span className="muted approval-time">
                  run #{a.run_id} · {new Date(a.created_at).toLocaleTimeString()}
                </span>
              </div>
              <div className="approval-body">
                <div><span className="muted">Cause:</span> {a.root_cause}</div>
                <div><span className="muted">Proposed fix:</span> {a.fix_plan}</div>
              </div>
              <div className="approval-buttons">
                <button
                  className="approve"
                  disabled={anyBusy}
                  onClick={() => act(a.approval_id, () => api.approve(a.approval_id))}
                >
                  {busy[a.approval_id] ? "Fixing…" : "✓ Approve fix"}
                </button>
                <button
                  className="reject"
                  disabled={anyBusy}
                  onClick={() => act(a.approval_id, () => api.reject(a.approval_id))}
                >
                  ✕ Reject
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {recent.length > 0 && (
        <>
          <div className="muted refine-preview-label">Recent decisions</div>
          <div className="table-scroll">
            <table>
              <thead>
                <tr>
                  <th>pipeline</th><th>failure</th><th>risk</th><th>decision</th>
                  <th>outcome</th><th>incident</th><th>when</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((a) => (
                  <tr key={a.approval_id}>
                    <td>{a.pipeline_name}</td>
                    <td>{a.failure_type}</td>
                    <td><span className={`badge ${a.risk_level}`}>{a.risk_level}</span></td>
                    <td>{a.status}</td>
                    <td>
                      <span className={`badge ${OUTCOME_BADGE[a.outcome] || "low"}`}>
                        {a.outcome || "…"}
                      </span>
                    </td>
                    <td>{a.incident_id ? `#${a.incident_id}` : "—"}</td>
                    <td className="muted">
                      {a.decided_at ? new Date(a.decided_at).toLocaleTimeString() : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
