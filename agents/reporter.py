"""Reporter node (Phase 7) — the incident record + narrative.

Terminal sink for every incident path. Computes MTTR, builds a structured
human-readable report (and a short spoken summary for the voice layer), and
persists the incident so the dashboard and incident-RAG have a durable record.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from utils import database
from utils.state import RiskLevel, SentinelState

# Severity glyphs for at-a-glance scanning in logs / dashboard.
_RISK_GLYPH = {
    RiskLevel.LOW.value: "🟢",
    RiskLevel.MEDIUM.value: "🟡",
    RiskLevel.HIGH.value: "🟠",
    RiskLevel.CRITICAL.value: "🔴",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _mttr_seconds(started_at: Optional[str], resolved_at: str) -> Optional[float]:
    if not started_at:
        return None
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(resolved_at)
    except ValueError:
        return None
    return round((end - start).total_seconds(), 3)


def _human_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "n/a"
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes, secs = divmod(int(seconds), 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def _outcome(state: SentinelState) -> str:
    if state.get("risk_level") == RiskLevel.CRITICAL.value:
        return "ESCALATED — critical risk, no auto-fix attempted"
    if state.get("human_approved") is False:
        return "NOT FIXED — human rejected the proposed fix"
    if state.get("fix_successful"):
        return "RESOLVED — fix applied and validated"
    if state.get("fix_applied"):
        return "UNRESOLVED — fix applied but validation failed (escalated)"
    return "NO FIX — see risk/approval path"


def _spoken_summary(state: SentinelState, mttr: Optional[float]) -> str:
    """One sentence for the voice layer to read back."""
    name = state.get("pipeline_name")
    ftype = str(state.get("failure_type", "failure")).replace("_", " ")
    if state.get("fix_successful"):
        return f"The {name} pipeline's {ftype} was fixed and validated in {_human_duration(mttr)}."
    if state.get("risk_level") == RiskLevel.CRITICAL.value:
        return f"The {name} pipeline has a critical {ftype}. I have escalated it without auto-fixing."
    if state.get("human_approved") is False:
        return f"The {name} pipeline's {ftype} was not fixed because the fix was rejected."
    return f"The {name} pipeline's {ftype} could not be resolved automatically and was escalated."


def _report(state: SentinelState, mttr: Optional[float]) -> str:
    glyph = _RISK_GLYPH.get(state.get("risk_level", ""), "⚪")
    lines = [
        f"{glyph} INCIDENT — {state.get('pipeline_name')} / {state.get('failure_type')}",
        f"  risk:       {state.get('risk_level')} (confidence {state.get('confidence', 0):.2f})",
        f"  root cause: {state.get('root_cause', 'n/a')}",
        f"  fix plan:   {state.get('fix_plan', 'n/a')}",
        f"  strategy:   {state.get('fix_strategy', 'n/a')} "
        f"(attempts: {state.get('fix_attempts', 0)})",
        f"  approval:   {_approval_str(state)}",
        f"  outcome:    {_outcome(state)}",
        f"  MTTR:       {_human_duration(mttr)}",
    ]
    return "\n".join(lines)


def _approval_str(state: SentinelState) -> str:
    if not state.get("human_approval_required"):
        return "auto (no human needed)"
    return "approved by human" if state.get("human_approved") else "rejected by human"


def reporter_node(state: SentinelState) -> dict[str, Any]:
    resolved_at = _now_iso()
    mttr = _mttr_seconds(state.get("started_at"), resolved_at)
    report = _report(state, mttr)
    spoken = _spoken_summary(state, mttr)

    incident_id = database.save_incident(
        pipeline_name=state.get("pipeline_name", "unknown"),
        failure_type=state.get("failure_type", "none"),
        root_cause=state.get("root_cause", ""),
        risk_level=state.get("risk_level", RiskLevel.LOW.value),
        confidence=state.get("confidence", 0.0),
        fix_plan=state.get("fix_plan", ""),
        started_at=state.get("started_at", resolved_at),
        fix_applied=state.get("fix_applied"),
        fix_successful=state.get("fix_successful"),
        human_approved=state.get("human_approved"),
        mttr=mttr,
        resolved_at=resolved_at,
        report=report,
    )

    # Optionally speak the outcome when a voice channel is active.
    from utils.config import settings
    if settings.VOICE_MODE in {"text", "voice"}:
        from voice.speech import speak
        speak(spoken)

    return {
        "incident_id": incident_id,
        "mttr": mttr,
        "resolved_at": resolved_at,
        "report": report,
        "spoken_summary": spoken,
        "node_trace": ["reporter"],
    }
