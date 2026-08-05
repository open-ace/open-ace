from unittest.mock import MagicMock

from app.modules.workspace.autonomous.evidence import Verdict
from app.modules.workspace.autonomous.phases.acceptance_verification import run_scope_gate


def test_required_path_present_is_confirmed():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/services/retention.py", "README.md"]
    verdicts = run_scope_gate(gh, ["app/services/retention.py"], "base", "merge")
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.CONFIRMED


def test_required_path_missing_is_rejected():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["README.md"]
    verdicts = run_scope_gate(gh, ["app/services/retention.py"], "base", "merge")
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "app/services/retention.py" in verdicts[0].item
    assert verdicts[0].evidence  # carries the missing-path ref


def test_glob_matches_changed_path():
    gh = MagicMock()
    gh.get_changed_files.return_value = ["app/services/retention.py", "app/services/legal.py"]
    verdicts = run_scope_gate(gh, ["app/services/*.py"], "base", "merge")
    assert all(v.verdict is Verdict.CONFIRMED for v in verdicts)


def test_no_required_paths_returns_empty():
    gh = MagicMock()
    assert run_scope_gate(gh, [], "base", "merge") == []
