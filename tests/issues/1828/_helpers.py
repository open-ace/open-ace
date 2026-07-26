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

REMOTE_AGENT_DIR = Path(__file__).resolve().parents[3] / "remote-agent"

PROXY_BASE_URL = "https://openace.example/api/remote/llm-proxy/v1"


def _ensure_remote_agent_on_path() -> None:
    agent_dir = str(REMOTE_AGENT_DIR)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)


def load_cli_settings():
    """Load ``remote-agent/cli_settings.py`` as an isolated module."""
    _ensure_remote_agent_on_path()
    module_path = REMOTE_AGENT_DIR / "cli_settings.py"
    spec = importlib.util.spec_from_file_location("cli_settings_1828", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_openace_cli():
    """Load ``remote-agent/openace_cli.py`` as an isolated module."""
    _ensure_remote_agent_on_path()
    module_path = REMOTE_AGENT_DIR / "openace_cli.py"
    spec = importlib.util.spec_from_file_location("openace_cli_1828", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def load_agent_module():
    """Load ``remote-agent/agent.py`` as an isolated module.

    ``agent.py`` imports a local ``config`` module; pop any stale cached copy so
    a foreign module from another test does not leak in (mirrors the
    ``tests/issues/1776`` loader).
    """
    _ensure_remote_agent_on_path()
    module_path = REMOTE_AGENT_DIR / "agent.py"
    sys.modules.pop("config", None)
    spec = importlib.util.spec_from_file_location("remote_agent_1828", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


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
