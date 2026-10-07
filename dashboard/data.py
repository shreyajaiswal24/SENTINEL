"""Dashboard data access (Phase 9) — streamlit-free so it's unit-testable.

Shapes the rows from utils.database into plain dicts the UI renders. Keeping this
separate from app.py means the data layer can be tested without importing
streamlit or starting a server.
"""
from __future__ import annotations

from typing import Any

from utils import database


def metrics() -> dict[str, Any]:
    return database.incident_stats()


def runs_table(limit: int = 50) -> list[dict[str, Any]]:
    return [
        {
            "run": r["run_id"],
            "pipeline": r["pipeline_name"],
            "status": r["status"],
            "rows": r["rows_actual"],
            "expected": r["rows_expected"],
            "time(s)": r["execution_time"],
            "injected": r["injected_failure"] or "—",
        }
        for r in database.recent_runs(limit=limit)
    ]


def incidents_table(limit: int = 50) -> list[dict[str, Any]]:
    return [
        {
            "id": r["incident_id"],
            "pipeline": r["pipeline_name"],
            "failure": r["failure_type"],
            "risk": r["risk_level"],
            "resolved": bool(r["fix_successful"]),
            "MTTR(s)": r["mttr"],
            "cause": r["root_cause"],
        }
        for r in database.recent_incidents(limit=limit)
    ]
