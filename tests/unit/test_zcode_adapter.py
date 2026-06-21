#!/usr/bin/env python3
"""Unit tests for the ZCode CLI adapter, settings writer, and usage parser."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

# --------------------------------------------------------------------------- #
# Module loading helpers (mirrors test_cli_settings_apply.py)
# --------------------------------------------------------------------------- #

_AGENT_DIR = str(Path(__file__).resolve().parents[2] / "remote-agent")


def _load_module(name: str, rel_path: str):
    module_path = Path(__file__).resolve().parents[2] / rel_path
    if _AGENT_DIR not in sys.path:
        sys.path.insert(0, _AGENT_DIR)
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli_adapters_pkg():
    # Import as a proper package (relative imports `.base`, `.zcode` require
    # the parent dir on sys.path with the package name intact).
    import importlib

    if _AGENT_DIR not in sys.path:
        sys.path.insert(0, _AGENT_DIR)
    return importlib.import_module("cli_adapters")


@pytest.fixture(scope="module")
def cli_settings_mod():
    return _load_module("cli_settings", "remote-agent/cli_settings.py")


@pytest.fixture(scope="module")
def usage_parser_mod():
    return _load_module("usage_parser", "remote-agent/cli_adapters/usage_parser.py")


# --------------------------------------------------------------------------- #
# Adapter registry & basic contract
# --------------------------------------------------------------------------- #


def test_zcode_registered_in_adapter_registry(cli_adapters_pkg):
    assert "zcode" in cli_adapters_pkg.ADAPTERS
    assert "zcode-code" in cli_adapters_pkg.ADAPTERS


def test_zcode_get_adapter_returns_zcode_instance(cli_adapters_pkg):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    assert adapter.get_display_name() == "ZCode"
    assert adapter.get_executable_name() == "zcode"


def test_zcode_supports_stdin_input_is_false(cli_adapters_pkg):
    """ZCode does NOT use Claude's stream-json stdin protocol."""
    adapter = cli_adapters_pkg.get_adapter("zcode")
    assert adapter.supports_stdin_input() is False


def test_zcode_provides_full_command(cli_adapters_pkg):
    """ZCode's build_start_args returns a self-contained command.

    This lets agent_runner use the args verbatim instead of shutil.which +
    [exe] + args[1:], which fails because the bundled engine isn't on PATH.
    The first element is the interpreter (``node`` on macOS where the bundled
    .cjs engine exists, or the resolved ``zcode`` binary on Linux); we only
    assert the contract, not the host-dependent prefix.
    """
    adapter = cli_adapters_pkg.get_adapter("zcode")
    assert adapter.provides_full_command() is True
    args = adapter.build_start_args("sess_x", "/tmp/proj", permission_mode="bypass")
    # Self-contained command is non-empty and includes app-server + cwd.
    assert len(args) >= 3
    assert "app-server" in args


def test_other_adapters_do_not_provide_full_command(cli_adapters_pkg):
    """Claude/Qwen/Codex/OpenClaw go through the shutil.which path (default)."""
    for tool in ("claude-code", "qwen-code-cli", "codex", "openclaw"):
        adapter = cli_adapters_pkg.get_adapter(tool)
        assert adapter.provides_full_command() is False, tool


def test_zcode_get_settings_path(cli_adapters_pkg):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    assert adapter.get_settings_path().endswith(".zcode/cli/config.json")


def test_zcode_get_env_vars_routes_through_anthropic_proxy(cli_adapters_pkg):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    env = adapter.get_env_vars("https://proxy.example/api/llm-proxy/", "tok")
    # Trailing slash stripped; ZCode's anthropic provider reads both env vars,
    # mirroring Claude: ANTHROPIC_API_KEY authenticates, ANTHROPIC_BASE_URL routes.
    assert env["ANTHROPIC_BASE_URL"] == "https://proxy.example/api/llm-proxy"
    assert env["ANTHROPIC_API_KEY"] == "tok"


