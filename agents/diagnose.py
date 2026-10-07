"""Diagnose agent (Phase 3) — the first LLM-backed node.

Monitor (Phase 2) answers *what failed*. Diagnose answers *why, how risky, and
what to do*. It takes Monitor's `failure_type` plus the run facts, retrieves
similar past incidents (cheap incident-RAG from the `incidents` table), and asks
an LLM (OpenAI first, Groq as fallback) for a structured diagnosis:

    { root_cause, risk_level, confidence, fix_plan }

Rules (agreed in planning):
  * LangGraph node. `diagnose_node(state)` returns a partial SentinelState.
  * Low confidence bumps the risk tier — an uncertain diagnosis is treated as
    more dangerous, not less (a cheap safety margin before remediation).
  * The LLM decides, but we ANCHOR and VALIDATE: defaults per failure type guide
    the model and catch malformed output, so a bad LLM response can't crash the
    graph or smuggle in an invalid risk level.

Requires OPENAI_API_KEY (preferred) or GROQ_API_KEY in .env. If the preferred
provider errors at call time, `diagnose()` falls back to the other; it raises a
clear error only if no key is set or every provider fails.
"""
from __future__ import annotations

import json
import re
from typing import Any, Optional

from langchain_groq import ChatGroq
from langchain_openai import ChatOpenAI
from langgraph.graph import END, START, StateGraph

from utils import database
from utils.config import settings
from utils.state import FailureType, RiskLevel, SentinelState

# Sensible risk anchor per failure type. Guides the LLM and is the fallback if it
# returns an invalid tier. Diagnose may override based on the specifics.
DEFAULT_RISK = {
    FailureType.CRASH.value: RiskLevel.HIGH,
    FailureType.TIMEOUT.value: RiskLevel.MEDIUM,
    FailureType.SCHEMA_DRIFT.value: RiskLevel.HIGH,
    FailureType.ROW_COUNT_ANOMALY.value: RiskLevel.MEDIUM,
    FailureType.NULL_VALUES.value: RiskLevel.HIGH,
    FailureType.DUPLICATE_DATA.value: RiskLevel.LOW,
    FailureType.NONE.value: RiskLevel.LOW,
}

# Order used when a low-confidence diagnosis bumps the tier up one level.
_RISK_LADDER = [RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL]
CONFIDENCE_BUMP_THRESHOLD = 0.5

_SYSTEM_PROMPT = (
    "You are the Diagnose agent in SENTINEL, a self-healing data-pipeline system. "
    "Given a detected pipeline failure and its facts, you determine the ROOT CAUSE, "
    "a RISK LEVEL, your CONFIDENCE, and a concrete FIX PLAN.\n"
    "Risk levels: low (safe to auto-fix), medium (ask a human yes/no), "
    "high (needs explicit approval), critical (stop everything, no auto-fix).\n"
    "Respond with ONLY a JSON object, no prose, with exactly these keys: "
    '{"root_cause": str, "risk_level": "low|medium|high|critical", '
    '"confidence": float 0-1, "fix_plan": str}.'
)


def _build_openai():
    return ChatOpenAI(
        model=settings.OPENAI_MODEL,
        temperature=settings.OPENAI_TEMPERATURE,
        api_key=settings.OPENAI_API_KEY,
    )


def _build_groq() -> ChatGroq:
    return ChatGroq(
        model=settings.GROQ_MODEL,
        temperature=settings.GROQ_TEMPERATURE,
        api_key=settings.GROQ_API_KEY,
    )


# Provider builders keyed by name. Each is built lazily only when its key is set.
_BUILDERS = {"openai": (settings.openai_ready, _build_openai),
             "groq": (settings.groq_ready, _build_groq)}


def _provider_order() -> list[str]:
    """OpenAI first, Groq as fallback — unless LLM_PROVIDER forces one provider.
    Only providers whose API key is configured are included."""
    if settings.LLM_PROVIDER in _BUILDERS:
        order = [settings.LLM_PROVIDER] + [p for p in _BUILDERS if p != settings.LLM_PROVIDER]
    else:
        order = ["openai", "groq"]
    return [name for name in order if _BUILDERS[name][0]]


def _build_llm():
    """Build the single most-preferred available LLM (used when a caller wants one
    provider). Prefer `diagnose()`'s built-in fallback for resilience."""
    order = _provider_order()
    if not order:
        raise RuntimeError(
            "No LLM key set. Copy .env.example to .env and add OPENAI_API_KEY "
            "(preferred) or GROQ_API_KEY."
        )
    return _BUILDERS[order[0]][1]()


def _facts_prompt(
    pipeline_name: str,
    failure_type: str,
    facts: dict[str, Any],
    similar: list[dict[str, Any]],
) -> str:
    lines = [
        f"Pipeline: {pipeline_name}",
        f"Detected failure type: {failure_type}",
        "Run facts:",
        json.dumps(facts, indent=2, default=str),
    ]
    if similar:
        lines.append("\nSimilar past incidents (for reference):")
        for inc in similar:
            lines.append(
                f"- cause: {inc.get('root_cause')} | risk: {inc.get('risk_level')} "
                f"| fix: {inc.get('fix_plan')}"
            )
    else:
        lines.append("\nNo similar past incidents on record.")
    lines.append(
        f"\nThe typical risk for {failure_type} is "
        f"'{DEFAULT_RISK.get(failure_type, RiskLevel.MEDIUM).value}', but judge from the facts."
    )
    return "\n".join(lines)


