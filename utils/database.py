"""SQLite persistence for SENTINEL.

Two foundational tables created now (Phase 1):
  - pipeline_runs : one row per pipeline execution (the raw facts Monitor reads)
  - pipeline_data : the generated rows, stored as a JSON blob so schema drift
                    (changing columns) needs no migrations
  - incidents     : created now so Phases 3-7 (and incident-RAG) have a home

Cross-platform: paths via pathlib, so it works on Windows and Linux/WSL alike.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

# data/sentinel.db relative to the project root (SENTINEL/)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "sentinel.db"


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist. Safe to call repeatedly."""
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS pipeline_runs (
                run_id          INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_name   TEXT    NOT NULL,
                status          TEXT    NOT NULL,
                rows_expected   INTEGER NOT NULL,
                rows_actual     INTEGER NOT NULL,
                columns_actual  TEXT    NOT NULL,   -- JSON list
                execution_time  REAL    NOT NULL,
                error_message   TEXT,
                injected_failure TEXT,              -- ground truth for testing
                started_at      TEXT    NOT NULL,
                finished_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS pipeline_data (
                run_id        INTEGER NOT NULL,
                pipeline_name TEXT    NOT NULL,
                data_json     TEXT    NOT NULL,      -- JSON list of row dicts
                FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id)
            );

            CREATE TABLE IF NOT EXISTS incidents (
                incident_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                pipeline_name TEXT    NOT NULL,
                failure_type  TEXT,
                root_cause    TEXT,
                risk_level    TEXT,
                confidence    REAL,
                fix_plan      TEXT,
                fix_applied   INTEGER,
                fix_successful INTEGER,
                human_approved INTEGER,
                mttr          REAL,
                started_at    TEXT,
                resolved_at   TEXT,
                report        TEXT
            );

            -- Dashboard click-to-approve queue: one row per graph paused at HITL.
            CREATE TABLE IF NOT EXISTS approvals (
                approval_id   INTEGER PRIMARY KEY AUTOINCREMENT,
                thread_id     TEXT    NOT NULL UNIQUE,  -- LangGraph checkpoint thread
                run_id        INTEGER,
                pipeline_name TEXT,
                failure_type  TEXT,
                risk_level    TEXT,
                confidence    REAL,
                root_cause    TEXT,
                fix_plan      TEXT,
                status        TEXT    NOT NULL DEFAULT 'pending',  -- pending|approved|rejected
                outcome       TEXT,      -- resolved|unresolved|rejected|error (after resume)
                incident_id   INTEGER,
                error         TEXT,
                created_at    TEXT    NOT NULL,
                decided_at    TEXT,
                decided_by    TEXT
            );
            """
        )


def save_run(
    *,
    pipeline_name: str,
    status: str,
    rows_expected: int,
    rows_actual: int,
    columns_actual: list[str],
    execution_time: float,
    error_message: Optional[str],
    injected_failure: Optional[str],
    started_at: str,
    finished_at: str,
    data: Optional[list[dict[str, Any]]] = None,
) -> int:
    """Persist one pipeline run (+ its data) and return the run_id."""
    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO pipeline_runs
                (pipeline_name, status, rows_expected, rows_actual, columns_actual,
                 execution_time, error_message, injected_failure, started_at, finished_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pipeline_name, status, rows_expected, rows_actual,
                json.dumps(columns_actual), execution_time, error_message,
                injected_failure, started_at, finished_at,
            ),
        )
        run_id = cur.lastrowid
        if data:
            conn.execute(
                "INSERT INTO pipeline_data (run_id, pipeline_name, data_json) VALUES (?, ?, ?)",
                (run_id, pipeline_name, json.dumps(data)),
            )
        return run_id


def get_baseline(pipeline_name: str, last_n: int = 10) -> Optional[float]:
    """Average rows_actual across recent *successful* runs — the Monitor agent's
    reference point for row-count anomalies (Phase 2). None if no history yet."""
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT AVG(rows_actual) AS avg_rows FROM (
                SELECT rows_actual FROM pipeline_runs
                WHERE pipeline_name = ? AND status = 'success'
                ORDER BY run_id DESC LIMIT ?
            )
            """,
            (pipeline_name, last_n),
        ).fetchone()
    return row["avg_rows"] if row and row["avg_rows"] is not None else None


def get_run(run_id: int) -> Optional[sqlite3.Row]:
    """The raw facts of a single run — what the Monitor agent observes."""
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)
        ).fetchone()


