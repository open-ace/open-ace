"""Helpers for applying CLI settings to local tool config files."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from cli_adapters.base import normalize_model_providers
from constants import SENSITIVE_ENV_KEYS, collect_dynamic_env_keys

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

logger = logging.getLogger(__name__)

_BARE_TOML_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def _atomic_write_json(filepath: Path, data: dict | list) -> None:
    """Atomically write JSON data to disk."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=filepath.parent,
        suffix=".tmp",
    ) as tmp:
        json.dump(data, tmp, indent=2, ensure_ascii=False)
        tmp.write("\n")
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, filepath)


def _atomic_write_text(filepath: Path, content: str) -> None:
    """Atomically write text content to disk."""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        dir=filepath.parent,
        suffix=".tmp",
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, filepath)


def _deep_merge_dicts(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Merge dicts recursively while preserving unrelated existing keys."""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge_dicts(merged[key], value)
        else:
            merged[key] = value
    return merged


def _strip_sensitive_env(settings: dict[str, Any]) -> dict[str, Any]:
    """Remove API keys and base URLs from a CLI settings mapping."""
    cleaned = settings.copy()
    all_sensitive = SENSITIVE_ENV_KEYS | collect_dynamic_env_keys(cleaned)

    env = cleaned.get("env", {})
    if env:
        env = {k: v for k, v in env.items() if k not in all_sensitive}
        cleaned["env"] = env

    return cleaned


def _load_json_file(filepath: Path) -> dict[str, Any]:
    if not filepath.exists():
        return {}
    try:
        return json.loads(filepath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _load_toml_file(filepath: Path) -> dict[str, Any]:
    if not filepath.exists():
        return {}
    try:
        return tomllib.loads(filepath.read_text(encoding="utf-8"))
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _toml_key(key: str) -> str:
    if _BARE_TOML_KEY_RE.match(key):
        return key
    escaped = key.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_value(value: Any) -> str:
    if isinstance(value, str):
        escaped = (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\t", "\\t")
            .replace("\r", "\\r")
        )
        return f'"{escaped}"'
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        parts = [f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()]
        return "{ " + ", ".join(parts) + " }"
    raise TypeError(f"Unsupported TOML value type: {type(value)!r}")


def _dump_toml_table(lines: list[str], data: dict[str, Any], path: list[str]) -> None:
    scalar_items: list[tuple[str, Any]] = []
    table_items: list[tuple[str, dict[str, Any]]] = []

    for key, value in data.items():
        if isinstance(value, dict):
            table_items.append((key, value))
        else:
            scalar_items.append((key, value))

    if path:
        lines.append("[" + ".".join(_toml_key(part) for part in path) + "]")

    for key, value in scalar_items:
        lines.append(f"{_toml_key(key)} = {_toml_value(value)}")

    if scalar_items and table_items:
        lines.append("")

    for index, (key, value) in enumerate(table_items):
        _dump_toml_table(lines, value, path + [key])
        if index != len(table_items) - 1:
            lines.append("")


def dump_toml(data: dict[str, Any]) -> str:
    """Serialize a nested dict to TOML."""
    lines: list[str] = []
    _dump_toml_table(lines, data, [])
    return "\n".join(lines).rstrip() + "\n"


def _normalize_qwen_settings(settings: dict[str, Any], proxy_base_url: str) -> dict[str, Any]:
    cleaned = _strip_sensitive_env(settings)
    for provider_models in cleaned.get("modelProviders", {}).values():
        if isinstance(provider_models, list):
            for model in provider_models:
                if isinstance(model, dict):
                    model.pop("baseUrl", None)
    normalize_model_providers(cleaned, proxy_base_url=proxy_base_url)
    if "$version" not in cleaned:
        cleaned["$version"] = 3
    return cleaned


def parse_codex_settings(raw_settings: dict[str, Any] | str | None) -> dict[str, Any]:
    """Parse stored Codex settings from dict or TOML string form."""
    if raw_settings is None:
        return {}
    if isinstance(raw_settings, dict):
        return raw_settings.copy()
    if isinstance(raw_settings, str):
        try:
            parsed = tomllib.loads(raw_settings)
        except tomllib.TOMLDecodeError as exc:
            logger.warning("Failed to parse Codex settings TOML: %s", exc)
            return {}
        if isinstance(parsed, dict):
            return parsed
    logger.warning("Unsupported Codex settings type: %s", type(raw_settings).__name__)
    return {}


def _normalize_codex_settings(
    settings: dict[str, Any],
    proxy_base_url: str,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Normalize Codex settings for config.toml.

    Args:
        settings: Raw Codex settings dict.
        proxy_base_url: LLM proxy base URL.
        bearer_token: Optional bearer token for Windows UWP compatibility.
            When provided, uses ``experimental_bearer_token`` instead of ``env_key``.
    """
    cleaned = _strip_sensitive_env(settings)
    if "model_reasoning_summary" not in cleaned:
        cleaned["model_reasoning_summary"] = "auto"
    if "model_provider" not in cleaned:
        cleaned["model_provider"] = "openace"

    providers = cleaned.get("model_providers")
    if not isinstance(providers, dict):
        providers = {}
        cleaned["model_providers"] = providers

    openace_provider = providers.get("openace")
    if not isinstance(openace_provider, dict):
        openace_provider = {}
    openace_provider = {
        **openace_provider,
        "name": openace_provider.get("name", "Open ACE Proxy"),
        "wire_api": openace_provider.get("wire_api", "responses"),
        "base_url": proxy_base_url,
    }
    # Windows UWP: use experimental_bearer_token to bypass env var restrictions
    if bearer_token:
        openace_provider["experimental_bearer_token"] = bearer_token
    else:
        openace_provider["env_key"] = "OPENAI_API_KEY"
    providers["openace"] = openace_provider
    return cleaned


def write_claude_settings(settings: dict[str, Any], home_dir: Path | None = None) -> Path:
    """Merge and write ~/.claude/settings.json."""
    base_dir = home_dir or Path.home()
    settings_path = base_dir / ".claude" / "settings.json"
    merged = {**_load_json_file(settings_path), **_strip_sensitive_env(settings)}
    _atomic_write_json(settings_path, merged)
    return settings_path


def write_qwen_settings(
    settings: dict[str, Any],
    proxy_base_url: str,
    home_dir: Path | None = None,
) -> Path:
    """Merge and write ~/.qwen/settings.json."""
    base_dir = home_dir or Path.home()
    settings_path = base_dir / ".qwen" / "settings.json"
    normalized = _normalize_qwen_settings(settings, proxy_base_url=proxy_base_url)
    merged = {**_load_json_file(settings_path), **normalized}
    _atomic_write_json(settings_path, merged)
    return settings_path


def write_codex_settings(
    settings: dict[str, Any] | str,
    proxy_base_url: str,
    home_dir: Path | None = None,
    bearer_token: str | None = None,
) -> Path:
    """Merge and write ~/.codex/config.toml.

    Args:
        settings: Codex settings dict or TOML string.
        proxy_base_url: LLM proxy base URL.
        home_dir: Optional home directory path.
        bearer_token: Optional bearer token for Windows UWP compatibility.
            When provided, uses ``experimental_bearer_token`` instead of ``env_key``.
    """
    base_dir = home_dir or Path.home()
    config_path = base_dir / ".codex" / "config.toml"
    normalized = _normalize_codex_settings(
        parse_codex_settings(settings),
        proxy_base_url,
        bearer_token=bearer_token,
    )
    merged = _deep_merge_dicts(_load_toml_file(config_path), normalized)
    _atomic_write_text(config_path, dump_toml(merged))
    # Ensure config file has secure permissions (0600) when containing bearer token
    if bearer_token:
        config_path.chmod(0o600)
    return config_path


def write_zcode_settings(
    settings: dict[str, Any],
    proxy_base_url: str,
    home_dir: Path | None = None,
) -> Path:
    """Merge and write ~/.zcode/cli/config.json.

    ZCode uses a ``zcode.config.v1`` schema where each provider lives under a
    top-level ``provider`` map with an ``options`` block (``baseURL``/``apiKey``)
    and a ``model`` selector in ``provider/model`` format. We merge onto any
    existing config, inject the proxy ``baseURL`` and ``apiKey`` into the
    ``zai`` (Anthropic-compatible) provider, and default the model selector.
    """
    base_dir = home_dir or Path.home()
    config_path = base_dir / ".zcode" / "cli" / "config.json"
    merged = _load_json_file(config_path)

    providers = merged.setdefault("provider", {})
    zai = providers.setdefault(
        "zai",
        {
            "id": "zai",
            "kind": "anthropic",
            "name": "Z.AI (Anthropic-compatible)",
            "options": {},
        },
    )
    if not isinstance(zai, dict):
        zai = providers["zai"] = {
            "id": "zai",
            "kind": "anthropic",
            "name": "Z.AI (Anthropic-compatible)",
            "options": {},
        }
    options = zai.setdefault("options", {})
    if not isinstance(options, dict):
        options = zai["options"] = {}

    # Route model traffic through the Open ACE proxy.
    options["baseURL"] = proxy_base_url.rstrip("/")
    api_key = settings.get("api_key") or settings.get("apiKey")
    if api_key:
        options["apiKey"] = api_key

    # Default the model selector if the user hasn't configured one.
    merged.setdefault("model", {"main": "zai/glm-5.2", "lite": "zai/glm-4.5-air"})

    _atomic_write_json(config_path, merged)
    return config_path


def apply_cli_settings(
    cli_settings: dict[str, Any],
    proxy_base_url: str,
    home_dir: Path | None = None,
    codex_bearer_token: str | None = None,
) -> None:
    """Apply CLI settings for supported tools to local config files.

    Args:
        cli_settings: Dict mapping tool names to their settings.
        proxy_base_url: LLM proxy base URL.
        home_dir: Optional home directory path.
        codex_bearer_token: Optional bearer token for Codex on Windows UWP.
            When provided, Codex config uses ``experimental_bearer_token``
            instead of ``env_key`` to bypass UWP environment variable restrictions.
    """
    if not cli_settings:
        return

    for tool_name, settings in cli_settings.items():
        try:
            if tool_name == "claude-code" and isinstance(settings, dict):
                write_claude_settings(settings, home_dir=home_dir)
            elif tool_name == "qwen-code" and isinstance(settings, dict):
                write_qwen_settings(settings, proxy_base_url=proxy_base_url, home_dir=home_dir)
            elif tool_name in {"codex", "codex-cli"}:
                write_codex_settings(
                    settings,
                    proxy_base_url=proxy_base_url,
                    home_dir=home_dir,
                    bearer_token=codex_bearer_token,
                )
            elif tool_name in {"zcode", "zcode-code"} and isinstance(settings, dict):
                write_zcode_settings(settings, proxy_base_url=proxy_base_url, home_dir=home_dir)
            else:
                logger.warning("Unknown tool name for settings: %s", tool_name)
        except Exception as exc:
            logger.error("Failed to write settings for %s: %s", tool_name, exc)
