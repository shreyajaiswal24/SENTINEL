// Thin client over the SENTINEL FastAPI backend.
// In dev, Vite proxies /api -> http://localhost:8000 (see vite.config.js).
// In prod, set VITE_API_BASE to the backend origin.
const BASE = import.meta.env.VITE_API_BASE ?? "";

async function get(path) {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function upload(path, file) {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE}${path}`, { method: "POST", body: form });
  if (!res.ok) {
    let msg = `${path} -> ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) msg = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(msg);
  }
  return res.json();
}

async function post(path) {
  const res = await fetch(`${BASE}${path}`, { method: "POST" });
  if (!res.ok) {
    let msg = `${path} -> ${res.status}`;
    try {
      const body = await res.json();
      if (body.detail) msg = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new Error(msg);
  }
  return res.json();
}

export const api = {
  metrics: () => get("/api/metrics"),
  runs: (limit = 50) => get(`/api/runs?limit=${limit}`),
  incidents: (limit = 50) => get(`/api/incidents?limit=${limit}`),
  approvals: () => get("/api/approvals"),
  approve: (id) => post(`/api/approvals/${id}/approve`),
  reject: (id) => post(`/api/approvals/${id}/reject`),
  approveAll: () => post("/api/approvals/approve-all"),
  simulate: () => post("/api/simulate"),
  refine: (file) => upload("/api/refine", file),
  refineDownloadUrl: (token) => `${BASE}/api/refine/download/${token}`,
};