def test_zcode_build_single_shot_args(cli_adapters_pkg):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    args = adapter.build_single_shot_args("do the task", "/tmp/proj", model="glm-5.2")
    # The leading token(s) resolve the engine (either `node <engine.cjs>` when the
    # bundled app is present, or a bare `zcode` binary). Don't assert the prefix
    # since it depends on the host (macOS bundle vs Linux CI). The engine is
    # always followed by the prompt/mode/json flags below.
    assert "--prompt" in args
    assert "do the task" in args
    assert "--mode" in args and "yolo" in args
    assert "--json" in args
    assert "--no-color" in args
    # No --model flag: zcode has no headless model flag; model comes from config.
    assert "--model" not in args


def test_zcode_build_start_args_uses_app_server(cli_adapters_pkg):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    args = adapter.build_start_args("sess_x", "/tmp/proj", permission_mode="bypass")
    assert "app-server" in args
    assert "yolo" in args  # bypass -> yolo (fully autonomous)


def test_zcode_build_resume_args(cli_adapters_pkg):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    args = adapter.build_resume_args("sess_resume", "/tmp/proj", "continue now")
    assert "--resume" in args
    assert "sess_resume" in args
    assert "--prompt" in args and "continue now" in args


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("bypass", "yolo"),
        ("full-auto", "yolo"),
        ("auto", "build"),
        ("auto-edit", "edit"),
        ("plan", "plan"),
        (None, "yolo"),
    ],
)
def test_zcode_permission_mode_mapping(cli_adapters_pkg, mode, expected):
    adapter = cli_adapters_pkg.get_adapter("zcode")
    assert adapter._map_permission_mode(mode) == expected


# --------------------------------------------------------------------------- #
# Settings writer
# --------------------------------------------------------------------------- #


def test_write_zcode_settings_creates_config(cli_settings_mod, tmp_path):
    path = cli_settings_mod.write_zcode_settings(
        {"api_key": "key-123"},
        proxy_base_url="https://proxy.example/api/llm-proxy",
        home_dir=tmp_path,
    )
    cfg = json.loads(path.read_text(encoding="utf-8"))
    assert cfg["provider"]["zai"]["kind"] == "anthropic"
    assert cfg["provider"]["zai"]["options"]["baseURL"] == "https://proxy.example/api/llm-proxy"
    assert cfg["provider"]["zai"]["options"]["apiKey"] == "key-123"
    assert cfg["model"]["main"] == "zai/glm-5.2"
    assert cfg["model"]["lite"] == "zai/glm-4.5-air"


def test_write_zcode_settings_merges_existing(cli_settings_mod, tmp_path):
    config_path = tmp_path / ".zcode" / "cli" / "config.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        json.dumps(
            {
                "provider": {
                    "openai": {"id": "openai", "kind": "openai", "options": {}},
                    "zai": {"id": "zai", "kind": "anthropic", "options": {"apiKey": "old"}},
                }
            }
        ),
        encoding="utf-8",
    )
    cli_settings_mod.write_zcode_settings(
        {"api_key": "new-key"},
        proxy_base_url="https://px/v1",
        home_dir=tmp_path,
    )
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    # Existing non-zcode provider preserved.
    assert "openai" in cfg["provider"]
    # zai merged: baseURL injected, apiKey updated.
    assert cfg["provider"]["zai"]["options"]["baseURL"] == "https://px/v1"
    assert cfg["provider"]["zai"]["options"]["apiKey"] == "new-key"


def test_apply_cli_settings_dispatches_zcode(cli_settings_mod, tmp_path):
    cli_settings_mod.apply_cli_settings(
        {"zcode": {"api_key": "dispatch-key"}},
        proxy_base_url="https://px/v1",
        home_dir=tmp_path,
    )
    config_path = tmp_path / ".zcode" / "cli" / "config.json"
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    assert cfg["provider"]["zai"]["options"]["apiKey"] == "dispatch-key"


# --------------------------------------------------------------------------- #
# Usage parser
# --------------------------------------------------------------------------- #


