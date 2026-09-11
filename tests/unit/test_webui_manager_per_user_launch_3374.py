"""Unit tests for WebUIManager.supports_per_user_launch (Issue #3374)."""

import pytest

from app.services.webui_manager import WebUIManager

pytestmark = [pytest.mark.issue(3374)]


class _PwEntry:
    def __init__(self, pw_name):
        self.pw_name = pw_name


class _LaunchProbeManager(WebUIManager):
    """Bypass __init__ (config/env side effects); stub launch-path inputs."""

    def __init__(self, platform, webui_cmd, webui_dir):
        self._platform = platform
        self._webui_cmd = webui_cmd
        self._webui_dir = webui_dir
        self._resolved_webui = None  # probe memoization slot

    def _find_webui_executable(self):
        return self._webui_cmd, self._webui_dir


def _make(platform="linux", webui_cmd="/usr/local/bin/qwen-code-webui", webui_dir=None):
    return _LaunchProbeManager(platform, webui_cmd, webui_dir)


def _patch_identity(monkeypatch, current_user="open-ace", sudo="/usr/bin/sudo"):
    monkeypatch.setattr(
        "app.services.webui_manager.pwd.getpwuid",
        lambda uid: _PwEntry(current_user),
    )
    monkeypatch.setattr(
        "app.services.webui_manager.shutil.which",
        lambda name: sudo if name == "sudo" else None,
    )


def test_rejects_unsupported_platform():
    ok, reason = _make(platform="windows").supports_per_user_launch("alice_acct")
    assert ok is False and reason == "platform_unsupported"


def test_rejects_missing_executable(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make(webui_cmd=None).supports_per_user_launch("alice_acct")
    assert ok is False and reason == "webui_executable_missing"


def test_allows_when_already_target_user(monkeypatch):
    _patch_identity(monkeypatch, current_user="alice_acct")
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is True and reason is None


def test_rejects_dev_directory_mode_for_other_user(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make(webui_dir="/srv/qwen-code-webui").supports_per_user_launch("alice_acct")
    assert ok is False and reason == "dev_directory_mode_shared_account"


def test_rejects_when_sudo_missing(monkeypatch):
    _patch_identity(monkeypatch, sudo=None)
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is False and reason == "sudo_unavailable"


def test_allows_sudo_path_for_global_executable(monkeypatch):
    _patch_identity(monkeypatch)
    ok, reason = _make().supports_per_user_launch("alice_acct")
    assert ok is True and reason is None


def test_successful_resolution_is_memoized(monkeypatch):
    _patch_identity(monkeypatch)
    manager = _make()
    calls = []

    def _counting_find():
        calls.append(1)
        return "/usr/local/bin/qwen-code-webui", None

    manager._find_webui_executable = _counting_find
    assert manager.supports_per_user_launch("alice_acct")[0] is True
    assert manager.supports_per_user_launch("bob_acct")[0] is True
    assert len(calls) == 1


def test_failed_resolution_is_not_memoized(monkeypatch):
    _patch_identity(monkeypatch)
    manager = _make(webui_cmd=None)
    ok1, reason1 = manager.supports_per_user_launch("alice_acct")
    manager._webui_cmd = "/usr/local/bin/qwen-code-webui"
    ok2, reason2 = manager.supports_per_user_launch("alice_acct")
    assert ok1 is False and reason1 == "webui_executable_missing"
    assert ok2 is True and reason2 is None
