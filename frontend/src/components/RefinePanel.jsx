import { useState } from "react";
import { api } from "../api";

const SEV_BADGE = { info: "low", warning: "medium", high: "critical" };

export default function RefinePanel() {
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  // One entry per uploaded file: { name, status: "pending"|"done"|"error", report?, error? }
  const [results, setResults] = useState([]);

  const onPick = (e) => {
    setFiles(Array.from(e.target.files ?? []));
    setResults([]);
  };

  const onRefine = async () => {
    if (!files.length) return;
    setBusy(true);
    setResults(files.map((f) => ({ name: f.name, status: "pending" })));
    // Sequential, so each file's result appears as soon as it's ready.
    for (let i = 0; i < files.length; i++) {
      let entry;
      try {
        entry = { name: files[i].name, status: "done", report: await api.refine(files[i]) };
      } catch (e) {
        entry = { name: files[i].name, status: "error", error: e.message };
      }
      setResults((prev) => prev.map((r, j) => (j === i ? entry : r)));
    }
    setBusy(false);
  };

  const label = busy
    ? "Refining…"
    : files.length > 1
      ? `Verify & refine ${files.length} files`
      : "Verify & refine";

  return (
    <div className="card refine">
      <h3>📤 Refine your data</h3>
      <p className="muted refine-help">
        Upload one or more sales files (CSV or Excel). SENTINEL verifies each one —
        duplicates, missing values, column/schema issues, and volume — and returns
        a cleaned file. It cleans; it never invents data.
      </p>

      <div className="refine-controls">
        <input type="file" accept=".csv,.xlsx,.xls" multiple onChange={onPick} />
        <button onClick={onRefine} disabled={!files.length || busy}>
          {label}
        </button>
      </div>

      {results.length > 0 && (
        <div className="refine-files">
          {results.map((r, i) => (
            <FileResult key={`${r.name}-${i}`} result={r} defaultOpen={results.length === 1} />
          ))}
        </div>
      )}
    </div>
  );
}

function FileResult({ result, defaultOpen }) {
  const { name, status, report, error } = result;
  return (
    <details className="refine-file" open={defaultOpen}>
      <summary>
        <b>{name}</b>
        {status === "pending" && <span className="muted">refining…</span>}
        {status === "error" && <span className="badge crit">Error: {error}</span>}
        {status === "done" && (
          <>
            <span className={`badge ${report.clean ? "ok" : "medium"}`}>
              {report.clean ? "Clean" : `${report.issues.length} issue(s) found & fixed`}
            </span>
            <span className="muted">
              {report.original_rows} rows → <b>{report.refined_rows}</b>
              {report.rows_removed > 0 && ` (removed ${report.rows_removed})`}
            </span>
          </>
        )}
      </summary>
      {status === "done" && <ReportDetail report={report} />}
    </details>
  );
}

function ReportDetail({ report }) {
  const previewCols = report.preview?.length ? Object.keys(report.preview[0]) : [];
  return (
    <div className="refine-result">
      <div className="refine-summary">
        <a
          className="download-btn"
          href={api.refineDownloadUrl(report.download_token)}
          download={report.download_name}
        >
          ⬇ Download {report.download_name}
        </a>
      </div>

      <table className="refine-issues">
        <thead>
          <tr>
            <th>Check</th>
            <th>Severity</th>
            <th>What we found</th>
            <th>Action</th>
          </tr>
        </thead>
        <tbody>
          {report.issues.length === 0 ? (
            <tr>
              <td colSpan={4} className="muted">
                No issues — your data is already clean. ✅
              </td>
            </tr>
          ) : (
            report.issues.map((it, idx) => (
              <tr key={idx}>
                <td>{it.check}</td>
                <td>
                  <span className={`badge ${SEV_BADGE[it.severity] || "low"}`}>
                    {it.severity}
                  </span>
                </td>
                <td>{it.detail}</td>
                <td className="muted">{it.action}</td>
              </tr>
            ))
          )}
        </tbody>
      </table>

      {report.preview?.length > 0 && (
        <>
          <div className="muted refine-preview-label">
            Preview of refined data (first {report.preview.length} rows)
          </div>
          <div className="refine-preview-scroll">
            <table>
              <thead>
                <tr>
                  {previewCols.map((c) => (
                    <th key={c}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {report.preview.map((row, i) => (
                  <tr key={i}>
                    {previewCols.map((c) => (
                      <td key={c}>{row[c] == null ? "" : String(row[c])}</td>
                    ))}
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