def test_usage_parser_zcode_single_shot_shape(usage_parser_mod):
    # Shape from `zcode --prompt ... --json` (verified against running CLI).
    parsed = {
        "usage": {
            "inputTokens": 8358,
            "outputTokens": 4,
            "cacheReadTokens": 7040,
            "reasoningTokens": 0,
            "modelRequestCount": 1,
        }
    }
    r = usage_parser_mod.extract_stream_usage("zcode", parsed)
    assert r == {
        "input": 8358,
        "output": 4,
        "cache_read": 7040,
        "reasoning": 0,
        "model_requests": 1,
    }


def test_usage_parser_zcode_session_usage_shape(usage_parser_mod):
    # Shape from app-server session/usage (top-level camelCase, no wrapper).
    parsed = {
        "totalTokens": 8362,
        "inputTokens": 8358,
        "outputTokens": 4,
        "cacheReadTokens": 0,
        "modelRequestCount": 1,
    }
    r = usage_parser_mod.extract_stream_usage("zcode", parsed)
    assert r["input"] == 8358
    assert r["output"] == 4
    assert r["cache_read"] == 0
    assert r["model_requests"] == 1


def test_usage_parser_no_regression_for_claude(usage_parser_mod):
    parsed = {"type": "result", "usage": {"input_tokens": 100, "output_tokens": 50}}
    assert usage_parser_mod.extract_stream_usage("claude-code", parsed) == {
        "input": 100,
        "output": 50,
    }


def test_usage_parser_no_regression_for_qwen(usage_parser_mod):
    parsed = {"usage": {"input_tokens": 10, "output_tokens": 5, "cachedContentTokenCount": 2}}
    assert usage_parser_mod.extract_stream_usage("qwen-code-cli", parsed) == {
        "input": 8,
        "output": 5,
    }


# --------------------------------------------------------------------------- #
# ZCodeAppServerSession — worker/resume/guard (mocked)
# --------------------------------------------------------------------------- #


@pytest.fixture(scope="module")
def zcode_session_cls():
    """Load the ZCodeAppServerSession class without spawning a real process."""
    mod = _load_module("zcode_app_server", "remote-agent/zcode_app_server.py")
    return mod.ZCodeAppServerSession


class _FakeProcess:
    """Minimal stand-in for subprocess.Popen."""

    def __init__(self):
        self.returncode = None
        self.pid = 12345
        self.stdin = None
        self.stdout = None
        self.stderr = None

    def wait(self, timeout=None):  # noqa: D401
        return 0

    def terminate(self):
        self.returncode = 0

    def kill(self):
        self.returncode = 0


def _make_session(zcode_session_cls, requests):
    """Build a session whose ``_request`` is replaced by *requests* lookup."""
    import threading

    sess = zcode_session_cls.__new__(zcode_session_cls)
    sess.session_id = "sess_test"
    sess.process = _FakeProcess()
    sess.project_path = "/tmp"
    sess.cli_tool = "zcode"
    sess.output_callback = lambda *a, **k: None
    sess.usage_callback = None
    sess.permission_callback = None
    sess.model = None
    sess.permission_mode = None
    sess.env = None
    sess.allowed_tools = []
    sess._paused = False
    sess._restart_lock = threading.Lock()
    sess._cli_session_id = None
    sess._created = threading.Event()
    sess._stopped = threading.Event()
    sess._lock = threading.Lock()
    sess._pending = {}
    sess._last_event_seq = 0
    sess._turn_done = threading.Event()
    sess._turn_done.set()
    sess._worker = None
    sess.last_send_error = None
    sess._reader_thread = threading.Thread(target=lambda: None, daemon=True)

    def fake_request(method, params, timeout=30.0):
        return requests.get(method, {}).get("result")

    sess._request = fake_request
    return sess


