"""
Open ACE - Session-average root-cause diagnostics

Pure helper used by the data-gathering gate (runbook) for the
"会话统计 / per-session average near zero" investigation.

Background
----------
``avg_*_per_session = numerator / total_sessions``. The legacy
``total_sessions = unique_days * unique_tools`` is a *small* denominator
(hundreds), so a small denominator + a large numerator can never yield a
near-zero average. "Near zero" therefore implies one of a few root causes,
and -- crucially -- *switching to the real conversation count only helps in
one of them*. This classifier decides which one applies from sampled values
before any code is changed, so we never "blind-fix" the denominator.

The function is intentionally pure (no DB, no side effects) so it can be
unit-tested directly (see tests/unit/test_session_diagnostics.py).
"""

from __future__ import annotations

from typing import Any


def classify_session_avg_rootcause(
    sample: dict[str, Any],
    *,
    token_min: float = 1.0,
    message_min: float = 1.0,
    distinct_min: int = 1,
    ratio_k: float = 10.0,
) -> dict[str, Any]:
    """
    Classify why per-session averages may be near zero.

    Expected keys in ``sample`` (all optional, missing treated as 0):
        total_tokens, total_messages -- numerator candidates (from
            daily_stats aggregates).
        unique_days, unique_tools    -- legacy approximation inputs.
        distinct                     -- real distinct conversation count
            (method B ``COUNT(DISTINCT COALESCE(...))``), the denominator
            the fix would adopt.

    Returns ``{"class", "reason", "proceed"}`` where ``class`` is one of
    ``"a" | "b" | "c" | "d" | "unknown"`` and ``proceed`` indicates whether
    switching the denominator to the real ``distinct`` is the correct fix.

    Order is significant: (b) and (d) are evaluated before (a)/(c) so that a
    near-zero ``distinct`` is not mis-classified as (a).
    """
    total_tokens = float(sample.get("total_tokens", 0) or 0)
    total_messages = float(sample.get("total_messages", 0) or 0)
    unique_days = int(sample.get("unique_days", 0) or 0)
    unique_tools = int(sample.get("unique_tools", 0) or 0)
    distinct = int(sample.get("distinct", 0) or 0)

    approx = unique_days * unique_tools
    has_activity = total_tokens >= token_min or total_messages >= message_min

    # (b) numerator itself is ~0 -> fixing the denominator is pointless;
    #     investigate the daily_stats aggregation pipeline instead.
    if total_tokens < token_min and total_messages < message_min:
        return {
            "class": "b",
            "reason": (
                "numerator ~0 (total_tokens/total_messages both below "
                f"{token_min}/{message_min}); fixing denominator is ineffective - "
                "investigate daily_stats aggregation (needs_refresh / refresh_stats)."
            ),
            "proceed": False,
        }

    # (d) activity exists but distinct ~0 -> messages lack session ids;
    #     avg would still be 0 (0/0 guarded) after the fix -> data-quality line.
    if distinct < distinct_min and has_activity:
        return {
            "class": "d",
            "reason": (
                f"distinct conversations below {distinct_min} despite activity; "
                "messages likely lack session ids -> the fix would still show 0; "
                "investigate session-id ingestion."
            ),
            "proceed": False,
        }

    # (c) real >> approx -> switching to the real denominator makes the
    #     average SMALLER (closer to 0) -> opposite of the goal.
    if approx > 0 and distinct > 0 and approx < distinct / ratio_k:
        return {
            "class": "c",
            "reason": (
                f"real distinct ({distinct}) >> approximation ({approx}); switching "
                "denominator drives the average closer to 0 - requirement's causal "
                "model needs redefining before changing code."
            ),
            "proceed": False,
        }

    # (a) approx >> real -> the legacy denominator was inflated (e.g. tool_name
    #     not normalized, or "All" range spanning the whole year); the real
    #     denominator is smaller -> average becomes larger. This is the only
    #     case where the fix is correct.
    if distinct > 0 and approx > distinct * ratio_k:
        return {
            "class": "a",
            "reason": (
                f"approximation ({approx}) >> real distinct ({distinct}); legacy "
                "denominator was inflated - switching to real count raises the "
                "average. Fix direction is correct."
            ),
            "proceed": True,
        }

    return {
        "class": "unknown",
        "reason": (
            f"inconclusive: approx={approx}, distinct={distinct}, "
            f"tokens={total_tokens}, messages={total_messages}. Manual review needed."
        ),
        "proceed": False,
    }