def get_run_data(run_id: int) -> list[dict[str, Any]]:
    """The generated rows for a run, decoded from the JSON blob. Needed by the
    Monitor agent to inspect cell-level failures (NULLs, duplicates). Empty list
    if the run produced no data (e.g. a crash)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT data_json FROM pipeline_data WHERE run_id = ?", (run_id,)
        ).fetchone()
    return json.loads(row["data_json"]) if row else []


def save_incident(
    *,
    pipeline_name: str,
    failure_type: str,
    root_cause: str,
    risk_level: str,
    confidence: float,
    fix_plan: str,
    started_at: str,
    fix_applied: Optional[bool] = None,
    fix_successful: Optional[bool] = None,
    human_approved: Optional[bool] = None,
    mttr: Optional[float] = None,
    resolved_at: Optional[str] = None,
    report: Optional[str] = None,
) -> int:
    """Persist a diagnosed incident and return its incident_id. Booleans are
    stored as 0/1/NULL so Phases 5-7 can fill in remediation outcomes later."""
    def _b(v: Optional[bool]) -> Optional[int]:
        return None if v is None else int(v)

    with get_connection() as conn:
        cur = conn.execute(
            """
            INSERT INTO incidents
                (pipeline_name, failure_type, root_cause, risk_level, confidence,
                 fix_plan, fix_applied, fix_successful, human_approved, mttr,
                 started_at, resolved_at, report)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                pipeline_name, failure_type, root_cause, risk_level, confidence,
                fix_plan, _b(fix_applied), _b(fix_successful), _b(human_approved),
                mttr, started_at, resolved_at, report,
            ),
        )
        return cur.lastrowid


def get_similar_incidents(
    pipeline_name: str, failure_type: str, limit: int = 3
) -> list[sqlite3.Row]:
    """Cheap incident-RAG for the Diagnose agent: prior incidents of the same
    failure type, most recent first — same pipeline preferred but not required."""
    with get_connection() as conn:
        return conn.execute(
            """
            SELECT * FROM incidents
            WHERE failure_type = ?
            ORDER BY (pipeline_name = ?) DESC, incident_id DESC
            LIMIT ?
            """,
            (failure_type, pipeline_name, limit),
        ).fetchall()


def recent_incidents(limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM incidents ORDER BY incident_id DESC LIMIT ?", (limit,)
        ).fetchall()


def incident_stats() -> dict[str, Any]:
    """Aggregate metrics for the dashboard (Phase 9)."""
    with get_connection() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM incidents").fetchone()["c"]
        resolved = conn.execute(
            "SELECT COUNT(*) c FROM incidents WHERE fix_successful = 1"
        ).fetchone()["c"]
        avg_mttr = conn.execute(
            "SELECT AVG(mttr) m FROM incidents WHERE mttr IS NOT NULL"
        ).fetchone()["m"]
        by_type = conn.execute(
            "SELECT failure_type, COUNT(*) c FROM incidents GROUP BY failure_type"
        ).fetchall()
        by_risk = conn.execute(
            "SELECT risk_level, COUNT(*) c FROM incidents GROUP BY risk_level"
        ).fetchall()
    return {
        "total": total,
        "resolved": resolved,
        "resolution_rate": (resolved / total) if total else 0.0,
        "avg_mttr": avg_mttr,
        "by_type": {r["failure_type"]: r["c"] for r in by_type},
        "by_risk": {r["risk_level"]: r["c"] for r in by_risk},
    }


def recent_runs(pipeline_name: Optional[str] = None, limit: int = 20) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if pipeline_name:
            return conn.execute(
                "SELECT * FROM pipeline_runs WHERE pipeline_name = ? ORDER BY run_id DESC LIMIT ?",
                (pipeline_name, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM pipeline_runs ORDER BY run_id DESC LIMIT ?", (limit,)
        ).fetchall()


# --- dashboard approvals ----------------------------------------------------
def create_approval(*, thread_id: str, created_at: str, **fields: Any) -> int:
    """Queue a paused graph for a human decision. `fields` = the interrupt payload."""
    cols = ["run_id", "pipeline_name", "failure_type", "risk_level",
            "confidence", "root_cause", "fix_plan"]
    with get_connection() as conn:
        cur = conn.execute(
            f"INSERT INTO approvals (thread_id, created_at, {', '.join(cols)}) "
            f"VALUES (?, ?, {', '.join('?' * len(cols))})",
            (thread_id, created_at, *(fields.get(c) for c in cols)),
        )
        return cur.lastrowid


def claim_approval(approval_id: int, status: str, decided_by: str, decided_at: str) -> bool:
    """Atomically move pending -> approved/rejected. False if it was already decided,
    so a double-click (or two people clicking) can never resume the graph twice."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE approvals SET status = ?, decided_by = ?, decided_at = ? "
            "WHERE approval_id = ? AND status = 'pending'",
            (status, decided_by, decided_at, approval_id),
        )
        return cur.rowcount == 1


def finish_approval(approval_id: int, *, outcome: str,
                    incident_id: Optional[int] = None, error: Optional[str] = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE approvals SET outcome = ?, incident_id = ?, error = ? WHERE approval_id = ?",
            (outcome, incident_id, error, approval_id),
        )


def get_approval(approval_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM approvals WHERE approval_id = ?", (approval_id,)
        ).fetchone()


def list_approvals(status: Optional[str] = None, limit: int = 50) -> list[sqlite3.Row]:
    with get_connection() as conn:
        if status:
            return conn.execute(
                "SELECT * FROM approvals WHERE status = ? ORDER BY approval_id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        return conn.execute(
            "SELECT * FROM approvals WHERE status != 'pending' "
            "ORDER BY decided_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
