#!/usr/bin/env python3
"""Regression tests for the Codex bearer-token cleanup (review on PR #1776).

These cover four confirmed findings all rooted in the bearer token being
persisted to ``~/.codex/config.toml`` and never removed:

1. ``cmd_menu`` (default entry point) used ``os.execvpe`` which replaces the
   process image, so no ``finally`` / ``atexit`` cleanup ever ran.
2. ``_cmd_stop_terminal`` / ``_cmd_attach_terminal`` re-applied tokens but
   never removed them, leaving stale tokens on disk.
3. The bearer token was persisted in plaintext with no eviction path.
4. ``chmod(0o600)`` is a no-op on Windows, so the POSIX-only assertion gave a
   false sense of protection.
"""

from __future__ import annotations

import importlib.util
import stat
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Module loading helpers (mirrors tests/unit/test_cli_settings_apply.py style)
# ---------------------------------------------------------------------------


def load_cli_settings():
    module_path = Path(__file__).resolve().parents[3] / "remote-agent" / "cli_settings.py"
    agent_dir = str(module_path.parent)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    spec = importlib.util.spec_from_file_location("cli_settings_1776", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_openace_cli():
    module_path = Path(__file__).resolve().parents[3] / "remote-agent" / "openace_cli.py"
    agent_dir = str(module_path.parent)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    spec = importlib.util.spec_from_file_location("openace_cli_1776", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _seed_codex_token(cli_settings, tmp_path: Path, token: str) -> Path:
    """Write a config.toml carrying an experimental_bearer_token."""
    config_path = tmp_path / ".codex" / "config.toml"
    cli_settings.write_codex_settings(
        {},
        proxy_base_url="https://openace.example/api/remote/llm-proxy/v1",
        home_dir=tmp_path,
        bearer_token=token,
    )
    assert config_path.exists()
    return config_path


# ---------------------------------------------------------------------------
# Finding: clear_codex_bearer_token must remove a persisted bearer token
# ---------------------------------------------------------------------------


def test_clear_codex_bearer_token_removes_persisted_token(tmp_path):
    cli_settings = load_cli_settings()
    config_path = _seed_codex_token(cli_settings, tmp_path, "proxy-token-123")

    cli_settings.clear_codex_bearer_token(home_dir=tmp_path)

    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    openace = parsed["model_providers"]["openace"]
    assert (
        "experimental_bearer_token" not in openace
    ), "clear_codex_bearer_token must scrub the persisted bearer token"
    # Non-sensitive routing must be preserved.
    assert openace["base_url"] == "https://openace.example/api/remote/llm-proxy/v1"


def test_clear_codex_bearer_token_is_idempotent_when_absent(tmp_path):
    cli_settings = load_cli_settings()
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        '[model_providers.openace]\nenv_key = "OPENAI_API_KEY"\n',
        encoding="utf-8",
    )

    # Should not raise even though there is no bearer token to remove.
    cli_settings.clear_codex_bearer_token(home_dir=tmp_path)

    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "experimental_bearer_token" not in parsed["model_providers"]["openace"]


def test_clear_codex_bearer_token_handles_missing_config(tmp_path):
    cli_settings = load_cli_settings()
    # No config.toml exists at all.
    cli_settings.clear_codex_bearer_token(home_dir=tmp_path)
    assert not (tmp_path / ".codex" / "config.toml").exists()


# ---------------------------------------------------------------------------
# Finding: write_codex_settings chmod must be POSIX-only / no false guarantee
# ---------------------------------------------------------------------------


def test_write_codex_settings_skips_chmod_on_windows(tmp_path):
    """On Windows chmod is a no-op, so the writer must gate it on POSIX only.

    Monkeypatching ``os.name`` globally also rewrites ``os.path`` internals
    and breaks ``os.replace``/``Path`` instantiation on this POSIX host, so we
    instead prove the contract two ways that do not perturb ``os.name``:
    (1) the source gates the chmod on ``os.name == "posix"``, and
    (2) on this POSIX host the chmod actually fires when a token is written.
    """
    cli_settings = load_cli_settings()

    # (1) The chmod call site must be guarded by a POSIX check, not run on all
    # platforms (chmod is a no-op on Windows and would give a false guarantee).
    module_path = Path(__file__).resolve().parents[3] / "remote-agent" / "cli_settings.py"
    source = module_path.read_text(encoding="utf-8")
    assert (
        'os.name == "posix"' in source
    ), "write_codex_settings must gate the bearer-token chmod on os.name == 'posix'"

    # (2) On POSIX the file is chmod'd 0600 when a token is written.
    config_path = cli_settings.write_codex_settings(
        {},
        proxy_base_url="https://openace.example/api/remote/llm-proxy/v1",
        home_dir=tmp_path,
        bearer_token="proxy-token-123",
    )
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


def test_write_codex_settings_chmods_on_posix(tmp_path, monkeypatch):
    cli_settings = load_cli_settings()
    monkeypatch.setattr(cli_settings.os, "name", "posix")

    config_path = cli_settings.write_codex_settings(
        {},
        proxy_base_url="https://openace.example/api/remote/llm-proxy/v1",
        home_dir=tmp_path,
        bearer_token="proxy-token-123",
    )

    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600


# ---------------------------------------------------------------------------
# Finding: cmd_menu must clean up the bearer token (no exec-without-cleanup)
# ---------------------------------------------------------------------------


def _fake_terminal() -> dict:
    return {
        "session_id": "term-123",
        "proxy_url": "https://openace.example/api/remote/llm-proxy",
        "cli_settings": {"codex-cli": {"model_provider": "openace"}},
        "tokens": {"openai": "proxy-token-123"},
        "source": "ssh_cli",
    }


def test_cmd_menu_clears_bearer_token_after_menu_exits(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()

    started = []
    cleared_tokens = []

    monkeypatch.setattr(openace_cli, "_start_cli_terminal", lambda work_dir: _fake_terminal())
    monkeypatch.setattr(
        openace_cli, "_apply_local_cli_settings", lambda terminal: started.append(terminal)
    )
    monkeypatch.setattr(openace_cli, "_write_active_terminal", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_session_env", lambda terminal: {})
    monkeypatch.setattr(openace_cli, "_clear_active_terminal", lambda: None)
    monkeypatch.setattr(
        openace_cli,
        "clear_codex_bearer_token",
        lambda home_dir=None: cleared_tokens.append(home_dir),
    )

    # The menu is launched as a subprocess we can wait on; simulate it returning.
    class FakeProc:
        returncode = 0

        def wait(self):
            return 0

    captured = {}

    def fake_run(args, env=None, cwd=None, check=False):
        captured["called"] = True
        captured["args"] = list(args)
        return FakeProc()

    monkeypatch.setattr(openace_cli.subprocess, "run", fake_run)

    rc = openace_cli.cmd_menu(_ns(work_dir=str(tmp_path)))

    assert rc == 0
    assert captured.get("called") is True, "menu should still launch"
    assert captured["args"][0] == sys.executable
    assert cleared_tokens == [
        None
    ], "cmd_menu must clear the persisted bearer token after the menu exits"


def test_cmd_menu_clears_token_even_when_menu_fails(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()

    cleared_tokens = []
    monkeypatch.setattr(openace_cli, "_start_cli_terminal", lambda work_dir: _fake_terminal())
    monkeypatch.setattr(openace_cli, "_apply_local_cli_settings", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_write_active_terminal", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_session_env", lambda terminal: {})
    monkeypatch.setattr(openace_cli, "_clear_active_terminal", lambda: None)
    monkeypatch.setattr(
        openace_cli,
        "clear_codex_bearer_token",
        lambda home_dir=None: cleared_tokens.append(home_dir),
    )

    def fake_run(args, env=None, cwd=None, check=False):
        raise RuntimeError("menu crashed")

    monkeypatch.setattr(openace_cli.subprocess, "run", fake_run)

    # The cleanup must run regardless of menu failure.
    with pytest.raises(RuntimeError):
        openace_cli.cmd_menu(_ns(work_dir=str(tmp_path)))

    assert cleared_tokens == [
        None
    ], "bearer-token cleanup must run in the finally path even if the menu crashes"


def test_cmd_shell_clears_bearer_token_in_finally(monkeypatch, tmp_path):
    openace_cli = load_openace_cli()

    cleared_tokens = []
    monkeypatch.setattr(openace_cli, "_start_cli_terminal", lambda work_dir: _fake_terminal())
    monkeypatch.setattr(openace_cli, "_apply_local_cli_settings", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_write_active_terminal", lambda terminal: None)
    monkeypatch.setattr(openace_cli, "_session_env", lambda terminal: {})
    monkeypatch.setattr(openace_cli, "_clear_active_terminal", lambda: None)
    monkeypatch.setattr(
        openace_cli,
        "clear_codex_bearer_token",
        lambda home_dir=None: cleared_tokens.append(home_dir),
    )

    ran = {"shell": False}

    def fake_run(args, env=None, cwd=None, check=False):
        ran["shell"] = True
        return None

    monkeypatch.setattr(openace_cli.subprocess, "run", fake_run)
    monkeypatch.setattr(openace_cli, "_windows_shell_args", lambda: ["cmd.exe"])

    rc = openace_cli.cmd_shell(_ns(work_dir=str(tmp_path)))

    assert rc == 0
    assert ran["shell"] is True
    assert cleared_tokens == [None], "cmd_shell must clear the persisted bearer token"


# ---------------------------------------------------------------------------
# Finding: _cmd_stop_terminal must revoke the persisted bearer token
# ---------------------------------------------------------------------------


def load_agent_module():
    module_path = Path(__file__).resolve().parents[3] / "remote-agent" / "agent.py"
    agent_dir = str(module_path.parent)
    if agent_dir in sys.path:
        sys.path.remove(agent_dir)
    sys.path.insert(0, agent_dir)
    sys.modules.pop("config", None)
    spec = importlib.util.spec_from_file_location("remote_agent_1776", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def _bare_agent(agent_module):
    agent = agent_module.RemoteAgent.__new__(agent_module.RemoteAgent)

    class _FakeConfig:
        machine_id = "machine-123"

    agent.config = _FakeConfig()
    # _cmd_stop_terminal's finally now checks whether any other terminal is
    # still active before scrubbing the shared token, so the agent must carry
    # the active-terminal map. Default to empty (no other terminals) so the
    # single-stop path still clears the token.
    agent._terminal_processes = {}
    return agent


def test_cmd_stop_terminal_clears_persisted_bearer_token(monkeypatch):
    """Stopping a terminal must scrub the on-disk Codex bearer token."""
    agent_module = load_agent_module()
    agent = _bare_agent(agent_module)

    cleared = []
    monkeypatch.setattr(agent, "_stop_terminal_process", lambda terminal_id: None)

    sent = []
    monkeypatch.setattr(agent, "_http_send", lambda payload: sent.append(payload))
    monkeypatch.setattr(
        agent_module, "clear_codex_bearer_token", lambda home_dir=None: cleared.append(home_dir)
    )

    agent._cmd_stop_terminal({"terminal_id": "term-123"})

    assert cleared == [None], "_cmd_stop_terminal must clear the persisted bearer token on stop"
    assert any(msg.get("status") == "stopped" for msg in sent)


def test_cmd_stop_terminal_clears_token_even_when_process_stop_fails(monkeypatch):
    agent_module = load_agent_module()
    agent = _bare_agent(agent_module)

    cleared = []
    monkeypatch.setattr(
        agent,
        "_stop_terminal_process",
        lambda terminal_id: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(agent, "_http_send", lambda payload: None)
    monkeypatch.setattr(
        agent_module, "clear_codex_bearer_token", lambda home_dir=None: cleared.append(home_dir)
    )

    with pytest.raises(RuntimeError):
        agent._cmd_stop_terminal({"terminal_id": "term-123"})

    assert cleared == [
        None
    ], "bearer-token cleanup must run in the finally path of _cmd_stop_terminal"


# ---------------------------------------------------------------------------
# Severe#1 (round-2 review): multi-terminal concurrency
# Stopping one terminal must NOT scrub the shared ~/.codex/config.toml token
# while other terminals are still running (they may depend on it via the
# Windows-UWP file-only path). Cleanup is only safe when no active terminal
# remains. _stop_terminal_process pops the current id, so the finally branch
# can simply check ``not self._terminal_processes``.
# ---------------------------------------------------------------------------


def test_cmd_stop_terminal_skips_clear_when_other_terminals_active(monkeypatch):
    """Stopping terminal A while terminal B still runs must keep the token."""
    agent_module = load_agent_module()
    agent = _bare_agent(agent_module)
    # Two terminals are active; _stop_terminal_process (real pop) removes the
    # one being stopped, leaving the other in the map.
    agent._terminal_processes = {"term-a": object(), "term-b": object()}

    cleared = []

    def fake_stop(terminal_id):
        agent._terminal_processes.pop(terminal_id, None)

    monkeypatch.setattr(agent, "_stop_terminal_process", fake_stop)
    monkeypatch.setattr(agent, "_http_send", lambda payload: None)
    monkeypatch.setattr(
        agent_module, "clear_codex_bearer_token", lambda home_dir=None: cleared.append(home_dir)
    )

    agent._cmd_stop_terminal({"terminal_id": "term-a"})

    assert (
        cleared == []
    ), "must NOT clear the shared bearer token while another terminal is still active"
    assert "term-b" in agent._terminal_processes, "the other terminal must remain tracked"


def test_cmd_stop_terminal_clears_token_when_last_terminal_stops(monkeypatch):
    """Stopping the only/last terminal must scrub the token (regression guard)."""
    agent_module = load_agent_module()
    agent = _bare_agent(agent_module)
    agent._terminal_processes = {"term-solo": object()}

    cleared = []

    def fake_stop(terminal_id):
        agent._terminal_processes.pop(terminal_id, None)

    monkeypatch.setattr(agent, "_stop_terminal_process", fake_stop)
    monkeypatch.setattr(agent, "_http_send", lambda payload: None)
    monkeypatch.setattr(
        agent_module, "clear_codex_bearer_token", lambda home_dir=None: cleared.append(home_dir)
    )

    agent._cmd_stop_terminal({"terminal_id": "term-solo"})

    assert cleared == [None], "must clear the shared bearer token once no active terminal remains"
    assert agent._terminal_processes == {}


# ---------------------------------------------------------------------------
# Severe#2 (round-2 review): clear_codex_bearer_token / write_codex_settings
# read-modify-write races. A process-wide threading.Lock must serialize all
# access to ~/.codex/config.toml so a concurrent writer cannot lose fields.
# ---------------------------------------------------------------------------


def test_codex_config_writes_are_serialized(tmp_path, monkeypatch):
    """Concurrent write_codex_settings + clear_codex_bearer_token must not lose
    fields. Without a process lock, a slow reader's stale snapshot can clobber a
    concurrent writer's newly-added field (lost update).

    This is made deterministic by intercepting ``_load_toml_file`` inside
    ``clear_codex_bearer_token``: the clearer reads a stale snapshot, then yields
    so the writer can persist a fresh config, then the clearer writes its stale
    snapshot back - clobbering the writer's change. A process-wide lock must
    make the whole read-modify-write atomic so no field is lost.
    """
    cli_settings = load_cli_settings()

    config_path = _seed_codex_token(cli_settings, tmp_path, "proxy-token-123")
    assert config_path.exists()

    import threading

    real_load = cli_settings._load_toml_file
    clearer_ready = threading.Event()
    clearer_read = threading.Event()
    writer_done = threading.Event()
    in_clearer = threading.local()

    def racing_load(filepath):
        # Only inject the stall for reads issued by the clearer thread.
        if getattr(in_clearer, "value", False) and not clearer_read.is_set():
            clearer_read.set()
            # Let the writer land its fresh config while we hold a stale snapshot.
            writer_done.wait(timeout=5)
        return real_load(filepath)

    monkeypatch.setattr(cli_settings, "_load_toml_file", racing_load)

    def clearer():
        in_clearer.value = True
        clearer_ready.set()
        cli_settings.clear_codex_bearer_token(home_dir=tmp_path)

    def writer():
        # Wait until the clearer has captured its stale snapshot, then write a
        # fresh config carrying a brand-new field the clearer never saw.
        clearer_read.wait(timeout=5)
        cli_settings.write_codex_settings(
            {"model_provider": "openace"},
            proxy_base_url="https://openace.example/api/remote/llm-proxy/v1",
            home_dir=tmp_path,
            bearer_token="writer-token-999",
        )
        writer_done.set()

    t_clear = threading.Thread(target=clearer)
    t_write = threading.Thread(target=writer)
    t_clear.start()
    t_write.start()
    t_clear.join(timeout=10)
    t_write.join(timeout=10)

    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    openace = parsed["model_providers"]["openace"]
    # The writer's bearer token must survive the clearer's stale rewrite.
    assert openace.get("experimental_bearer_token") == "writer-token-999", (
        "writer's field was lost to a concurrent read-modify-write race "
        "(missing serialization lock on ~/.codex/config.toml)"
    )


def _ns(work_dir: str | None = None):
    import argparse

    return argparse.Namespace(work_dir=work_dir)
