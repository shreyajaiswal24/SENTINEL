export default function MetricCard({ label, value, accent }) {
  return (
    <div className="card metric">
      <div className="metric-value" style={accent ? { color: accent } : undefined}>
        {value}
      </div>
      <div className="metric-label">{label}</div>
    </div>
  );
}
