"""Phase 5 evaluation: real Fixer + Validator, fully offline.

For each failure type this injects a FRESH failing run, drives it through the
graph (stub Diagnose, real Fixer + Validator + retry loop), and then INSPECTS the
corrected run's data to prove the fix is real — not just a flag flip:

  schema_drift   -> drifted column renamed back to canonical
  duplicate_data -> no duplicate rows remain
  row_count / crash / timeout / null_values -> clean re-run restores good data

Also unit-tests the Validator->Fixer retry routing.

    python phase5.py
"""
from __future__ import annotations

import json
from typing import Any

from agents.diagnose import DEFAULT_RISK
from agents.fixer import RERUN, REPAIR
from agents.graph import (
    MAX_FIX_ATTEMPTS,
    build_sentinel_graph,
    initial_state_from_run,
    route_after_validator,
)
from pipelines import PIPELINES
from utils import database
from utils.state import CRITICAL_COLUMNS, FailureType, RiskLevel


def stub_diagnose(state: dict) -> dict[str, Any]:
    if not state.get("failure_detected"):
        return {"node_trace": ["diagnose:skipped"]}
    ftype = state["failure_type"]
    return {
        "root_cause": f"(stub) {ftype}",
        "risk_level": DEFAULT_RISK.get(ftype, RiskLevel.MEDIUM).value,
        "confidence": 0.9,
        "fix_plan": f"(stub) remediate {ftype}",
        "similar_incidents": [],
        "node_trace": ["diagnose"],
    }


def _inject(pipeline_name: str, failure: FailureType):
    """Create a fresh failing run and return its DB row."""
    result = PIPELINES[pipeline_name]().run(force_failure=failure)
    return database.get_run(result.run_id)


def _verify_corrected(failure: FailureType, pipeline_name: str, corrected_run_id: int) -> tuple[bool, str]:
    run = database.get_run(corrected_run_id)
    data = database.get_run_data(corrected_run_id)
    cols = json.loads(run["columns_actual"])
    pipeline = PIPELINES[pipeline_name]
    expected = pipeline.expected_rows

    if failure is FailureType.SCHEMA_DRIFT:
        ok = pipeline.drift_column in cols and pipeline.drift_new_name not in cols
        return ok, f"cols restored ({pipeline.drift_column} present={pipeline.drift_column in cols})"
    if failure is FailureType.DUPLICATE_DATA:
        keys = [repr(sorted(r.items(), key=lambda kv: kv[0])) for r in data]
        ok = len(keys) == len(set(keys))
        return ok, f"{len(keys)} rows, {len(keys)-len(set(keys))} dups"
    if failure is FailureType.NULL_VALUES:
        crit = CRITICAL_COLUMNS.get(pipeline_name, [])
        nulls = sum(1 for r in data for c in crit if r.get(c) is None)
        return nulls == 0, f"{nulls} null critical cells"
    # crash / timeout / row_count_anomaly: re-run restored normal volume + speed
    within = abs(run["rows_actual"] - expected) <= 0.2 * expected
    fast = run["execution_time"] <= 10.0
    return (within and fast), f"rows={run['rows_actual']}/{expected} time={run['execution_time']:.1f}s"


def test_end_to_end() -> bool:
    print("=== Phase 5: real Fixer + Validator (offline) ===\n")
    app = build_sentinel_graph(diagnose=stub_diagnose)
    all_ok = True
    for failure in FailureType:
        if failure is FailureType.NONE:
            continue
        # use sales for most; each pipeline supports all failures
        pipeline_name = "sales"
        row = _inject(pipeline_name, failure)
        out = app.invoke(initial_state_from_run(row))

        applied = out.get("fix_applied")
        success = out.get("fix_successful")
        strategy = out.get("fix_strategy")
        corrected = out.get("corrected_run_id")
        data_ok, detail = _verify_corrected(failure, pipeline_name, corrected) if corrected else (False, "no corrected run")

        ok = bool(applied and success and data_ok)
        all_ok &= ok
        mark = "OK " if ok else "XX "
        print(f"  [{mark}] {failure.value:<18} strategy={strategy:<6} "
              f"applied={applied} validated={success}")
        print(f"        corrected run #{corrected}: {detail}")
    return all_ok


def test_retry_routing() -> bool:
    print("\n=== Validator -> Fixer retry routing ===")
    cases = [
        ("success -> reporter", {"fix_successful": True, "fix_attempts": 1}, "reporter"),
        ("fail, under cap -> fixer", {"fix_successful": False, "fix_attempts": 1}, "fixer"),
        ("fail, at cap -> reporter", {"fix_successful": False, "fix_attempts": MAX_FIX_ATTEMPTS}, "reporter"),
    ]
    ok = True
    for name, state, expected in cases:
        got = route_after_validator(state)
        good = got == expected
        ok &= good
        print(f"  [{'OK ' if good else 'XX '}] {name}: -> {got}")
    return ok


def main() -> None:
    database.init_db()
    e2e = test_end_to_end()
    retry = test_retry_routing()
    all_ok = e2e and retry
    print("\nPhase 5", "OK." if all_ok else "FAILED — see XX rows above.")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
