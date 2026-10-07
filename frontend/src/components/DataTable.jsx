// Generic table from an array of flat objects. `columns` is optional; defaults
// to the keys of the first row. `badges` maps column -> fn(value)=>className.
export default function DataTable({ title, rows, badges = {} }) {
  const cols = rows.length ? Object.keys(rows[0]) : [];
  return (
    <div className="card">
      <h3>{title}</h3>
      {rows.length === 0 ? (
        <p className="muted">Nothing yet.</p>
      ) : (
        <div className="table-scroll">
          <table>
            <thead>
              <tr>{cols.map((c) => <th key={c}>{c}</th>)}</tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr key={i}>
                  {cols.map((c) => {
                    const val = row[c];
                    const badgeClass = badges[c]?.(val);
                    if (!badgeClass && isLongText(val)) {
                      return (
                        <td key={c} className="wrap" title={val}>
                          <span>{val}</span>
                        </td>
                      );
                    }
                    return (
                      <td key={c}>
                        {badgeClass ? (
                          <span className={`badge ${badgeClass}`}>{String(val)}</span>
                        ) : (
                          formatCell(val)
                        )}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

function isLongText(val) {
  return typeof val === "string" && val.length > 40;
}

function formatCell(val) {
  if (val === true) return "✓";
  if (val === false) return "✗";
  if (val === null || val === undefined) return "—";
  if (typeof val === "number" && !Number.isInteger(val)) return val.toFixed(1);
  return String(val);
}
