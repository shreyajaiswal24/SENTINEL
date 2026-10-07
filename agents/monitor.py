"""Monitor agent (Phase 2).

Reads the RAW FACTS of a pipeline run (never the `injected_failure` ground-truth
label) plus the historical baseline, and decides whether the run is anomalous —
and if so, which single failure type best describes it.

Design decisions (agreed in planning):
  * Single best label. When several anomalies coexist (a drift run could also dip
    in row count), Monitor reports ONE failure_type by severity priority. This
    matches the one-label ground truth and keeps scoring honest. The priority is:
        crash > timeout > schema_drift > row_count_anomaly > null_values > duplicate_data
  * LangGraph node now. `monitor_node(state)` is a graph node returning a partial
    SentinelState; the full graph is assembled in Phase 4. `detect()` underneath
    is a pure function so Phase 2 can be unit-tested without the graph.

The Monitor only OBSERVES and CLASSIFIES. It does not fix, and it does not assign
risk — Diagnose (Phase 3) owns root cause and risk tier.
"""
from __future__ import annotations

from typing import Any, Optional

from langgraph.graph import END, START, StateGraph

from utils import database
from utils.state import (
    CRITICAL_COLUMNS,
    FailureType,
    RunStatus,
    SentinelState,
)

# Monitor's time budget. A run slower than this is flagged as a timeout.
TIMEOUT_BUDGET_SECONDS = 10.0
# A run is a row-count anomaly if it falls outside baseline +/- this fraction.
ROW_COUNT_TOLERANCE = 0.20

# Severity order, highest first. Used to collapse multiple anomalies to one label.
_PRIORITY = [
    FailureType.CRASH,
    FailureType.TIMEOUT,
    FailureType.SCHEMA_DRIFT,
    FailureType.ROW_COUNT_ANOMALY,
    FailureType.NULL_VALUES,
    FailureType.DUPLICATE_DATA,
]


def _has_nulls(rows: list[dict[str, Any]], critical_columns: list[str]) -> bool:
    return any(row.get(col) is None for row in rows for col in critical_columns)


def _duplicate_count(rows: list[dict[str, Any]]) -> int:
    """How many rows are repeats of an earlier row (len - unique)."""
    seen: set[str] = set()
    dups = 0
    for row in rows:
        # order-independent fingerprint of the row's items
        key = repr(sorted(row.items(), key=lambda kv: kv[0]))
        if key in seen:
            dups += 1
        else:
            seen.add(key)
    return dups


def detect(
    *,
    pipeline_name: str,
    status: str,
    rows_actual: int,
    columns_actual: list[str],
    columns_expected: list[str],
    execution_time: float,
    baseline_rows: Optional[float],
    data: Optional[list[dict[str, Any]]] = None,
) -> dict[str, Any]:
    """Pure detection. Returns {failure_detected, failure_type, anomalies, reason}.

    `anomalies` is every anomaly observed (for transparency / Phase 7 reporting);
    `failure_type` is the single highest-priority one (NONE if clean).
    """
    anomalies: list[FailureType] = []
    reasons: dict[str, str] = {}

    # 1. crash — the pipeline reported it directly
    if status == RunStatus.CRASHED.value:
        anomalies.append(FailureType.CRASH)
        reasons[FailureType.CRASH.value] = "pipeline reported status=crashed"

    # 2. timeout — over the budget
    if execution_time > TIMEOUT_BUDGET_SECONDS:
        anomalies.append(FailureType.TIMEOUT)
        reasons[FailureType.TIMEOUT.value] = (
            f"execution_time {execution_time:.1f}s > {TIMEOUT_BUDGET_SECONDS:.0f}s budget"
        )

    # 3. schema drift — columns differ from the expected set (ignore ordering)
    if columns_actual and set(columns_actual) != set(columns_expected):
        anomalies.append(FailureType.SCHEMA_DRIFT)
        added = sorted(set(columns_actual) - set(columns_expected))
        removed = sorted(set(columns_expected) - set(columns_actual))
        reasons[FailureType.SCHEMA_DRIFT.value] = f"added={added} removed={removed}"

    # 5 & 6. cell-level checks need the data blob. Done before the row-count
    # check because duplicates EXPLAIN an inflated count — we judge the row count
    # on unique rows so a pure-duplicate run isn't mislabeled as a row anomaly.
    unique_rows = rows_actual
    if data:
        critical = CRITICAL_COLUMNS.get(pipeline_name, [])
        if critical and _has_nulls(data, critical):
            anomalies.append(FailureType.NULL_VALUES)
            reasons[FailureType.NULL_VALUES.value] = f"NULLs in critical columns {critical}"
        dup_count = _duplicate_count(data)
        if dup_count > 0:
            anomalies.append(FailureType.DUPLICATE_DATA)
            reasons[FailureType.DUPLICATE_DATA.value] = f"{dup_count} duplicate rows present"
        unique_rows = rows_actual - dup_count

    # 4. row-count anomaly — unique-row volume outside baseline tolerance (needs history)
    if baseline_rows is not None and baseline_rows > 0:
        low = baseline_rows * (1 - ROW_COUNT_TOLERANCE)
        high = baseline_rows * (1 + ROW_COUNT_TOLERANCE)
        if not (low <= unique_rows <= high):
            anomalies.append(FailureType.ROW_COUNT_ANOMALY)
            note = "" if unique_rows == rows_actual else f" (unique of {rows_actual})"
            reasons[FailureType.ROW_COUNT_ANOMALY.value] = (
                f"rows {unique_rows}{note} outside baseline "
                f"{baseline_rows:.0f} +/-{int(ROW_COUNT_TOLERANCE*100)}%"
            )

    # collapse to the single highest-priority label
    best = FailureType.NONE
    for failure in _PRIORITY:
        if failure in anomalies:
            best = failure
            break

    return {
        "failure_detected": best is not FailureType.NONE,
        "failure_type": best.value,
        "anomalies": [a.value for a in anomalies],
        "reason": reasons.get(best.value, "no anomaly detected"),
    }


def monitor_node(state: SentinelState) -> dict[str, Any]:
    """LangGraph node. Reads observation fields from `state`, looks up the
    baseline from the DB, and writes the detection verdict back into the state.

    Expects the observation half of SentinelState to be populated (by the
    pipeline-run -> state adapter). Falls back to a DB lookup of the cell data
    only when it isn't already carried on the state.
    """
    pipeline_name = state["pipeline_name"]
    data = state.get("_data")  # optional carry-through to avoid a DB hit
    if data is None and state.get("run_id"):
        data = database.get_run_data(state["run_id"])

    verdict = detect(
        pipeline_name=pipeline_name,
        status=state.get("pipeline_status", RunStatus.SUCCESS.value),
        rows_actual=state.get("rows_actual", 0),
        columns_actual=state.get("columns_actual", []),
        columns_expected=state.get("columns_expected", []),
        execution_time=state.get("execution_time", 0.0),
        baseline_rows=database.get_baseline(pipeline_name),
        data=data,
    )
    return {
        "failure_detected": verdict["failure_detected"],
        "failure_type": verdict["failure_type"],
        "node_trace": ["monitor"],
    }


def build_monitor_graph():
    """A minimal one-node graph. Phase 4 extends START -> monitor -> diagnose -> ...;
    for now it's START -> monitor -> END so the node is exercised as a real graph."""
    graph = StateGraph(SentinelState)
    graph.add_node("monitor", monitor_node)
    graph.add_edge(START, "monitor")
    graph.add_edge("monitor", END)
    return graph.compile()
