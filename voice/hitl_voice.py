"""Voice human-in-the-loop approval (Phase 6).

The queen states the incident and asks for a decision, with phrasing scaled to the
risk tier:
    MEDIUM -> "yes / no"
    HIGH   -> "approve / reject"

Returns True (proceed with the fix) or False (do not). Affirmative/negative words
are matched loosely so "yes", "go ahead", "approve", "do it" all mean proceed.
"""
from __future__ import annotations

from typing import Optional

from utils.state import RiskLevel, SentinelState
from voice.speech import listen, speak

_AFFIRMATIVE = {"yes", "yeah", "yep", "approve", "approved", "ok", "okay", "sure",
                "confirm", "confirmed", "go", "proceed", "do", "fix"}
_NEGATIVE = {"no", "nope", "reject", "rejected", "stop", "deny", "denied",
             "negative", "cancel", "don't", "dont"}


def _parse_decision(text: str) -> Optional[bool]:
    """Map a spoken/typed reply to approve (True) / reject (False) / unclear (None).

    Safety-first for a HITL gate: negation must never read as approval.
      * any explicit negative word ("no", "reject", ...) -> False, even if a stray
        affirmative-ish token is also present ("no, do not apply" has "do").
      * a negation cue ("not", "n't") with no clear negative word -> None (unclear;
        request_approval will re-ask, then safe-default to no-apply) so "not sure"
        and "do not" never approve via the "sure"/"do" tokens.
      * only an unambiguous affirmative with no negation -> True.
    """
    words = {w.strip(".,!?").lower() for w in text.split()}
    if words & _NEGATIVE:
        return False
    if "not" in words or any(w.endswith("n't") for w in words):
        return None
    if words & _AFFIRMATIVE:
        return True
    return None


def _script(state: SentinelState) -> tuple[str, str]:
    """(spoken announcement, decision verbs) for this incident."""
    high = state.get("risk_level") == RiskLevel.HIGH.value
    verbs = "approve or reject" if high else "yes or no"
    announce = (
        f"Attention. The {state.get('pipeline_name')} pipeline has a "
        f"{state.get('failure_type', 'failure').replace('_', ' ')}. "
        f"Risk is {state.get('risk_level')}. "
        f"Root cause: {state.get('root_cause', 'unknown')}. "
        f"Proposed fix: {state.get('fix_plan', 'none')}. "
        f"Do you {verbs}?"
    )
    return announce, verbs


def request_approval(state: SentinelState, max_retries: int = 2) -> bool:
    """Speak the incident, listen for a decision, retry on an unclear answer."""
    announce, verbs = _script(state)
    speak(announce)
    for _ in range(max_retries + 1):
        reply = listen(f"({verbs}): ")
        decision = _parse_decision(reply)
        if decision is not None:
            speak("Understood. Proceeding with the fix." if decision
                  else "Understood. Holding off.")
            return decision
        speak(f"Sorry, I didn't catch that. Please say {verbs}.")
    # unclear after retries -> safe default is NOT to auto-apply
    speak("No clear decision. I will not apply the fix.")
    return False
