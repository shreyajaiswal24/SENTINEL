"""SENTINEL graph assembly (Phase 4).

Wires every agent node into one LangGraph state machine with risk-based routing:

    START
      -> monitor
           | failure?  no  -> END            (clean run, nothing to do)
           |           yes -> diagnose
    diagnose
           | risk == critical -> reporter     (stop, escalate, NO auto-fix)
           | risk == low       -> fixer        (auto-fix silently)
           | risk in {med,high}-> hitl         (ask a human)
    hitl
           | approved  -> fixer
           | rejected  -> reporter             (record the rejection, no fix)
    fixer  -> validator -> reporter -> END

The Diagnose node is INJECTABLE so the whole graph can be exercised offline
(before the Groq key exists): pass `diagnose=<stub>` to build_sentinel_graph().
In production, omit it and the real LLM-backed node is used.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from langgraph.graph import END, START, StateGraph

from agents.diagnose import diagnose_node
from agents.fixer import fixer_node
from agents.hitl import dashboard_hitl_node, hitl_node
from agents.monitor import monitor_node
from agents.reporter import reporter_node
from agents.validator import validator_node
from pipelines import PIPELINES
from utils.state import RiskLevel, SentinelState

NodeFn = Callable[[SentinelState], dict[str, Any]]

# How many times the Fixer may retry before the Validator gives up and escalates.
MAX_FIX_ATTEMPTS = 2


# --- routing predicates -----------------------------------------------------
def route_after_monitor(state: SentinelState) -> str:
    return "diagnose" if state.get("failure_detected") else END


def route_after_diagnose(state: SentinelState) -> str:
    risk = state.get("risk_level")
    if risk == RiskLevel.CRITICAL.value:
        return "reporter"          # critical: never auto-fix
    if risk == RiskLevel.LOW.value:
        return "fixer"             # low: auto-fix silently
    return "hitl"                  # medium/high: ask a human


def route_after_hitl(state: SentinelState) -> str:
    return "fixer" if state.get("human_approved") else "reporter"


def route_after_validator(state: SentinelState) -> str:
    """Success -> report it. Failure -> retry the Fixer until the attempt cap,
    then give up and let the Reporter escalate (fix_successful stays False)."""
    if state.get("fix_successful"):
        return "reporter"
    if state.get("fix_attempts", 0) < MAX_FIX_ATTEMPTS:
        return "fixer"
    return "reporter"


# --- assembly ---------------------------------------------------------------
def build_sentinel_graph(
    diagnose: Optional[NodeFn] = None,
    *,
    approval: str = "auto",
    checkpointer: Any = None,
):
    """Compile the full graph. `diagnose` overrides the Diagnose node (for offline
    testing); defaults to the real LLM-backed node.

    `approval` picks the HITL node: "auto" = voice/headless (hitl_node);
    "dashboard" = pause for an Approve/Reject click (needs a `checkpointer`)."""
    if approval == "dashboard" and checkpointer is None:
        raise ValueError("dashboard approval needs a checkpointer to pause/resume")
    graph = StateGraph(SentinelState)

    graph.add_node("monitor", monitor_node)
    graph.add_node("diagnose", diagnose or diagnose_node)
    graph.add_node("hitl", dashboard_hitl_node if approval == "dashboard" else hitl_node)
    graph.add_node("fixer", fixer_node)
    graph.add_node("validator", validator_node)
    graph.add_node("reporter", reporter_node)

    graph.add_edge(START, "monitor")
    graph.add_conditional_edges("monitor", route_after_monitor, {"diagnose": "diagnose", END: END})
    graph.add_conditional_edges(
        "diagnose", route_after_diagnose,
        {"reporter": "reporter", "fixer": "fixer", "hitl": "hitl"},
    )
    graph.add_conditional_edges("hitl", route_after_hitl, {"fixer": "fixer", "reporter": "reporter"})
    graph.add_edge("fixer", "validator")
    graph.add_conditional_edges("validator", route_after_validator, {"fixer": "fixer", "reporter": "reporter"})
    graph.add_edge("reporter", END)

    return graph.compile(checkpointer=checkpointer)


# --- run -> initial state adapter -------------------------------------------
def initial_state_from_run(run) -> dict[str, Any]:
    """Build the observation half of SentinelState from a pipeline_runs row."""
    return {
        "run_id": run["run_id"],
        "pipeline_name": run["pipeline_name"],
        "pipeline_status": run["status"],
        "rows_expected": run["rows_expected"],
        "rows_actual": run["rows_actual"],
        "columns_actual": json.loads(run["columns_actual"]),
        "columns_expected": PIPELINES[run["pipeline_name"]].columns,
        "execution_time": run["execution_time"],
        "error_message": run["error_message"],
        "started_at": run["started_at"],
        "node_trace": [],
    }
