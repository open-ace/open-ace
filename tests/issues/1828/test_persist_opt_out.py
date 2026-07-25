"""Issue #1828 finding #4 (improvement): a per-host opt-out that disables
persisting the Codex bearer token to disk.

The opt-out lives in the Open ACE-private sidecar ``~/.codex/openace_state.json``
(``persist_bearer_token: false``), deliberately NOT in ``config.toml``, so it
never round-trips through Codex's strict TOML schema and risks breaking Codex
startup. Open ACE only ever reads this file. When the opt-out is in effect, a
valid proxy token is still degraded to ``env_key`` auth (Windows-UWP Codex then
cannot authenticate directly — the cost is warned about).
"""

from __future__ import annotations

import logging

import pytest

from _helpers import PROXY_BASE_URL, PROXY_TOKEN, load_cli_settings


@pytest.fixture(scope="module")
def cli_settings():
    return load_cli_settings()


def _write_sidecar(tmp_path, body: str) -> None:
    sidecar = tmp_path / ".codex" / "openace_state.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(body, encoding="utf-8")


def _openace(cli_settings, tmp_path) -> dict:
    config_path = tmp_path / ".codex" / "config.toml"
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    return parsed["model_providers"]["openace"]


def test_opt_out_suppresses_bearer_token(cli_settings, tmp_path, caplog):
    _write_sidecar(tmp_path, '{"persist_bearer_token": false}')

    with caplog.at_level(logging.WARNING):
        cli_settings.write_codex_settings(
            {},
            proxy_base_url=PROXY_BASE_URL,
            home_dir=tmp_path,
            bearer_token=PROXY_TOKEN,
        )

    openace = _openace(cli_settings, tmp_path)
    assert "experimental_bearer_token" not in openace
    assert openace["env_key"] == "OPENAI_API_KEY"
    assert any("UWP" in rec.getMessage() for rec in caplog.records)


def test_opt_out_defaults_true_when_sidecar_missing(cli_settings, tmp_path):
    cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    assert _openace(cli_settings, tmp_path)["experimental_bearer_token"] == PROXY_TOKEN


def test_opt_out_defaults_true_when_sidecar_corrupt(cli_settings, tmp_path):
    _write_sidecar(tmp_path, "not-json{")
    cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    assert _openace(cli_settings, tmp_path)["experimental_bearer_token"] == PROXY_TOKEN


def test_opt_out_defaults_true_when_key_missing(cli_settings, tmp_path):
    _write_sidecar(tmp_path, '{"unrelated": true}')
    cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    assert _openace(cli_settings, tmp_path)["experimental_bearer_token"] == PROXY_TOKEN


def test_opt_out_does_not_leak_into_config_toml(cli_settings, tmp_path):
    """The sidecar must keep Codex's config.toml schema zero-touch: no Open ACE
    private section or key may appear in config.toml."""
    _write_sidecar(tmp_path, '{"persist_bearer_token": false}')
    cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    text = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert "openace_state" not in text
    assert "persist_bearer_token" not in text
