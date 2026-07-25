"""Issue #1828 finding #1 (DRY) integrated with #2/#3 at the agent layer.

V8a: both launch paths route the bearer-token decision through the shared
``resolve_codex_bearer_token`` helper — no inline ``os.name`` gate left to drift.
V8b: the agent's ``_apply_cli_settings`` path flows the resolved token through
``write_codex_settings`` (hitting the #3 guard and the #2 active-provider
routing) and lands the correct on-disk config.
"""

from __future__ import annotations

from _helpers import (
    PROXY_TOKEN,
    REMOTE_AGENT_DIR,
    load_agent_module,
    load_cli_settings,
    make_agent,
)


# ---------------------------------------------------------------------------
# V8a: source-level contract (robust against os.name monkeypatch hazards)
# ---------------------------------------------------------------------------


def test_agent_source_routes_through_resolve_helper():
    source = (REMOTE_AGENT_DIR / "agent.py").read_text(encoding="utf-8")
    assert "resolve_codex_bearer_token(" in source
    # The old inline token gate must be gone from the start_terminal path.
    assert 'os.name == "nt" and openai_token' not in source


def test_openace_cli_source_routes_through_resolve_helper():
    source = (REMOTE_AGENT_DIR / "openace_cli.py").read_text(encoding="utf-8")
    assert "resolve_codex_bearer_token(" in source


# ---------------------------------------------------------------------------
# V8b: behavioural — agent apply path -> write_codex_settings (guard + provider)
# ---------------------------------------------------------------------------


def _redirect_apply_to_tmp(agent_module, cli_settings, tmp_path, monkeypatch):
    """Force the agent's apply_cli_settings to write into tmp_path instead of
    the real ~/.codex, while still exercising the real writer (guard + provider
    routing + opt-out all run)."""

    def fake_apply(settings, proxy_base_url, home_dir=None, codex_bearer_token=None):
        cli_settings.apply_cli_settings(
            settings,
            proxy_base_url,
            home_dir=tmp_path,
            codex_bearer_token=codex_bearer_token,
        )

    monkeypatch.setattr(agent_module, "apply_cli_settings", fake_apply)


def test_agent_apply_writes_token_to_active_provider(tmp_path, monkeypatch):
    agent_module = load_agent_module()
    cli_settings = load_cli_settings()
    agent = make_agent(agent_module)
    _redirect_apply_to_tmp(agent_module, cli_settings, tmp_path, monkeypatch)

    agent._apply_cli_settings(
        {"codex": {"model_provider": "my-proxy"}},
        codex_bearer_token=PROXY_TOKEN,
    )

    parsed = cli_settings.tomllib.loads(
        (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    )
    mine = parsed["model_providers"]["my-proxy"]
    assert mine["experimental_bearer_token"] == PROXY_TOKEN
    assert mine["base_url"].endswith("/api/remote/llm-proxy/v1")


def test_agent_apply_degrades_non_proxy_token(tmp_path, monkeypatch):
    """A non-proxy token reaching the agent apply path is degraded by the #3
    guard — it must never land on disk as experimental_bearer_token."""
    agent_module = load_agent_module()
    cli_settings = load_cli_settings()
    agent = make_agent(agent_module)
    _redirect_apply_to_tmp(agent_module, cli_settings, tmp_path, monkeypatch)

    agent._apply_cli_settings({"codex": {}}, codex_bearer_token="sk-leaked")

    parsed = cli_settings.tomllib.loads(
        (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    )
    openace = parsed["model_providers"]["openace"]
    assert "experimental_bearer_token" not in openace
    assert openace["env_key"] == "OPENAI_API_KEY"
