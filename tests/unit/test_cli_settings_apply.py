#!/usr/bin/env python3
"""Unit tests for shared CLI settings writers."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_cli_settings():
    module_path = Path(__file__).resolve().parents[2] / "remote-agent" / "cli_settings.py"
    agent_dir = str(module_path.parent)
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    spec = importlib.util.spec_from_file_location("cli_settings", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_write_codex_settings_merges_existing_and_injects_proxy(tmp_path):
    cli_settings = load_cli_settings()
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        'personality = "pragmatic"\n\n[desktop]\nappearanceTheme = "system"\n',
        encoding="utf-8",
    )

    cli_settings.write_codex_settings(
        """model_provider = "openace"
model = "qwen3.7-max"

[model_providers.openace]
name = "Open ACE Proxy"
wire_api = "responses"
""",
        proxy_base_url="https://openace.example/api/remote/llm-proxy/v1",
        home_dir=tmp_path,
    )

    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["personality"] == "pragmatic"
    assert parsed["desktop"]["appearanceTheme"] == "system"
    assert parsed["model_provider"] == "openace"
    assert parsed["model"] == "qwen3.7-max"
    assert parsed["model_reasoning_summary"] == "auto"
    assert parsed["model_providers"]["openace"]["name"] == "Open ACE Proxy"
    assert parsed["model_providers"]["openace"]["wire_api"] == "responses"
    assert (
        parsed["model_providers"]["openace"]["base_url"]
        == "https://openace.example/api/remote/llm-proxy/v1"
    )
    assert parsed["model_providers"]["openace"]["env_key"] == "OPENAI_API_KEY"
