from app.modules.workspace.autonomous.orchestrator import (
    _COMPLETED_TERMINAL_PHASES,
    PHASE_ORDER,
    PHASE_STATUS_MAP,
)
from app.modules.workspace.autonomous.phases import resolve_phase_handler


def test_acceptance_verification_phase_registered_and_ordered():
    assert "acceptance_verification" in PHASE_ORDER
    assert PHASE_ORDER.index("acceptance_verification") > PHASE_ORDER.index("merge")
    assert PHASE_STATUS_MAP["acceptance_verification"] == "verification_pending"
    assert resolve_phase_handler("acceptance_verification") is not None


def test_terminal_set_includes_acceptance_verification():
    # merge -> acceptance_verification -> completed; the workflow rests at
    # current_phase="acceptance_verification" on confirmed, so it must be a valid
    # terminal real phase. "merge"/"completed" retained for the legacy paths.
    assert "acceptance_verification" in _COMPLETED_TERMINAL_PHASES
    assert "completed" in _COMPLETED_TERMINAL_PHASES
