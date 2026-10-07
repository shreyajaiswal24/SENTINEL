"""Phase 6 evaluation: voice HITL, fully offline (mocked speak/listen).

Verifies, without any audio device or API key:
  * the decision parser maps spoken phrases to approve/reject correctly,
  * request_approval() speaks, listens, retries on unclear input, and returns
    the right boolean,
  * hitl_node respects an injected decision, and falls back to AUTO_APPROVE when
    VOICE_MODE is "off".

    python phase6.py
"""
from __future__ import annotations

import voice.hitl_voice as hv
from agents.hitl import AUTO_APPROVE_DEFAULT, hitl_node
from utils.state import RiskLevel
from voice.hitl_voice import _parse_decision, request_approval

SPOKEN: list[str] = []  # captures what the queen "said"


def _mock_speak(text: str) -> None:
    SPOKEN.append(text)


def _scripted_listen(replies: list[str]):
    it = iter(replies)
    return lambda prompt="": next(it, "")


def _med(approved=None):
    s = {"pipeline_name": "sales", "failure_type": "row_count_anomaly",
         "risk_level": RiskLevel.MEDIUM.value, "root_cause": "fewer rows",
         "fix_plan": "re-run pipeline"}
    if approved is not None:
        s["human_approved"] = approved
    return s


def test_parser() -> bool:
    print("=== Decision parser ===")
    cases = [
        ("yes", True), ("Yes, go ahead.", True), ("approve", True),
        ("do it", True), ("no", False), ("Reject!", False),
        ("stop please", False), ("hmm what", None),
    ]
    ok = True
    for text, expected in cases:
        got = _parse_decision(text)
        good = got is expected
        ok &= good
        print(f"  [{'OK ' if good else 'XX '}] {text!r:<20} -> {got}")
    return ok


def test_request_approval() -> bool:
    print("\n=== request_approval (mocked voice) ===")
    ok = True
    # straightforward yes
    hv.speak, hv.listen = _mock_speak, _scripted_listen(["yes"])
    r = request_approval(_med())
    ok &= (r is True)
    print(f"  [{'OK ' if r is True else 'XX '}] 'yes' -> {r}")
    # reject
    hv.listen = _scripted_listen(["no"])
    r = request_approval(_med())
    ok &= (r is False)
    print(f"  [{'OK ' if r is False else 'XX '}] 'no' -> {r}")
    # unclear then approve -> retry path, ends True
    SPOKEN.clear()
    hv.listen = _scripted_listen(["uhh", "approve"])
    r = request_approval(_med())
    retried = any("didn't catch" in s for s in SPOKEN)
    ok &= (r is True and retried)
    print(f"  [{'OK ' if (r is True and retried) else 'XX '}] 'uhh' then 'approve' -> {r} (retried={retried})")
    # never clear -> safe default False
    hv.listen = _scripted_listen(["what", "huh", "eh"])
    r = request_approval(_med())
    ok &= (r is False)
    print(f"  [{'OK ' if r is False else 'XX '}] all unclear -> {r} (safe default)")
    return ok


def test_hitl_node() -> bool:
    print("\n=== hitl_node ===")
    ok = True
    # injected decision is respected (no prompting)
    out = hitl_node(_med(approved=False))
    good = out["human_approved"] is False and out["human_approval_required"]
    ok &= good
    print(f"  [{'OK ' if good else 'XX '}] injected reject respected -> {out['human_approved']}")
    # VOICE_MODE 'off' (default in tests) -> AUTO_APPROVE_DEFAULT
    out = hitl_node(_med())
    good = out["human_approved"] is AUTO_APPROVE_DEFAULT
    ok &= good
    print(f"  [{'OK ' if good else 'XX '}] headless default -> {out['human_approved']}")
    return ok


def main() -> None:
    all_ok = test_parser() and test_request_approval() and test_hitl_node()
    print("\nPhase 6", "OK." if all_ok else "FAILED — see XX rows above.")
    raise SystemExit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
