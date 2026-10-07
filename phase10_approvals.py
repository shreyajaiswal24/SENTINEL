"""Phase 10 evaluation: dashboard click-to-approve HITL, fully offline.

Uses a stub Diagnose (fixed risk per failure type), an in-memory checkpointer and
a throwaway SQLite DB, so it needs no API key and leaves data/sentinel.db alone.

Verifies:
  * medium/high incidents PAUSE (queued as pending) — they are never auto-approved,
  * low auto-fixes and critical escalates without pausing,
  * Approve resumes the graph -> fixer -> validator -> reporter (resolved),
  * Reject resumes -> reporter only (no fix),
  * a second click on the same approval is refused (no double-run),
  * approve-all clears the queue.

    python phase10_approvals.py
"""
from __future__ import annotations

import tempfile
from pathlib import Path

from langgraph.checkpoint.memory import InMemorySaver

from backend import approvals
from pipelines import PIPELINES
from utils import database
from utils.state import FailureType

RISK = {
    FailureType.CRASH.value: "high",
    FailureType.SCHEMA_DRIFT.value: "medium",
    FailureType.ROW_COUNT_ANOMALY.value: "high",
    FailureType.NULL_VALUES.value: "critical",
    FailureType.DUPLICATE_DATA.value: "low",
}


def stub_diagnose(state):
    if not state.get("failure_detected"):
        return {"node_trace": ["diagnose:skipped"]}
    return {
        "root_cause": f"(stub) {state['failure_type']}",
        "risk_level": RISK[state["failure_type"]],
        "confidence": 0.9,
        "fix_plan": "(stub) re-run or repair",
        "node_trace": ["diagnose"],
    }


def run(pipeline: str, failure: FailureType) -> dict:
    return approvals.process_run(PIPELINES[pipeline]().run(force_failure=failure).run_id)


def check(label: str, cond: bool) -> bool:
    print(f"  [{'OK ' if cond else 'XX '}] {label}")
    return cond


def main() -> None:
    tmp = Path(tempfile.mkdtemp()) / "sentinel_test.db"
    database.DB_PATH = tmp
    database.init_db()
    approvals.get_graph(diagnose=stub_diagnose, checkpointer=InMemorySaver())
    for cls in PIPELINES.values():  # clean history = the Monitor's row-count baseline
        cls().run()
    ok = True

    print("=== Routing ===")
    crash = run("sales", FailureType.CRASH)
    drift = run("orders", FailureType.SCHEMA_DRIFT)
    rows = run("payments", FailureType.ROW_COUNT_ANOMALY)
    dupes = run("orders", FailureType.DUPLICATE_DATA)
    nulls = run("sales", FailureType.NULL_VALUES)
    ok &= check("high crash paused for approval", crash["status"] == "pending_approval")
    ok &= check("medium drift paused for approval", drift["status"] == "pending_approval")
    ok &= check("high row-count paused for approval", rows["status"] == "pending_approval")
    ok &= check("low duplicates auto-fixed (no pause)", dupes["status"] == "completed")
    ok &= check("critical nulls escalated (no pause)", nulls["status"] == "completed")
    ok &= check("3 pending in queue", len(database.list_approvals("pending")) == 3)

    print("\n=== Approve ===")
    res = approvals.decide(crash["approval_id"], True)
    inc = database.recent_incidents(1)[0]
    ok &= check("approve -> resolved", res["outcome"] == "resolved")
    ok &= check("incident saved as fixed + human-approved",
                inc["fix_successful"] == 1 and inc["human_approved"] == 1)

    print("\n=== Double click ===")
    try:
        approvals.decide(crash["approval_id"], True)
        ok &= check("second click refused", False)
    except approvals.AlreadyDecided:
        ok &= check("second click refused", True)

    print("\n=== Reject ===")
    res = approvals.decide(drift["approval_id"], False)
    inc = database.recent_incidents(1)[0]
    ok &= check("reject -> rejected", res["outcome"] == "rejected")
    ok &= check("no fix applied", not inc["fix_applied"] and inc["human_approved"] == 0)

    print("\n=== Approve all ===")
    run("sales", FailureType.CRASH)
    results = approvals.decide_all(True)
    ok &= check("approve-all handled 2", len(results) == 2)
    ok &= check("all resolved", all(r["outcome"] == "resolved" for r in results))
    ok &= check("queue empty", not database.list_approvals("pending"))

    print("\nPhase 10 OK." if ok else "\nPhase 10 FAILED.")
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
