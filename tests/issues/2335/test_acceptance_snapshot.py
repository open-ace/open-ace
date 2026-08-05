from app.modules.workspace.autonomous.acceptance_snapshot import (
    AcceptanceSnapshot,
    hash_snapshot,
    parse_acceptance_snapshot,
)


def test_parses_convention_sections():
    body = """Some intro.

## Scope
- `app/services/retention.py`
- `app/routes/legal.py`

## 验收标准
- [ ] retention manager runs on cron
- [ ] DELETE is gated by legal-hold

## 不在 Scope
- UI changes
"""
    snap = parse_acceptance_snapshot(body)
    assert snap.source == "convention"
    assert snap.confidence == "high"
    assert "app/services/retention.py" in snap.required_paths
    assert "app/routes/legal.py" in snap.required_paths
    assert len(snap.checklist) == 2
    assert "retention manager runs on cron" in snap.checklist
    assert snap.non_scope == ["UI changes"]


def test_missing_sections_flagged_for_llm_extraction():
    snap = parse_acceptance_snapshot("just a free-form issue, no sections")
    assert snap.source == "missing"
    assert snap.required_paths == []
    assert snap.checklist == []


def test_closure_constraint_detected():
    body = "## Scope\n- `x.py`\n\n禁止阶段性关闭。"
    snap = parse_acceptance_snapshot(body)
    assert snap.closure_constraints is True


def test_hash_is_stable_and_order_independent():
    a = parse_acceptance_snapshot("## Scope\n- `a.py`\n- `b.py`\n## 验收标准\n- [ ] one\n")
    b = parse_acceptance_snapshot("## Scope\n- `b.py`\n- `a.py`\n## 验收标准\n- [ ] one\n")
    assert hash_snapshot(a) == hash_snapshot(b)
    assert isinstance(hash_snapshot(a), str) and len(hash_snapshot(a)) == 64