def test_start_resumes_when_session_sessionid_present(zcode_session_cls):
    """session/resume returns session.sessionId -> resume succeeds, no create."""
    requests = {
        "session/resume": {"result": {"session": {"sessionId": "sess_resumed_xyz"}}},
    }
    sess = _make_session(zcode_session_cls, requests)
    ok = sess.start(resume_session_id="sess_resumed_xyz")
    assert ok is True
    assert sess._cli_session_id == "sess_resumed_xyz"


def test_runtime_model_api_key_uses_protocol_credential_shape(
    zcode_session_cls, tmp_path, monkeypatch
):
    """runtimeModel.provider.apiKey must be an object accepted by ZCode Protocol.

    ZCode 0.14.5 rejects a bare string with:
    runtimeModel.provider.apiKey expected object, received string. That makes
    session/resume fail and the runner fall back to session/create, losing the
    workflow's intended main/review/test resume context.
    """
    config_path = tmp_path / ".zcode" / "cli" / "config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps({"provider": {"zai": {"options": {"apiKey": "key-123"}}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(tmp_path))

    sess = _make_session(zcode_session_cls, {})
    sess.model = "glm-5.2"

    runtime_model = sess._build_runtime_model()

    assert runtime_model["provider"]["apiKey"] == {
        "source": "inline",
        "value": "key-123",
    }


def test_start_falls_back_to_create_when_resume_lacks_session(zcode_session_cls):
    """If resume response lacks session.sessionId, fall back to session/create."""
    requests = {
        # Resume returns no session envelope (simulates shape mismatch).
        "session/resume": {"result": {"messages": [], "projection": {}}},
        "session/create": {"result": {"session": {"sessionId": "sess_fresh"}}},
    }
    sess = _make_session(zcode_session_cls, requests)
    ok = sess.start(resume_session_id="sess_resumed_xyz")
    assert ok is True
    # Fresh id from create, not the resume id.
    assert sess._cli_session_id == "sess_fresh"


def test_start_creates_when_no_resume_id(zcode_session_cls):
    requests = {"session/create": {"result": {"session": {"sessionId": "sess_new"}}}}
    sess = _make_session(zcode_session_cls, requests)
    ok = sess.start()
    assert ok is True
    assert sess._cli_session_id == "sess_new"


def test_send_message_returns_immediately_and_runs_worker(zcode_session_cls):
    """send_message returns True without blocking; the worker runs async."""
    import time

    # session/send accepted; events immediately report turn.completed.
    requests = {
        "session/send": {"result": {"accepted": True, "sessionId": "sess_x"}},
        "session/events": {
            "result": {
                "events": [
                    {"seq": 1, "type": "turn.completed", "payload": {"usage": {}}},
                ]
            }
        },
        "session/usage": {"result": {"inputTokens": 0, "outputTokens": 0}},
    }
    sess = _make_session(zcode_session_cls, requests)
    sess._cli_session_id = "sess_x"
    t0 = time.monotonic()
    ok = sess.send_message("hello")
    elapsed = time.monotonic() - t0
    assert ok is True
    assert elapsed < 1.0  # non-blocking: returns well under a second
    # Worker completes the turn.
    assert sess.wait_turn(timeout=5.0) is True


def test_send_message_rejects_concurrent_send_as_busy(zcode_session_cls):
    """A second send while a turn is in progress is rejected with a busy error."""
    import threading

    gate = threading.Event()

    def slow_request(method, params, timeout=30.0):
        if method == "session/send":
            gate.wait(timeout=5.0)  # hold the turn open
            return {"accepted": True, "sessionId": "sess_x"}
        if method == "session/events":
            gate.set()
            return {"events": [{"seq": 1, "type": "turn.completed", "payload": {}}]}
        return None

    sess = _make_session(zcode_session_cls, {})
    sess._cli_session_id = "sess_x"
    sess._request = slow_request
    assert sess.send_message("first") is True
    # Second send while the first turn is still running.
    second = sess.send_message("second")
    assert second is False
    assert sess.last_send_error is not None
    assert "in progress" in sess.last_send_error
    gate.set()
    assert sess.wait_turn(timeout=5.0)
