"""Issue #1828 finding #3 (hardening): the write path must refuse to persist a
non-proxy-format token and degrade to ``env_key`` auth instead.

Proxy tokens from ``generate_proxy_token`` have the shape
``{standard-base64}.{hmac_sha256_hex}`` — a single ``.`` separator, hex on the
right, and a left segment that may legitimately contain ``+``/``/``/``=`` (so the
guard must NOT constrain the left character set). A raw ``sk-`` key or any other
shape is rejected, with an escape valve (``OPENACE_ALLOW_ANY_CODEX_TOKEN=1``) for
token-format rollout windows.
"""

from __future__ import annotations

import logging

import pytest
from _helpers import PROXY_BASE_URL, PROXY_TOKEN, load_cli_settings, make_proxy_token


@pytest.fixture(scope="module")
def cli_settings():
    return load_cli_settings()


# ---------------------------------------------------------------------------
# _is_proxy_token_format: pure validator
# ---------------------------------------------------------------------------


def test_accepts_standard_base64_left_segment_with_special_chars(cli_settings):
    """b'\\xfb\\xff' base64-encodes to '//8=' — contains '/' and '='.

    The guard must NOT mis-reject a real standard-base64 payload (only base64url
    omits these chars)."""
    token = make_proxy_token(b"\xfb\xff")
    left = token.split(".")[0]
    assert "/" in left or "+" in left or "=" in left
    assert cli_settings._is_proxy_token_format(token) is True


def test_accepts_realistic_proxy_token(cli_settings):
    assert cli_settings._is_proxy_token_format(PROXY_TOKEN) is True


def test_rejects_raw_sk_key(cli_settings):
    assert cli_settings._is_proxy_token_format("sk-proj-xxxxxxxxxxxxxxxxxx") is False


def test_requires_exactly_one_dot(cli_settings):
    # F-C precision: 0 dots, 1 dot, 2 dots.
    assert cli_settings._is_proxy_token_format("onlybase64") is False  # 0 dots
    assert cli_settings._is_proxy_token_format(PROXY_TOKEN) is True  # exactly 1
    assert cli_settings._is_proxy_token_format("a.b.c") is False  # 2 dots
    assert cli_settings._is_proxy_token_format("a.g") is False  # right not hex ('g' is past 'f')


def test_rejects_empty_or_non_string(cli_settings):
    assert cli_settings._is_proxy_token_format(None) is False
    assert cli_settings._is_proxy_token_format("") is False
    assert cli_settings._is_proxy_token_format(12345) is False  # type: ignore[arg-type]


def test_rejects_sk_prefix_on_left(cli_settings):
    # A raw key embedded as the left segment must still be rejected.
    assert cli_settings._is_proxy_token_format("sk-abc.deadbeef") is False


def test_escape_valve_bypasses_guard(cli_settings, monkeypatch):
    """OPENACE_ALLOW_ANY_CODEX_TOKEN=1 lets any token through (rollout escape)."""
    monkeypatch.setenv("OPENACE_ALLOW_ANY_CODEX_TOKEN", "1")
    assert cli_settings._is_proxy_token_format("sk-anything") is True
    assert cli_settings._is_proxy_token_format("not-a-proxy-token") is True


# ---------------------------------------------------------------------------
# write_codex_settings: guard behaviour on the write path
# ---------------------------------------------------------------------------


def _openace(parsed) -> dict:
    return parsed["model_providers"]["openace"]


def test_write_persists_valid_proxy_token_with_special_chars(cli_settings, tmp_path):
    token = make_proxy_token(b"\xfb\xff")  # left "//8="
    config_path = cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=token,
    )
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert _openace(parsed)["experimental_bearer_token"] == token
    assert "env_key" not in _openace(parsed)


def test_write_degrades_sk_key_to_env_key(cli_settings, tmp_path, caplog):
    """V3: a raw sk- key must NEVER land on disk as experimental_bearer_token."""
    with caplog.at_level(logging.WARNING):
        config_path = cli_settings.write_codex_settings(
            {},
            proxy_base_url=PROXY_BASE_URL,
            home_dir=tmp_path,
            bearer_token="sk-leaked-plaintext-key",
        )
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "experimental_bearer_token" not in _openace(parsed)
    assert _openace(parsed)["env_key"] == "OPENAI_API_KEY"
    # Warning must spell out the Windows-UWP consequence (risk A nailed down).
    assert any("UWP" in rec.getMessage() for rec in caplog.records)


def test_write_escape_valve_persists_sk_key(cli_settings, tmp_path, monkeypatch):
    """F-D: with the escape valve set, even an sk- token is persisted verbatim."""
    monkeypatch.setenv("OPENACE_ALLOW_ANY_CODEX_TOKEN", "1")
    config_path = cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token="sk-anything",
    )
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert _openace(parsed)["experimental_bearer_token"] == "sk-anything"
