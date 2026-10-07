"""Shared types for SENTINEL.

`SentinelState` is the single dict that flows through every LangGraph node
(wired in Phase 4). Defined here in Phase 1 so pipelines, the database, and the
agents all speak the same vocabulary (enums) from day one.
"""
from __future__ import annotations

import operator
from enum import Enum
from typing import Annotated, Optional, TypedDict


class FailureType(str, Enum):
    """The failure modes a pipeline can exhibit (and that we inject for testing)."""
    NONE = "none"
    SCHEMA_DRIFT = "schema_drift"          # a column gets renamed (amount -> total_amount)
    ROW_COUNT_ANOMALY = "row_count_anomaly"  # far fewer rows than the baseline
    CRASH = "crash"                        # exception thrown, nothing saved
    TIMEOUT = "timeout"                    # execution exceeds the time budget
    NULL_VALUES = "null_values"            # critical columns contain NULLs
    DUPLICATE_DATA = "duplicate_data"      # the same records saved multiple times


class RunStatus(str, Enum):
    """Raw outcome reported by a pipeline. The Monitor agent (Phase 2) decides
    whether a SUCCESS run is actually anomalous — the pipeline only reports facts."""
    SUCCESS = "success"
    CRASHED = "crashed"


class RiskLevel(str, Enum):
    LOW = "low"            # auto-fix silently
    MEDIUM = "medium"      # voice ask -> YES/NO
    HIGH = "high"          # voice ask -> APPROVE/REJECT
    CRITICAL = "critical"  # stop everything + alert, no auto-fix


# Critical columns whose NULLs / disappearance matter most, per pipeline.
# Kept here so Diagnose (Phase 3) and the pipelines agree on what "critical" means.
CRITICAL_COLUMNS = {
    "sales": ["order_id", "amount"],
    "orders": ["order_id", "total"],
    "payments": ["payment_id", "amount"],
}


class SentinelState(TypedDict, total=False):
    """The shared graph state. `total=False` so nodes fill it in incrementally."""
    # --- observation (Monitor) ---
    run_id: int               # the pipeline_runs row this state observes
    _data: list               # optional cell data carried from the run (avoids a DB hit)
    pipeline_name: str
    pipeline_status: str
    rows_expected: int
    rows_actual: int
    columns_expected: list[str]
    columns_actual: list[str]
    error_message: Optional[str]
    execution_time: float

    # --- detection / diagnosis (Monitor + Diagnose) ---
    failure_detected: bool
    failure_type: str
    root_cause: str
    risk_level: str
    confidence: float          # Diagnose self-confidence (0-1); low -> bump risk tier
    similar_incidents: list     # cheap RAG: prior incidents fed to Diagnose

    # --- remediation (Fixer + Validator) ---
    fix_plan: str
    fix_strategy: str          # "rerun" | "repair" — how the Fixer addressed it
    fix_applied: bool
    fix_successful: bool
    fix_attempts: int          # bounded-retry counter (Validator -> Fixer loop)
    corrected_run_id: Optional[int]  # the new, fixed pipeline_runs row

    # --- human-in-the-loop ---
    human_approval_required: bool
    human_approved: Optional[bool]

    # --- reporting ---
    incident_id: Optional[int]
    mttr: Optional[float]
    started_at: str
    resolved_at: Optional[str]
    report: Optional[str]
    spoken_summary: Optional[str]   # one-liner for the voice layer to read back

    # --- orchestration trace (Phase 4) ---
    # `operator.add` reducer: each node appends its name, so the path through the
    # graph accumulates instead of being overwritten on every state update.
    node_trace: Annotated[list[str], operator.add]
