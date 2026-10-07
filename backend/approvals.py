"""Click-to-approve HITL for the dashboard.

Medium/high-risk incidents pause the SENTINEL graph at the HITL node (LangGraph
`interrupt()`); the paused state is saved by a SQLite checkpointer so it survives
a server restart. Each pause is queued in the `approvals` table. When a human
clicks Approve/Reject, the decision is claimed atomically (no double-runs) and the
graph is resumed with `Command(resume=...)`: approve -> fixer -> validator ->
reporter; reject -> reporter. Critical incidents never pause — they escalate.
"""
from __future__ import annotations

import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from agents.graph import build_sentinel_graph, initial_state_from_run
from pipelines import PIPELINES
from utils import database
from utils.state import FailureType

CHECKPOINT_DB = database.DB_PATH.parent / "checkpoints.db"

# Failures the "Simulate failures" button injects, spread across the pipelines.
# Timeout is left out: its injected run deliberately sleeps ~11s.
SIMULATED_FAILURES = [
    ("sales", FailureType.CRASH),
    ("orders", FailureType.SCHEMA_DRIFT),
    ("payments", FailureType.ROW_COUNT_ANOMALY),
    ("sales", FailureType.NULL_VALUES),
    ("orders", FailureType.DUPLICATE_DATA),
    ("payments", FailureType.CRASH),
]


class AlreadyDecided(Exception):
    """The approval was not pending (already approved/rejected, or unknown)."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


_graph = None
_graph_lock = threading.Lock()


def get_graph(diagnose=None, checkpointer=None):
    """The dashboard-approval graph (singleton). Args are for tests."""
    global _graph
    with _graph_lock:
        if _graph is None or diagnose is not None or checkpointer is not None:
            if checkpointer is None:
                conn = sqlite3.connect(CHECKPOINT_DB, check_same_thread=False)
                checkpointer = SqliteSaver(conn)
            _graph = build_sentinel_graph(
                diagnose, approval="dashboard", checkpointer=checkpointer,
            )
        return _graph


def _config(thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_id}}


def process_run(run_id: int) -> dict[str, Any]:
    """Push one pipeline run through the graph. Low risk auto-fixes, critical
    escalates, medium/high pauses and is queued for the dashboard."""
    graph = get_graph()
    thread_id = f"run-{run_id}-{uuid.uuid4().hex[:8]}"
    config = _config(thread_id)
    result = graph.invoke(initial_state_from_run(database.get_run(run_id)), config)

    snapshot = graph.get_state(config)
    interrupts = [i for t in snapshot.tasks for i in t.interrupts]
    if interrupts:
        approval_id = database.create_approval(
            thread_id=thread_id, created_at=_now_iso(), **interrupts[0].value,
        )
        return {"run_id": run_id, "status": "pending_approval", "approval_id": approval_id}
    return {"run_id": run_id, "status": "completed", "incident_id": result.get("incident_id")}


def decide(approval_id: int, approve: bool, decided_by: str = "dashboard") -> dict[str, Any]:
    """Apply a human decision and resume the paused graph."""
    status = "approved" if approve else "rejected"
    if not database.claim_approval(approval_id, status, decided_by, _now_iso()):
        raise AlreadyDecided(f"approval {approval_id} is not pending")

    row = database.get_approval(approval_id)
    try:
        result = get_graph().invoke(Command(resume=approve), _config(row["thread_id"]))
    except Exception as exc:  # noqa: BLE001 — record it; don't leave the row half-done
        database.finish_approval(approval_id, outcome="error", error=str(exc))
        raise
    if not approve:
        outcome = "rejected"
    else:
        outcome = "resolved" if result.get("fix_successful") else "unresolved"
    database.finish_approval(approval_id, outcome=outcome, incident_id=result.get("incident_id"))
    return {"approval_id": approval_id, "status": status, "outcome": outcome,
            "incident_id": result.get("incident_id")}


def decide_all(approve: bool, decided_by: str = "dashboard") -> list[dict[str, Any]]:
    """Approve (or reject) every pending item. Already-claimed ones are skipped."""
    results = []
    for row in database.list_approvals("pending", limit=500):
        try:
            results.append(decide(row["approval_id"], approve, decided_by))
        except AlreadyDecided:
            continue
        except Exception as exc:  # noqa: BLE001
            results.append({"approval_id": row["approval_id"], "outcome": "error", "error": str(exc)})
    return results


# --- "Simulate failures" (background, so the button returns immediately) ----
_sim_lock = threading.Lock()
SIMULATION: dict[str, Any] = {"running": False, "done": 0, "total": 0, "error": None}


def _simulate() -> None:
    try:
        for pipeline_name, failure in SIMULATED_FAILURES:
            run = PIPELINES[pipeline_name]().run(force_failure=failure)
            process_run(run.run_id)
            SIMULATION["done"] += 1
    except Exception as exc:  # noqa: BLE001
        SIMULATION["error"] = str(exc)
    finally:
        SIMULATION["running"] = False


def start_simulation() -> bool:
    """Kick off a batch of injected failures. False if one is already running."""
    with _sim_lock:
        if SIMULATION["running"]:
            return False
        SIMULATION.update(running=True, done=0, total=len(SIMULATED_FAILURES), error=None)
    threading.Thread(target=_simulate, daemon=True).start()
    return True


def approvals_view() -> dict[str, Any]:
    def shape(r) -> dict[str, Any]:
        return {k: r[k] for k in r.keys()}
    return {
        "pending": [shape(r) for r in database.list_approvals("pending")],
        "recent": [shape(r) for r in database.list_approvals(limit=10)],
        "simulation": dict(SIMULATION),
    }
