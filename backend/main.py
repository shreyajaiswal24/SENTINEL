"""SENTINEL REST API (Phase 9) — FastAPI over the shared dashboard data layer.

Exposes the same data the Streamlit view used, as JSON, for the React frontend:

    GET /api/health
    GET /api/metrics
    GET /api/runs?limit=50
    GET /api/incidents?limit=50
    GET  /api/approvals                         (pending fixes + recent decisions)
    POST /api/approvals/{id}/approve|reject     (resume one paused graph)
    POST /api/approvals/approve-all             (approve every pending fix)
    POST /api/simulate                          (inject a batch of failures)

All data access goes through dashboard.data (which goes through utils.database),
so the API owns no schema knowledge of its own.

Run it:
    uvicorn backend.main:app --reload --port 8000
"""
from __future__ import annotations

import io
import uuid

from fastapi import FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

from backend import approvals
from dashboard import data
from refine.engine import refine_file, to_bytes
from utils import database

app = FastAPI(title="SENTINEL API", version="1.0.0")

# Dev CORS: the Vite dev server runs on 5173. Tighten for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# In-memory store of refined files awaiting download, keyed by a one-shot token.
# Fine for a single-process dev server; swap for object storage in production.
_REFINED: dict[str, tuple[str, bytes]] = {}


@app.on_event("startup")
def _startup() -> None:
    database.init_db()


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "sentinel"}


@app.get("/api/metrics")
def metrics() -> dict:
    return data.metrics()


@app.get("/api/runs")
def runs(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return data.runs_table(limit=limit)


@app.get("/api/incidents")
def incidents(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    return data.incidents_table(limit=limit)


@app.get("/api/approvals")
def list_approvals() -> dict:
    return approvals.approvals_view()


def _decide(approval_id: int, approve: bool) -> dict:
    try:
        return approvals.decide(approval_id, approve)
    except approvals.AlreadyDecided as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@app.post("/api/approvals/approve-all")
def approve_all() -> dict:
    results = approvals.decide_all(True)
    return {"count": len(results), "results": results}


@app.post("/api/approvals/{approval_id}/approve")
def approve(approval_id: int) -> dict:
    return _decide(approval_id, True)


@app.post("/api/approvals/{approval_id}/reject")
def reject(approval_id: int) -> dict:
    return _decide(approval_id, False)


@app.post("/api/simulate")
def simulate() -> dict:
    if not approvals.start_simulation():
        raise HTTPException(status_code=409, detail="A simulation is already running.")
    return approvals.approvals_view()["simulation"]


@app.post("/api/refine")
async def refine(file: UploadFile = File(...)) -> dict:
    """Verify + clean an uploaded sales file (CSV/Excel).

    Returns the report plus a `download_token` for fetching the refined file and
    a small `preview` of the cleaned rows. We clean, we never fabricate.
    """
    filename = file.filename or "upload.csv"
    raw = await file.read()
    try:
        refined_df, report = refine_file(io.BytesIO(raw), filename)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — a malformed file shouldn't 500 the server
        raise HTTPException(status_code=422, detail=f"Could not parse {filename!r}: {exc}")

    # Stash the refined bytes (same format as the upload) for one-shot download.
    out_name = _refined_name(filename)
    token = uuid.uuid4().hex
    _REFINED[token] = (out_name, to_bytes(refined_df, filename))

    preview = refined_df.head(10).where(refined_df.notna(), None).to_dict(orient="records")
    result = report.to_dict()
    result.update({
        "download_token": token,
        "download_name": out_name,
        "preview": preview,
    })
    return result


@app.get("/api/refine/download/{token}")
def refine_download(token: str) -> StreamingResponse:
    """Fetch a previously-refined file by its token."""
    entry = _REFINED.get(token)
    if entry is None:
        raise HTTPException(status_code=404, detail="Unknown or expired download token.")
    name, blob = entry
    media = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
             if name.lower().endswith((".xlsx", ".xls")) else "text/csv")
    return StreamingResponse(
        io.BytesIO(blob),
        media_type=media,
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


def _refined_name(filename: str) -> str:
    dot = filename.rfind(".")
    stem, ext = (filename[:dot], filename[dot:]) if dot != -1 else (filename, ".csv")
    return f"{stem}_refined{ext}"
