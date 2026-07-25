"""Issue #1828 finding #1 (DRY): ``resolve_codex_bearer_token`` is the single
source of truth for the Windows-UWP bearer-token decision.

Both launch paths (``remote-agent/agent.py`` WebSocket path and
``remote-agent/openace_cli.py`` SSH-CLI path) previously each inlined their own
``os.name == "nt"`` gate; this collapses them into one helper so a third site
cannot drift.
"""

from __future__ import annotations

from _helpers import PROXY_TOKEN, load_cli_settings


def test_returns_token_on_windows_when_non_empty(monkeypatch):
    cli_settings = load_cli_settings()
    monkeypatch.setattr(cli_settings.os, "name", "nt")
    assert cli_settings.resolve_codex_bearer_token(PROXY_TOKEN) == PROXY_TOKEN


def test_returns_none_on_windows_when_token_missing(monkeypatch):
    cli_settings = load_cli_settings()
    monkeypatch.setattr(cli_settings.os, "name", "nt")
    assert cli_settings.resolve_codex_bearer_token(None) is None
    assert cli_settings.resolve_codex_bearer_token("") is None


def test_returns_none_off_windows(monkeypatch):
    cli_settings = load_cli_settings()
    monkeypatch.setattr(cli_settings.os, "name", "posix")
    assert cli_settings.resolve_codex_bearer_token(PROXY_TOKEN) is None


def test_helper_is_pure_os_name_read(monkeypatch):
    """Patching ``os.name`` must be enough to flip the result; the helper must
    not depend on any other global (so the test host's real ``os.name`` never
    leaks into the assertion)."""
    cli_settings = load_cli_settings()
    monkeypatch.setattr(cli_settings.os, "name", "posix")
    assert cli_settings.resolve_codex_bearer_token(PROXY_TOKEN) is None
    monkeypatch.setattr(cli_settings.os, "name", "nt")
    assert cli_settings.resolve_codex_bearer_token(PROXY_TOKEN) == PROXY_TOKEN
