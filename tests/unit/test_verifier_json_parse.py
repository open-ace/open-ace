"""Regression: the acceptance verifier's JSON extraction must tolerate glm output.

The verifier agent (glm-5 on prod) routinely deviates from "ONLY a fenced JSON
block": it prefaces with prose, omits/mismatches fences, and emits trailing
commas. The old parser grabbed a fenced block else fell back to the WHOLE text
under strict ``json.loads`` — so any deviation raised "verification agent output
was not valid JSON", exhausted the infra-retry budget, and paused the workflow
at acceptance_verification (b48179df / 6584f242, #29).
"""

import pytest

pytestmark = [pytest.mark.regression]


def _extract(text):
    from app.modules.workspace.autonomous.orchestrator import _extract_verifier_json

    return _extract_verifier_json(text)


def test_clean_fenced_json_still_works():
    text = '```json\n{"verdicts": [{"item": "a", "verdict": "confirmed"}]}\n```'
    assert _extract(text) == {"verdicts": [{"item": "a", "verdict": "confirmed"}]}


def test_fenced_json_with_prose_preface():
    text = 'Here is my verification:\n```json\n{"verdicts": [], "snapshot": null}\n```'
    assert _extract(text) == {"verdicts": [], "snapshot": None}


def test_trailing_commas_tolerated():
    # glm family frequently emits {"a":1,} style trailing commas.
    text = (
        '```json\n{"verdicts": [{"item": "a", "verdict": "confirmed",},], "snapshot": null,}\n```'
    )
    result = _extract(text)
    assert result is not None
    assert result["verdicts"] == [{"item": "a", "verdict": "confirmed"}]
    assert result["snapshot"] is None


def test_unfenced_prose_wrapped_json():
    text = (
        "I verified the merged changes against the snapshot. "
        '{"verdicts": [{"item": "a", "verdict": "confirmed"}], "snapshot": null} '
        "That is all."
    )
    assert _extract(text) == {"verdicts": [{"item": "a", "verdict": "confirmed"}], "snapshot": None}


def test_trailing_comma_cleanup_preserves_comma_brace_inside_strings():
    # Real trailing comma AND a string value containing ",}" — the in-string
    # ",}" must be preserved, only the structural trailing comma stripped.
    text = '{"verdicts": [{"item": "a", "note": "see file,}"}], "snapshot": null,}'
    result = _extract(text)
    assert result is not None
    assert result["verdicts"][0]["note"] == "see file,}"
    assert result["snapshot"] is None


def test_braces_inside_string_values_do_not_break_depth():
    text = '```json\n{"verdicts": [{"item": "a", "evidence": "uses {brace} here"}]}\n```'
    result = _extract(text)
    assert result is not None
    assert result["verdicts"][0]["evidence"] == "uses {brace} here"


def test_pure_prose_with_no_json_returns_none():
    text = "The change looks correct but I could not produce structured output."
    assert _extract(text) is None


def test_last_fenced_block_wins_when_agent_prefaces_a_partial():
    # Agent emits a partial/earlier block, then the real one.
    text = (
        '```json\n{"verdicts": [{"item": "partial"}\n```\n'
        'final answer:\n```json\n{"verdicts": [{"item": "a", "verdict": "confirmed"}]}\n```'
    )
    result = _extract(text)
    assert result == {"verdicts": [{"item": "a", "verdict": "confirmed"}]}


# --- Truncation recovery (prod: glm-5 sometimes starts JSON but the output is
# cut off mid-object — token-budget exhaustion leaves an unbalanced fence).
# Recovery must keep the COMPLETE leading verdicts and drop the trailing
# incomplete one. Safe: a dropped checklist verdict becomes indeterminate
# downstream (never a false confirmed). #2394/#2328/#2349 infra-exhaustion.


def test_truncated_fenced_json_recovers_complete_prefix():
    # Model wrote verdict "a" fully, then ran out of tokens mid-"rejected" on "b".
    text = (
        "Based on my verification:\n```json\n"
        '{"verdicts": [{"item": "a", "verdict": "confirmed"}, '
        '{"item": "b", "verdict": "reject'
    )
    result = _extract(text)
    assert result is not None
    items = [v["item"] for v in result["verdicts"]]
    assert "a" in items  # complete verdict recovered
    assert "b" not in items  # incomplete trailing verdict dropped


def test_truncated_unfenced_json_recovers():
    # Prose preface + an unfenced JSON object cut off before the closing brace.
    text = (
        "Here is my assessment: "
        '{"verdicts": [{"item": "a", "verdict": "confirmed"}, '
        '{"item": "b", "verdict": "rejected"}'
    )
    result = _extract(text)
    assert result is not None
    assert result["verdicts"] == [
        {"item": "a", "verdict": "confirmed"},
        {"item": "b", "verdict": "rejected"},
    ]


def test_truncation_mid_evidence_keeps_complete_verdict_prefix():
    # Truncation inside a verdict's evidence array: keep the verdict with the
    # evidence completed so far, drop the dangling incomplete ref.
    text = (
        "```json\n"
        '{"verdicts": [{"item": "a", "verdict": "confirmed", '
        '"evidence": [{"ref": "file.py:10"}, {"ref": "file.py'
    )
    result = _extract(text)
    assert result is not None
    assert len(result["verdicts"]) == 1
    assert result["verdicts"][0]["item"] == "a"
    # The first complete evidence ref survived; the truncated one is gone.
    refs = [e["ref"] for e in result["verdicts"][0]["evidence"]]
    assert "file.py:10" in refs


def test_truncated_object_with_no_complete_element_returns_none():
    # Cut off before ANY complete verdict — nothing safe to recover.
    text = '```json\n{"verdicts": [{"item": "a", "verdict": "conf'
    assert _extract(text) is None
