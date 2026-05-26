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
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
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


def _normalize_codex_settings(settings: dict[str, Any], proxy_base_url: str) -> dict[str, Any]:
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
        "env_key": "OPENAI_API_KEY",
    }
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
) -> Path:
    """Merge and write ~/.codex/config.toml."""
    base_dir = home_dir or Path.home()
    config_path = base_dir / ".codex" / "config.toml"
    normalized = _normalize_codex_settings(parse_codex_settings(settings), proxy_base_url)
    merged = _deep_merge_dicts(_load_toml_file(config_path), normalized)
    _atomic_write_text(config_path, dump_toml(merged))
    return config_path


def apply_cli_settings(
    cli_settings: dict[str, Any],
    proxy_base_url: str,
    home_dir: Path | None = None,
) -> None:
    """Apply CLI settings for supported tools to local config files."""
    if not cli_settings:
        return

    for tool_name, settings in cli_settings.items():
        try:
            if tool_name == "claude-code" and isinstance(settings, dict):
                write_claude_settings(settings, home_dir=home_dir)
            elif tool_name == "qwen-code" and isinstance(settings, dict):
                write_qwen_settings(settings, proxy_base_url=proxy_base_url, home_dir=home_dir)
            elif tool_name in {"codex", "codex-cli"}:
                write_codex_settings(settings, proxy_base_url=proxy_base_url, home_dir=home_dir)
            else:
                logger.warning("Unknown tool name for settings: %s", tool_name)
        except Exception as exc:
            logger.error("Failed to write settings for %s: %s", tool_name, exc)
