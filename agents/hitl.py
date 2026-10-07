"""Human-in-the-loop node (Phase 6) — voice approval for MEDIUM/HIGH risk.

Decision precedence:
  1. If `human_approved` is already set on the state (injected by a test, an API,
     or a prior turn), respect it — no prompting.
  2. Else, if VOICE_MODE is "text" or "voice", ask the human via the voice layer
     (queen speaks the incident, listens for yes/no | approve/reject).
  3. Else (VOICE_MODE "off", e.g. headless graph tests), fall back to
     AUTO_APPROVE_DEFAULT so the graph stays runnable without I/O.

`dashboard_hitl_node` is the click-to-approve variant: it PAUSES the graph with a
LangGraph `interrupt()` (state saved by the checkpointer) and waits for a human to
press Approve/Reject on the dashboard, which resumes it via `Command(resume=...)`.
It never auto-approves.
"""
from __future__ import annotations

from typing import Any

from langgraph.types import interrupt

from utils.config import settings
from utils.state import SentinelState

# Headless default when no human and no voice channel are available.
AUTO_APPROVE_DEFAULT = True


def hitl_node(state: SentinelState) -> dict[str, Any]:
    decision = state.get("human_approved")
    if decision is None:
        if settings.VOICE_MODE in {"text", "voice"}:
            from voice.hitl_voice import request_approval
            decision = request_approval(state)
        else:
            decision = AUTO_APPROVE_DEFAULT
    return {
        "human_approval_required": True,
        "human_approved": decision,
        "node_trace": ["hitl"],
    }


def dashboard_hitl_node(state: SentinelState) -> dict[str, Any]:
    decision = state.get("human_approved")
    if decision is None:
        # Pauses here until the dashboard resumes the thread with True/False.
        # On resume LangGraph re-enters this node and interrupt() returns that value.
        decision = bool(interrupt({
            "run_id": state.get("run_id"),
            "pipeline_name": state.get("pipeline_name"),
            "failure_type": state.get("failure_type"),
            "risk_level": state.get("risk_level"),
            "confidence": state.get("confidence"),
            "root_cause": state.get("root_cause"),
            "fix_plan": state.get("fix_plan"),
        }))
    return {
        "human_approval_required": True,
        "human_approved": decision,
        "node_trace": ["hitl"],
    }
