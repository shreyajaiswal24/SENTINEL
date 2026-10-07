"""Phase 4 evaluation: drive the full graph end-to-end and verify routing.

Runs WITHOUT a Groq key by injecting a deterministic stub Diagnose node (risk is
taken from the per-failure anchor in agents.diagnose.DEFAULT_RISK). This proves
the graph wiring + risk routing are correct; swap the stub for the real node once
the key is in .env (build_sentinel_graph() with no args uses the LLM node).

    python phase4.py

Checks each path:
  clean       -> monitor -> END
  low risk    -> monitor -> diagnose -> fixer -> validator -> reporter
  med/high    -> monitor -> diagnose -> hitl  -> fixer -> validator -> reporter   (approved)
  rejected    -> monitor -> diagnose -> hitl  -> reporter                          (human said no)
  critical    -> monitor -> diagnose -> reporter                                   (no auto-fix)
"""
from __future__ import annotations

from typing import Any, Optional

from agents.diagnose import DEFAULT_RISK
from agents.graph import build_sentinel_graph, initial_state_from_run
from utils import database
from utils.state import FailureType, RiskLevel


def stub_diagnose(risk_override: Optional[str] = None):
    """A deterministic, no-LLM Diagnose node for offline routing tests."""
    def _node(state: dict) -> dict[str, Any]:
        if not state.get("failure_detected"):
            return {"node_trace": ["diagnose:skipped"]}
        ftype = state["failure_type"]
        risk = risk_override or DEFAULT_RISK.get(ftype, RiskLevel.MEDIUM).value
        return {
            "root_cause": f"(stub) diagnosed {ftype}",
            "risk_level": risk,
            "confidence": 0.9,
            "fix_plan": f"(stub) remediate {ftype}",
            "similar_incidents": [],
            "node_trace": ["diagnose"],
        }
    return _node


def _run_one(failure: FailureType, *, risk_override=None, human_approved=None):
    with database.get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM pipeline_runs WHERE injected_failure = ? ORDER BY run_id DESC LIMIT 1",
            (failure.value,),
        ).fetchone()
    if row is None:
        return None
    app = build_sentinel_graph(diagnose=stub_diagnose(risk_override))
    state = initial_state_from_run(row)
    if human_approved is not None:
        state["human_approved"] = human_approved
    return app.invoke(state)


def _check(name: str, trace: list[str], expected: list[str]) -> bool:
    ok = trace == expected
    mark = "OK " if ok else "XX "
    print(f"  [{mark}] {name}")
    print(f"        path: {' -> '.join(trace)}")
    if not ok:
        print(f"        expected: {' -> '.join(expected)}")
    return ok


def main() -> None:
    database.init_db()
    print("=== Phase 4: full-graph routing (offline, stub Diagnose) ===\n")
    all_ok = True

    # clean run: monitor detects nothing -> END (diagnose never runs)
    out = _run_one(FailureType.NONE)
    all_ok &= _check("clean run", out["node_trace"], ["monitor"])

    # low risk (duplicate_data anchor = low) -> auto-fix, no human
    out = _run_one(FailureType.DUPLICATE_DATA)
    all_ok &= _check("low risk -> auto-fix", out["node_trace"],
                     ["monitor", "diagnose", "fixer", "validator", "reporter"])

    # high risk (schema_drift anchor = high) -> hitl, auto-approved default
    out = _run_one(FailureType.SCHEMA_DRIFT)
    all_ok &= _check("high risk -> hitl -> approve", out["node_trace"],
                     ["monitor", "diagnose", "hitl", "fixer", "validator", "reporter"])

    # human rejects the fix -> stop after hitl
    out = _run_one(FailureType.SCHEMA_DRIFT, human_approved=False)
    all_ok &= _check("high risk -> hitl -> reject", out["node_trace"],
                     ["monitor", "diagnose", "hitl", "reporter"])

    # critical risk -> straight to reporter, never fix
    out = _run_one(FailureType.CRASH, risk_override=RiskLevel.CRITICAL.value)
    all_ok &= _check("critical -> escalate (no fix)", out["node_trace"],
                     ["monitor", "diagnose", "reporter"])

    # spot-check the outputs the reporter produced on a successful fix
    out = _run_one(FailureType.ROW_COUNT_ANOMALY)  # medium -> hitl -> fixer -> ...
    print("\n=== Reporter output sample (row_count_anomaly) ===")
    print(f"  incident_id={out.get('incident_id')} mttr={out.get('mttr')}s "
          f"fix_successful={out.get('fix_successful')}")
    print(f"  report: {out.get('report')}")
    fields_ok = (out.get("incident_id") is not None
                 and out.get("report")
                 and out.get("fix_successful") is True)
    all_ok &= fields_ok
    print(f"  reporter fields populated: {'OK' if fields_ok else 'XX'}")

    print("\nPhase 4", "OK." if all_ok else "FAILED — see XX rows above.")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
