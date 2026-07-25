"""Issue #1828 finding #2 (correctness): token write/clear must target the
*active* model provider, not a hard-coded ``"openace"``.

When ``model_provider`` names a custom provider, Open ACE must route the proxy
``base_url`` and ``experimental_bearer_token`` onto THAT provider (auto-creating
its entry if missing), and ``clear_codex_bearer_token`` must scrub only the
active provider's token — never a third-party token configured under a
non-active provider.
"""

from __future__ import annotations

import pytest
from _helpers import PROXY_BASE_URL, PROXY_TOKEN, load_cli_settings


@pytest.fixture(scope="module")
def cli_settings():
    return load_cli_settings()


# ---------------------------------------------------------------------------
# V4: write routes to the active provider
# ---------------------------------------------------------------------------


def test_write_routes_token_to_custom_active_provider(cli_settings, tmp_path):
    config_path = cli_settings.write_codex_settings(
        'model_provider = "my-proxy"\n[model_providers.my-proxy]\nname = "Mine"\n',
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["model_provider"] == "my-proxy"
    mine = parsed["model_providers"]["my-proxy"]
    assert mine["experimental_bearer_token"] == PROXY_TOKEN
    assert mine["base_url"] == PROXY_BASE_URL
    # The default "openace" provider is NOT force-injected when inactive.
    assert "experimental_bearer_token" not in parsed["model_providers"].get("openace", {})


def test_write_auto_creates_missing_active_provider(cli_settings, tmp_path):
    config_path = cli_settings.write_codex_settings(
        'model_provider = "my-proxy"\n',
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    mine = parsed["model_providers"]["my-proxy"]
    assert mine["experimental_bearer_token"] == PROXY_TOKEN
    assert mine["base_url"] == PROXY_BASE_URL
    assert mine["name"] == "Open ACE Proxy"
    assert mine["wire_api"] == "responses"


def test_write_default_provider_still_openace(cli_settings, tmp_path):
    """Regression: with no model_provider declared, the active provider is still
    ``openace`` and behaviour matches the pre-#1828 default."""
    config_path = cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert parsed["model_provider"] == "openace"
    assert parsed["model_providers"]["openace"]["experimental_bearer_token"] == PROXY_TOKEN


# ---------------------------------------------------------------------------
# V5: clear only scrubs the active provider
# ---------------------------------------------------------------------------


def test_clear_only_scrubs_active_provider_token(cli_settings, tmp_path):
    """Two providers each carry a bearer token; only the ACTIVE one is cleared."""
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(
        'model_provider = "my-proxy"\n\n'
        "[model_providers.openace]\n"
        'name = "Open ACE"\n'
        'experimental_bearer_token = "stale-third-party"\n\n'
        "[model_providers.my-proxy]\n"
        'name = "Mine"\n'
        f'experimental_bearer_token = "{PROXY_TOKEN}"\n',
        encoding="utf-8",
    )

    cli_settings.clear_codex_bearer_token(home_dir=tmp_path)

    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    # Active provider scrubbed, env_key restored.
    mine = parsed["model_providers"]["my-proxy"]
    assert "experimental_bearer_token" not in mine
    assert mine["env_key"] == "OPENAI_API_KEY"
    # Non-active provider's token is PRESERVED (no full-scan scrub).
    assert parsed["model_providers"]["openace"]["experimental_bearer_token"] == "stale-third-party"


def test_clear_default_openace_provider_still_works(cli_settings, tmp_path):
    """Regression: with no model_provider set, clear still scrubs openace."""
    cli_settings.write_codex_settings(
        {},
        proxy_base_url=PROXY_BASE_URL,
        home_dir=tmp_path,
        bearer_token=PROXY_TOKEN,
    )
    cli_settings.clear_codex_bearer_token(home_dir=tmp_path)

    config_path = tmp_path / ".codex" / "config.toml"
    parsed = cli_settings.tomllib.loads(config_path.read_text(encoding="utf-8"))
    assert "experimental_bearer_token" not in parsed["model_providers"]["openace"]
    assert parsed["model_providers"]["openace"]["env_key"] == "OPENAI_API_KEY"
