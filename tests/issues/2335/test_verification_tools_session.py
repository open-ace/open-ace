import copy

from app.modules.workspace.autonomous.constants import (
    REVIEW_ALLOWED_TOOLS,
    VERIFICATION_ALLOWED_TOOLS,
)


def test_verification_adds_bash_to_review_set():
    review = copy.deepcopy(REVIEW_ALLOWED_TOOLS["claude-code"])
    verif = VERIFICATION_ALLOWED_TOOLS["claude-code"]
    for t in review:
        assert t in verif
    assert "Bash" in verif
    assert "Write" not in verif and "Edit" not in verif


def test_session_line_registry_includes_verification():
    from app.modules.workspace.autonomous.orchestrator import SESSION_LINE_FIELDS

    assert SESSION_LINE_FIELDS.get("verification") == "verification_session_id"
