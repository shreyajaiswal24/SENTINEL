// Renders a {label: count} map as horizontal bars — no chart library needed.
const RISK_COLORS = {
  low: "#22c55e",
  medium: "#eab308",
  high: "#f97316",
  critical: "#ef4444",
};

export default function BarList({ title, data, colorByKey }) {
  const entries = Object.entries(data || {});
  const max = Math.max(1, ...entries.map(([, v]) => v));
  return (
    <div className="card">
      <h3>{title}</h3>
      {entries.length === 0 && <p className="muted">No data yet.</p>}
      {entries.map(([key, value]) => (
        <div className="bar-row" key={key}>
          <span className="bar-label">{key}</span>
          <div className="bar-track">
            <div
              className="bar-fill"
              style={{
                width: `${(value / max) * 100}%`,
                background: colorByKey ? RISK_COLORS[key] || "#6366f1" : "#6366f1",
              }}
            />
          </div>
          <span className="bar-value">{value}</span>
        </div>
      ))}
    </div>
  );
}
