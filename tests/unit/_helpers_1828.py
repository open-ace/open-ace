"""Shared loaders and proxy-token fixtures for Issue #1828 tests.

Mirrors the ``importlib`` loading style of ``tests/issues/1776`` and
``tests/unit/test_cli_settings_apply.py``: each remote-agent module is loaded
under a unique name so it does not collide with another test's cached copy, and
its private state (e.g. ``_CODEX_CONFIG_LOCK``, the ``os`` module reference) is
isolated for ``monkeypatch``.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import importlib.util
import sys
from pathlib import Path
from typing import Any

REMOTE_AGENT_DIR = Path(__file__).resolve().parents[2] / "remote-agent"

PROXY_BASE_URL = "https://openace.example/api/remote/llm-proxy/v1"


def _load_isolated(module_name: str, relative_path: str) -> Any:
    """Load a remote-agent module without leaking imports into other tests.

    Two pollution vectors are rolled back after exec_module:
    - the temporary ``remote-agent`` sys.path entry would shadow bare
      ``import config`` for every later test in the same worker
      (tests/unit/test_db.py imports the scripts-side ``config``);
    - modules the file binds during load (e.g. its sibling ``config``) stay
      cached in sys.modules otherwise, so the same shadowing survives even
      after the path entry is removed.

    A previously cached bare ``config`` is popped first (mirrors the
    tests/issues/1776 loader) and restored afterwards.
    """
    agent_dir = str(REMOTE_AGENT_DIR)
    module_path = REMOTE_AGENT_DIR / relative_path
    saved_config = sys.modules.pop("config", None)
    path_added = agent_dir not in sys.path
    if path_added:
        sys.path.insert(0, agent_dir)
    known = set(sys.modules)
    try:
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        spec.loader.exec_module(module)
        return module
    finally:
        for name in set(sys.modules) - known:
            del sys.modules[name]
        if saved_config is not None:
            sys.modules["config"] = saved_config
        if path_added:
            sys.path.remove(agent_dir)


def load_cli_settings():
    """Load ``remote-agent/cli_settings.py`` as an isolated module."""
    return _load_isolated("cli_settings_1828", "cli_settings.py")


def load_openace_cli():
    """Load ``remote-agent/openace_cli.py`` as an isolated module."""
    return _load_isolated("openace_cli_1828", "openace_cli.py")


def load_agent_module():
    """Load ``remote-agent/agent.py`` as an isolated module.

    ``agent.py`` imports a local ``config`` module; the loader pops any stale
    cached copy so a foreign module from another test does not leak in
    (mirrors the ``tests/issues/1776`` loader) and restores it after the load.
    """
    return _load_isolated("remote_agent_1828", "agent.py")


def make_proxy_token(
    payload: bytes | str = b'{"user_id":"u1"}',
    secret: bytes = b"openace-test-secret",
) -> str:
    """Build a realistic Open ACE proxy token: ``{base64(payload)}.{hmac hex}``.

    Mirrors ``api_key_proxy.generate_proxy_token`` so the Issue #1828 #3 format
    guard accepts the token: a standard-base64 payload (which may legally
    contain ``+``/``/``/``=``) joined by a single ``.`` to an HMAC-SHA256 hex
    signature.
    """
    if isinstance(payload, str):
        payload = payload.encode()
    payload_b64 = base64.b64encode(payload).decode()
    signature = hmac.new(secret, payload_b64.encode(), hashlib.sha256).hexdigest()
    return f"{payload_b64}.{signature}"


# Realistic proxy tokens accepted by the #3 format guard.
PROXY_TOKEN = make_proxy_token()
SECOND_TOKEN = make_proxy_token(b'{"user_id":"u2"}')


def make_agent(agent_module: Any) -> Any:
    """Construct a bare ``RemoteAgent`` with the minimum attributes for unit tests."""
    agent = agent_module.RemoteAgent.__new__(agent_module.RemoteAgent)

    class _FakeConfig:
        machine_id = "machine-1828"
        server_url = "https://openace.example"

    agent.config = _FakeConfig()
    agent._terminal_processes = {}
    agent._terminal_tokens = {}
    agent._terminal_ports = {}
    return agent
