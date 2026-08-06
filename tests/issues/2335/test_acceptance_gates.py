"""Unit tests for the mechanical acceptance gates (#2335 S4).

Each gate is a conservative static-analysis check: CONFIRMED on a positive
signal, REJECTED only on a definitive negative, INDETERMINATE when uncertain
(never a false REJECTED that blocks legit work, never a false CONFIRMED).

The gates are unit-tested by injecting a dict-backed ``read_file`` so no real
git/IO is required.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from app.modules.workspace.autonomous.acceptance_gates import (
    call_chain_gate,
    deployment_gate,
    legacy_pattern_gate,
    negative_test_gate,
    regression_gate,
    run_mechanical_gates,
)
from app.modules.workspace.autonomous.acceptance_snapshot import AcceptanceSnapshot
from app.modules.workspace.autonomous.evidence import Verdict


def _gh(changed: list[str]) -> MagicMock:
    gh = MagicMock()
    gh.get_changed_files.return_value = changed
    return gh


def _reader(files: dict[str, str]):
    """Return a read_file(path) callable backed by a {path: content} dict.

    Returns '' for unknown paths so regex greps simply don't match (matches the
    git-show fallback behavior for a path that doesn't exist at the merge SHA).
    """

    def _read(path: str) -> str:
        return files.get(path, "")

    return _read


# -- negative_test_gate -------------------------------------------------------


def test_negative_test_silent_when_no_security_files():
    gh = _gh(["README.md", "app/services/legal.py"])
    verdicts = negative_test_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    # Not applicable -> silent (no verdict), so it doesn't flip a confirmed issue.
    assert verdicts == []


def test_negative_test_confirmed_when_security_file_has_failure_test():
    gh = _gh(["app/services/security/auth.py", "tests/test_auth.py"])
    files = {
        "tests/test_auth.py": (
            "def test_auth_fail():\n"
            "    with pytest.raises(AuthError):\n"
            "        login('bad')\n"
        )
    }
    verdicts = negative_test_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.CONFIRMED


def test_negative_test_rejected_when_security_file_has_no_test():
    gh = _gh(["app/services/security/auth.py", "README.md"])
    verdicts = negative_test_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "auth.py" in verdicts[0].item
    assert verdicts[0].evidence  # carries the missing-test ref


def test_negative_test_silent_for_non_security_file_without_test():
    # A non-security file without a test must NOT trigger REJECTED (conservative).
    gh = _gh(["app/services/legal.py"])
    verdicts = negative_test_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    assert verdicts == []


# -- legacy_pattern_gate ------------------------------------------------------


def test_legacy_pattern_confirmed_when_clean():
    gh = _gh(["app/services/retention.py"])
    files = {"app/services/retention.py": "def archive(x):\n    return x\n"}
    verdicts = legacy_pattern_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.CONFIRMED


def test_legacy_pattern_rejected_when_banned_symbol_present():
    gh = _gh(["app/services/security/keys.py"])
    files = {"app/services/security/keys.py": "_sync_ssh_keys_legacy()\n"}
    verdicts = legacy_pattern_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.REJECTED
    note = verdicts[0].evidence[0].get("note", "") + verdicts[0].item
    assert "_sync_ssh_keys_legacy" in note


def test_legacy_pattern_rejected_when_lock_failure_proceeds():
    gh = _gh(["app/services/lock.py"])
    files = {"app/services/lock.py": "# could not acquire lock, proceeding anyway\n"}
    verdicts = legacy_pattern_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert verdicts[0].verdict is Verdict.REJECTED


def test_legacy_pattern_indeterminate_when_cannot_read():
    # No read_file provided and gh has no show helper -> can't read content.
    gh = _gh(["app/services/security/x.py"])
    verdicts = legacy_pattern_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=None)
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.INDETERMINATE


def test_legacy_pattern_ignores_test_files():
    gh = _gh(["tests/test_legacy.py"])
    files = {"tests/test_legacy.py": "_sync_ssh_keys_legacy()\n"}
    verdicts = legacy_pattern_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    # Test files are scanned by the negative_test_gate, not the legacy gate; with
    # no production files the gate is silent.
    assert verdicts == []


# -- call_chain_gate ----------------------------------------------------------


def test_call_chain_silent_when_no_new_modules():
    gh = _gh(["README.md"])
    verdicts = call_chain_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    assert verdicts == []


def test_call_chain_confirmed_when_new_repo_has_production_caller():
    gh = _gh(
        [
            "app/repositories/audit_repo.py",
            "app/services/audit_service.py",
        ]
    )
    files = {
        "app/repositories/audit_repo.py": "class AuditRepo:\n    pass\n",
        "app/services/audit_service.py": "from app.repositories.audit_repo import AuditRepo\n",
    }
    verdicts = call_chain_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.CONFIRMED


def test_call_chain_rejected_when_new_repo_has_only_test_ref():
    gh = _gh(
        [
            "app/repositories/audit_repo.py",
            "tests/test_audit.py",
        ]
    )
    files = {
        "app/repositories/audit_repo.py": "class AuditRepo:\n    pass\n",
        "tests/test_audit.py": "from app.repositories.audit_repo import AuditRepo\n",
    }
    verdicts = call_chain_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "audit_repo" in verdicts[0].item


def test_call_chain_confirmed_when_caller_in_changed_tree():
    gh = _gh(
        [
            "app/repositories/billing_repo.py",
            "app/services/billing.py",
        ]
    )
    files = {
        "app/repositories/billing_repo.py": "class BillingRepo:\n    pass\n",
        "app/services/billing.py": "from app.repositories.billing_repo import BillingRepo\n",
    }
    verdicts = call_chain_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert verdicts[0].verdict is Verdict.CONFIRMED


# -- deployment_gate ----------------------------------------------------------


def test_deployment_indeterminate_for_deploy_path_without_migration():
    # A deploy-critical path (sudoers) changed with no migration -> genuinely
    # probed, deferred to reviewer -> INDETERMINATE.
    gh = _gh(["deploy/sudoers", "app/repositories/x.py"])
    verdicts = deployment_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.INDETERMINATE


def test_deployment_confirmed_when_migration_file_present():
    gh = _gh(["migrations/versions/20260805_add_col.py", "app/repositories/x.py"])
    verdicts = deployment_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    assert verdicts[0].verdict is Verdict.CONFIRMED


def test_deployment_silent_when_no_deployment_paths():
    gh = _gh(["app/services/legal.py"])
    verdicts = deployment_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader({}))
    assert verdicts == []


# -- regression_gate ----------------------------------------------------------


def test_regression_silent_when_no_security_files():
    gh = _gh(["app/services/legal.py"])
    files = {"app/services/legal.py": "# TODO fix\n    pass\n"}
    verdicts = regression_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert verdicts == []


def test_regression_rejected_when_security_except_body_is_pass():
    gh = _gh(["app/services/security/auth.py"])
    files = {
        "app/services/security/auth.py": (
            "def verify(token):\n"
            "    try:\n"
            "        decode(token)\n"
            "    except Exception:\n"
            "        pass\n"
        )
    }
    verdicts = regression_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert len(verdicts) == 1
    assert verdicts[0].verdict is Verdict.REJECTED
    assert "auth" in verdicts[0].item
    assert "auth.py" in verdicts[0].evidence[0].get("ref", "") + verdicts[0].evidence[0].get(
        "note", ""
    )


def test_regression_silent_when_security_except_has_handling():
    gh = _gh(["app/services/security/auth.py"])
    files = {
        "app/services/security/auth.py": (
            "def verify(token):\n"
            "    try:\n"
            "        decode(token)\n"
            "    except Exception:\n"
            "        raise AuthError('bad token')\n"
        )
    }
    verdicts = regression_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    # No empty/pass except -> gate cannot CONFIRM, stays silent.
    assert verdicts == []


def test_regression_silent_when_only_todo_in_security_path():
    # A bare TODO is not a definitive regression (could be pre-existing); be conservative.
    gh = _gh(["app/services/security/auth.py"])
    files = {"app/services/security/auth.py": "# TODO refactor this\n"}
    verdicts = regression_gate(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    assert verdicts == []


# -- run_mechanical_gates (orchestrator) --------------------------------------


def test_run_mechanical_gates_returns_flat_list():
    gh = _gh(["app/services/security/auth.py", "tests/test_auth.py"])
    files = {
        "app/services/security/auth.py": "def verify():\n    pass\n",
        "tests/test_auth.py": "def test_x():\n    with pytest.raises(E):\n        verify()\n",
    }
    verdicts = run_mechanical_gates(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    # At least one verdict per gate (5 gates), flat list.
    assert isinstance(verdicts, list)
    assert all(hasattr(v, "verdict") for v in verdicts)


def test_run_mechanical_gates_works_without_read_file():
    # When read_file is None the gates fall back to a git-show reader; with a
    # MagicMock gh the git show returns nothing readable -> gates must degrade
    # to INDETERMINATE rather than raise.
    gh = _gh(["app/services/security/auth.py"])
    verdicts = run_mechanical_gates(gh, AcceptanceSnapshot(), "b", "m", read_file=None)
    assert isinstance(verdicts, list)
    assert len(verdicts) > 0
    # No gate should raise; REJECTED only happens on content we can read.
    for v in verdicts:
        assert v.verdict in (Verdict.CONFIRMED, Verdict.REJECTED, Verdict.INDETERMINATE)


def test_run_mechanical_gates_rejects_when_legacy_pattern_present():
    gh = _gh(["app/services/security/keys.py"])
    files = {"app/services/security/keys.py": "_sync_ssh_keys_legacy()\n"}
    verdicts = run_mechanical_gates(gh, AcceptanceSnapshot(), "b", "m", read_file=_reader(files))
    # The legacy gate emits a REJECTED; at least one verdict is REJECTED.
    assert any(v.verdict is Verdict.REJECTED for v in verdicts)
