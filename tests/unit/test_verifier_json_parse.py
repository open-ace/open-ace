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


# --- Unescaped inner ASCII double quotes (#2867). glm reproduces the Chinese
# acceptance criteria verbatim; when a checklist item quotes a UI label the
# inner 「"..."」 quotes land unescaped, so strict json.loads raises and the
# whole (actually-passing) verdict batch was misjudged as an infra failure.
# The parser must escape structurally-inner quotes and recover the verdicts,
# while still returning None (fail-closed) when nothing valid can be recovered.


def test_unescaped_inner_quotes_in_string_value_recovered():
    # An item value that quotes a UI label: the inner quotes are unescaped.
    text = (
        "```json\n"
        '{"verdicts": [{"item": "`platform_admin` 可以看到并进入"租户管理"。", '
        '"verdict": "confirmed"}], "snapshot": null}\n'
        "```"
    )
    result = _extract(text)
    assert result is not None
    assert result["verdicts"][0]["verdict"] == "confirmed"
    # The inner label is preserved intact in the recovered value.
    assert '"租户管理"' in result["verdicts"][0]["item"]
    assert result["snapshot"] is None


def test_2756_style_full_batch_recovers_all_confirmed():
    # Mirrors the #2756 raw output: 5/5 confirmed, several items quoting labels
    # with unescaped inner quotes, terse evidence refs, prose preface + fence.
    items = [
        '{"item": "`platform_admin` 可以看到并进入"租户管理"。", "verdict": "confirmed", '
        '"evidence": [{"ref": "app/routes/tenant.py:42", "note": "route guarded"}], '
        '"rationale": "读到 admin 分支"}',
        '{"item": "普通用户访问"租户管理"返回 403。", "verdict": "confirmed", '
        '"evidence": [{"ref": "app/routes/tenant.py:55", "note": "403 path"}], '
        '"rationale": "deny 分支"}',
        '{"item": "列表按 "tenant_id" 过滤。", "verdict": "confirmed", '
        '"evidence": [{"ref": "app/repositories/tenant.py:8", "note": "where tenant_id"}], '
        '"rationale": "SQL where"}',
        '{"item": "创建时写入 "tenant_id"。", "verdict": "confirmed", '
        '"evidence": [{"ref": "app/repositories/tenant.py:20", "note": "insert col"}], '
        '"rationale": "insert"}',
        '{"item": "前端菜单显示"租户管理"入口。", "verdict": "confirmed", '
        '"evidence": [{"ref": "web/src/Nav.tsx:12", "note": "menu item"}], '
        '"rationale": "nav"}',
    ]
    text = (
        "I verified the merged changes against the acceptance snapshot.\n"
        "```json\n"
        '{"verdicts": [' + ", ".join(items) + '], "snapshot": null}\n'
        "```"
    )
    result = _extract(text)
    assert result is not None
    verdicts = result["verdicts"]
    assert len(verdicts) == 5
    assert all(v["verdict"] == "confirmed" for v in verdicts)


def test_unescaped_inner_quotes_combined_with_trailing_comma():
    # fence + prose preface + trailing comma + unescaped inner quotes together.
    text = (
        "Result below:\n"
        "```json\n"
        '{"verdicts": [{"item": "进入"设置"页", "verdict": "confirmed",},], "snapshot": null,}\n'
        "```"
    )
    result = _extract(text)
    assert result is not None
    assert result["verdicts"][0]["verdict"] == "confirmed"
    assert '"设置"' in result["verdicts"][0]["item"]
    assert result["snapshot"] is None


def test_unescaped_inner_quotes_unfenced_prose_wrapped():
    # Same defect but unfenced, wrapped in prose on both sides.
    text = (
        "Here is my assessment: "
        '{"verdicts": [{"item": "点击"保存"按钮生效", "verdict": "confirmed"}], "snapshot": null}'
        " Done."
    )
    result = _extract(text)
    assert result is not None
    assert '"保存"' in result["verdicts"][0]["item"]


def test_unrecoverable_garbage_still_returns_none():
    # A brace-y blob that is not recoverable JSON must still fail closed — the
    # heuristic must never fabricate a verdict out of noise.
    text = '```json\n{this is "not: json at "all }{ }"\n```'
    assert _extract(text) is None


def test_parse_verifier_output_tags_unparseable_kind():
    # Producer side of the #2867 deterministic-retry contract: when extraction
    # fails, _parse_verifier_output must return infra_error_kind ==
    # "unparseable_output" — the exact string acceptance_verification's early
    # cap keys on. Anchors the cross-file literal so a rename on one side is
    # caught by a test rather than by a stuck prod workflow.
    from unittest.mock import MagicMock

    from app.modules.workspace.autonomous.orchestrator import AutonomousOrchestrator

    orch = AutonomousOrchestrator.__new__(AutonomousOrchestrator)
    orch._artifact_text = MagicMock(return_value="prose only, no json object at all")
    out = orch._parse_verifier_output(MagicMock())
    assert out["infra_error"] == "verification agent output was not valid JSON"
    assert out["infra_error_kind"] == "unparseable_output"
    assert out["verdicts"] == []

    # And the success path stays clean: a recoverable batch parses with no
    # infra_error / kind, so the cap logic never engages on a real verdict.
    orch._artifact_text = MagicMock(
        return_value='{"verdicts": [{"item": "进入"设置"页", "verdict": "confirmed"}], "snapshot": null}'
    )
    good = orch._parse_verifier_output(MagicMock())
    assert good.get("infra_error") is None
    assert good.get("infra_error_kind") is None
    assert good["verdicts"][0]["verdict"] == "confirmed"