def _parse_response(text: str) -> dict[str, Any]:
    """Extract the JSON object from the LLM reply, tolerating stray prose/fences."""
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ValueError(f"No JSON object in LLM response: {text[:200]!r}")
    return json.loads(match.group(0))


def _normalize(raw: dict[str, Any], failure_type: str) -> dict[str, Any]:
    """Validate + clamp the LLM output; apply the low-confidence risk bump."""
    # confidence -> float in [0, 1]
    try:
        confidence = float(raw.get("confidence", 0.5))
    except (TypeError, ValueError):
        confidence = 0.5
    confidence = max(0.0, min(1.0, confidence))

    # risk -> a valid RiskLevel, else the anchor for this failure type
    risk_raw = str(raw.get("risk_level", "")).strip().lower()
    try:
        risk = RiskLevel(risk_raw)
    except ValueError:
        risk = DEFAULT_RISK.get(failure_type, RiskLevel.MEDIUM)

    # low confidence => bump one tier (never above CRITICAL)
    bumped = False
    if confidence < CONFIDENCE_BUMP_THRESHOLD:
        idx = min(_RISK_LADDER.index(risk) + 1, len(_RISK_LADDER) - 1)
        if _RISK_LADDER[idx] is not risk:
            bumped = True
        risk = _RISK_LADDER[idx]

    return {
        "root_cause": str(raw.get("root_cause", "")).strip() or "unspecified",
        "risk_level": risk.value,
        "confidence": confidence,
        "fix_plan": str(raw.get("fix_plan", "")).strip() or "no fix plan provided",
        "risk_bumped_for_low_confidence": bumped,
    }


def diagnose(
    *,
    pipeline_name: str,
    failure_type: str,
    facts: dict[str, Any],
    similar_incidents: Optional[list[dict[str, Any]]] = None,
    llm: Optional[Any] = None,
) -> dict[str, Any]:
    """Produce a structured diagnosis.

    Provider strategy: OpenAI is tried first, Groq is the fallback (order set by
    `LLM_PROVIDER`). If the preferred provider raises at call time — bad key,
    quota, network, or unparseable output — the next configured provider is
    tried before giving up. `llm` is injectable for testing (bypasses fallback).
    """
    prompt = _facts_prompt(pipeline_name, failure_type, facts, similar_incidents or [])
    messages = [("system", _SYSTEM_PROMPT), ("human", prompt)]

    # Injected LLM (tests): use it directly, no fallback.
    if llm is not None:
        raw = _parse_response(llm.invoke(messages).content)
        return _normalize(raw, failure_type)

    order = _provider_order()
    if not order:
        raise RuntimeError(
            "No LLM key set. Copy .env.example to .env and add OPENAI_API_KEY "
            "(preferred) or GROQ_API_KEY."
        )

    errors: list[str] = []
    for name in order:
        try:
            provider = _BUILDERS[name][1]()
            raw = _parse_response(provider.invoke(messages).content)
            return _normalize(raw, failure_type)
        except Exception as exc:  # noqa: BLE001 — try the next provider on any failure
            errors.append(f"{name}: {type(exc).__name__}: {exc}")
            continue

    raise RuntimeError(
        "All diagnose LLM providers failed (" + " | ".join(errors) + ")"
    )


def diagnose_node(state: SentinelState) -> dict[str, Any]:
    """LangGraph node. Runs only when Monitor flagged a failure; otherwise a no-op."""
    if not state.get("failure_detected"):
        return {"node_trace": ["diagnose:skipped"]}

    pipeline_name = state["pipeline_name"]
    failure_type = state["failure_type"]
    facts = {
        "status": state.get("pipeline_status"),
        "rows_expected": state.get("rows_expected"),
        "rows_actual": state.get("rows_actual"),
        "columns_expected": state.get("columns_expected"),
        "columns_actual": state.get("columns_actual"),
        "execution_time": state.get("execution_time"),
        "error_message": state.get("error_message"),
    }
    similar = [dict(r) for r in database.get_similar_incidents(pipeline_name, failure_type)]

    result = diagnose(
        pipeline_name=pipeline_name,
        failure_type=failure_type,
        facts=facts,
        similar_incidents=similar,
    )
    return {
        "root_cause": result["root_cause"],
        "risk_level": result["risk_level"],
        "confidence": result["confidence"],
        "fix_plan": result["fix_plan"],
        "similar_incidents": similar,
        "node_trace": ["diagnose"],
    }


def build_diagnose_graph():
    """Standalone START -> diagnose -> END graph for Phase 3 testing.
    Phase 4 chains monitor -> diagnose in the full pipeline."""
    graph = StateGraph(SentinelState)
    graph.add_node("diagnose", diagnose_node)
    graph.add_edge(START, "diagnose")
    graph.add_edge("diagnose", END)
    return graph.compile()
