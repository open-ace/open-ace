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
