"""Fixer node (Phase 5) — real, deterministic remediation.

Two strategies, dispatched on the failure type:

  * RERUN  — for execution failures and data loss (crash, timeout, row-count
             anomaly, null values): re-run the pipeline cleanly. The source data
             is fine; the run was the problem. Produces a fresh, correct run.

  * REPAIR — for recoverable data corruption (schema drift, duplicates): fix the
             stored artifact in place — rename the drifted column back to its
             canonical name, or drop duplicate rows — and persist the corrected run.

Either way the Fixer writes a NEW pipeline_runs row (the "corrected run") and puts
its id in `corrected_run_id`, which the Validator then independently re-monitors.
No data is destroyed: the original failed run stays on record for the report.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pipelines import PIPELINES
from utils import database
from utils.state import FailureType, RunStatus, SentinelState

RERUN = "rerun"
REPAIR = "repair"

# Which strategy each failure type gets.
FIX_STRATEGY = {
    FailureType.CRASH.value: RERUN,
    FailureType.TIMEOUT.value: RERUN,
    FailureType.ROW_COUNT_ANOMALY.value: RERUN,
    FailureType.NULL_VALUES.value: RERUN,
    FailureType.SCHEMA_DRIFT.value: REPAIR,
    FailureType.DUPLICATE_DATA.value: REPAIR,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _repair_schema_drift(rows: list[dict], pipeline) -> tuple[list[dict], list[str]]:
    """Rename the drifted column back to its canonical name."""
    new_name, canonical = pipeline.drift_new_name, pipeline.drift_column
    fixed = [
        {(canonical if k == new_name else k): v for k, v in row.items()}
        for row in rows
    ]
    columns = list(fixed[0].keys()) if fixed else list(pipeline.columns)
    return fixed, columns


def _repair_duplicates(rows: list[dict], pipeline) -> tuple[list[dict], list[str]]:
    """Drop rows that repeat an earlier row (keep first occurrence, order-stable)."""
    seen: set[str] = set()
    deduped = []
    for row in rows:
        key = repr(sorted(row.items(), key=lambda kv: kv[0]))
        if key not in seen:
            seen.add(key)
            deduped.append(row)
    return deduped, list(pipeline.columns)


_REPAIR_FN = {
    FailureType.SCHEMA_DRIFT.value: _repair_schema_drift,
    FailureType.DUPLICATE_DATA.value: _repair_duplicates,
}


def fixer_node(state: SentinelState) -> dict[str, Any]:
    failure_type = state.get("failure_type", FailureType.NONE.value)
    pipeline_name = state["pipeline_name"]
    pipeline_cls = PIPELINES.get(pipeline_name)
    attempts = state.get("fix_attempts", 0) + 1

    strategy = FIX_STRATEGY.get(failure_type, RERUN)
    corrected_run_id = None

    if pipeline_cls is None:
        return {"fix_applied": False, "fix_attempts": attempts, "node_trace": ["fixer"]}

    if strategy == RERUN:
        # Re-run the pipeline cleanly; .run() persists the corrected run itself.
        result = pipeline_cls().run()  # force_failure=None, fail_probability=0
        corrected_run_id = result.run_id
    else:  # REPAIR — transform the stored artifact and persist a corrected run
        pipeline = pipeline_cls()
        original = database.get_run_data(state.get("run_id"))
        repaired, columns = _REPAIR_FN[failure_type](original, pipeline)
        now = _now_iso()
        corrected_run_id = database.save_run(
            pipeline_name=pipeline_name,
            status=RunStatus.SUCCESS.value,
            rows_expected=pipeline.expected_rows,
            rows_actual=len(repaired),
            columns_actual=columns,
            execution_time=0.0,
            error_message=None,
            injected_failure=None,   # a corrected run carries no injected failure
            started_at=now,
            finished_at=now,
            data=repaired,
        )

    return {
        "fix_applied": True,
        "fix_strategy": strategy,
        "fix_attempts": attempts,
        "corrected_run_id": corrected_run_id,
        "node_trace": ["fixer"],
    }
