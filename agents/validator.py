"""Validator node (Phase 5) — confirm the fix actually worked.

The Fixer's claim ("I applied a fix") is not trusted. The Validator independently
loads the corrected run the Fixer produced and runs the SAME Monitor detection on
it. The fix is successful only if Monitor now finds no anomaly.

Reference baseline: the pipeline's canonical `expected_rows`, not the historical
average — the historical average is polluted by the injected-anomaly runs, whereas
expected_rows is the ground-truth normal volume the fix should restore.

If validation fails, the graph's bounded retry loop sends control back to the
Fixer (up to MAX_FIX_ATTEMPTS) before giving up and escalating in the report.
"""
from __future__ import annotations

import json
from typing import Any

from agents.monitor import detect
from pipelines import PIPELINES
from utils import database
from utils.state import SentinelState


def validator_node(state: SentinelState) -> dict[str, Any]:
    corrected_run_id = state.get("corrected_run_id")
    if not corrected_run_id:
        return {"fix_successful": False, "node_trace": ["validator"]}

    run = database.get_run(corrected_run_id)
    if run is None:
        return {"fix_successful": False, "node_trace": ["validator"]}

    pipeline_name = run["pipeline_name"]
    expected_cols = PIPELINES[pipeline_name].columns
    data = database.get_run_data(corrected_run_id)

    verdict = detect(
        pipeline_name=pipeline_name,
        status=run["status"],
        rows_actual=run["rows_actual"],
        columns_actual=json.loads(run["columns_actual"]),
        columns_expected=expected_cols,
        execution_time=run["execution_time"],
        baseline_rows=PIPELINES[pipeline_name].expected_rows,
        data=data,
    )

    return {
        "fix_successful": not verdict["failure_detected"],
        "node_trace": ["validator"],
    }
