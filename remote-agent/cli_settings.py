"""Helpers for applying CLI settings to local tool config files."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
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

# Serializes all read-modify-write access to ~/.codex/config.toml within this
# process. write_codex_settings and clear_codex_bearer_token each do a
# load -> mutate -> _atomic_write cycle; without a lock, a concurrent caller's
# stale snapshot can clobber another's freshly written field (lost update) even
# though each individual write is atomic. RemoteAgent is a long-lived process
# driving multiple terminals concurrently, so this race is reachable.
_CODEX_CONFIG_LOCK = threading.Lock()


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


# Sentinel env var that disables the proxy-token format guard below. Intended
# only as an escape hatch during a ``generate_proxy_token`` format-rollout
# window: set ``OPENACE_ALLOW_ANY_CODEX_TOKEN=1`` to let an unrecognized token
# through to ``~/.codex/config.toml`` verbatim instead of degrading to
# ``env_key`` auth (which leaves Windows-UWP Codex unable to authenticate).
_ALLOW_ANY_CODEX_TOKEN_ENV = "OPENACE_ALLOW_ANY_CODEX_TOKEN"


def resolve_codex_bearer_token(openai_token: str | None) -> str | None:
    """Return the Codex bearer token to persist, or ``None``.

    Codex desktop on Windows UWP cannot read process environment variables, so
    the proxy token must be written into ``~/.codex/config.toml`` as
    ``experimental_bearer_token``. On every other platform Codex reads
    ``OPENAI_API_KEY`` from the environment, so nothing is persisted.

    This is the single source of truth consulted by both the WebSocket launch
    path (``remote-agent/agent.py``) and the SSH-CLI launch path
    (``remote-agent/openace_cli.py``); centralizing the ``os.name`` gate here
    keeps the two call sites from drifting apart again (Issue #1828 finding #1).
    """
    if os.name == "nt" and openai_token:
        return openai_token
    return None


def _is_proxy_token_format(token: str | None) -> bool:
    """Loosely validate that ``token`` looks like an Open ACE proxy token.

    Proxy tokens produced by ``generate_proxy_token`` have the shape
    ``f"{base64(payload)}.{hmac_sha256_hex}"``: a single ``.`` separator with a
    hex (``[0-9a-f]+``) signature on the right and a standard-base64 payload on
    the left (which may legally contain ``+``, ``/`` and ``=``). We deliberately
    do NOT constrain the left segment's character set, so a real standard-base64
    payload is never mis-rejected as base64url-only.

    A raw upstream API key (``sk-...``) or any other shape fails this check.
    Set ``OPENACE_ALLOW_ANY_CODEX_TOKEN=1`` to bypass validation entirely.
    """
    if os.environ.get(_ALLOW_ANY_CODEX_TOKEN_ENV) == "1":
        return True
    if not isinstance(token, str) or not token:
        return False
    parts = token.split(".")
    if len(parts) != 2:
        return False
    left, right = parts
    if not left or left.startswith("sk-"):
        return False
    return re.fullmatch(r"[0-9a-f]+", right) is not None


def _should_codex_persist_bearer_token(home_dir: Path) -> bool:
    """Whether Open ACE should persist the Codex bearer token to disk.

    Reads the Open ACE-private sidecar ``~/.codex/openace_state.json`` for a
    ``persist_bearer_token`` flag (default ``True``). This lives in a separate
    file from ``config.toml`` so the opt-out never round-trips through Codex's
    own (strict) TOML schema and risks breaking Codex startup. Open ACE only
    ever *reads* this sidecar; it never writes it, to avoid clobbering a user's
    manual opt-out (Issue #1828 finding #4).
    """
    state = _load_json_file(home_dir / ".codex" / "openace_state.json")
    flag = state.get("persist_bearer_token")
    if isinstance(flag, bool):
        return flag
    return True


def _active_codex_provider_name(settings: dict[str, Any]) -> str:
    """Resolve the model provider Open ACE should route Codex through.

    Defaults to ``"openace"`` for backward compatibility when the caller hasn't
    declared a provider, so the historical default config keeps working
    (Issue #1828 finding #2).
    """
    provider = settings.get("model_provider")
    if isinstance(provider, str) and provider:
        return provider
    return "openace"


def _normalize_codex_settings(
    settings: dict[str, Any],
    proxy_base_url: str,
    bearer_token: str | None = None,
) -> dict[str, Any]:
    """Normalize Codex settings for config.toml.

    Args:
        settings: Raw Codex settings dict.
        proxy_base_url: LLM proxy base URL.
        bearer_token: Optional Open ACE proxy token for Windows UWP
            compatibility. Must be a ``generate_proxy_token`` product
            (``{standard-base64 payload}.{hmac-sha256 hex}``); a raw ``sk-``
            key is rejected upstream in ``write_codex_settings`` and degrades to
            ``env_key`` auth. When provided, the active provider uses
            ``experimental_bearer_token`` instead of ``env_key``.

    Implicit contract (Issue #1828 finding #2 / F-E): Open ACE force-overrides
    the *active* provider's ``base_url`` (and ``experimental_bearer_token`` when
    a proxy token is supplied) on every write. This is correct only when the
    active provider points at the Open ACE LLM proxy; a user who repoints the
    active provider at a third-party upstream will have that upstream's
    ``base_url`` rewritten to the Open ACE proxy.
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

    active_name = _active_codex_provider_name(cleaned)
    provider = providers.get(active_name)
    if not isinstance(provider, dict):
        provider = {}
    provider = {
        **provider,
        "name": provider.get("name", "Open ACE Proxy"),
        "wire_api": provider.get("wire_api", "responses"),
        "base_url": proxy_base_url,
    }
    _set_codex_auth_mode(provider, bearer_token=bearer_token)
    providers[active_name] = provider
    return cleaned


def _set_codex_auth_mode(provider: dict[str, Any], bearer_token: str | None) -> None:
    """Keep Codex auth fields mutually exclusive across repeated rewrites."""
    if bearer_token:
        provider["experimental_bearer_token"] = bearer_token
        provider.pop("env_key", None)
    else:
        provider["env_key"] = "OPENAI_API_KEY"
        provider.pop("experimental_bearer_token", None)


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
        bearer_token: Optional Open ACE proxy token for Windows UWP
            compatibility. Must be a ``generate_proxy_token`` product
            (``{standard-base64 payload}.{hmac-sha256 hex}``); any other shape
            (e.g. a raw ``sk-`` key) is rejected by the guard below and degrades
            to ``env_key`` auth, which means Windows-UWP Codex cannot
            authenticate directly. Persistence can also be disabled per-host via
            the ``~/.codex/openace_state.json`` sidecar.
    """
    base_dir = home_dir or Path.home()
    config_path = base_dir / ".codex" / "config.toml"

    # Issue #1828 finding #3 (hardening): only ever persist a genuine proxy
    # token. A raw upstream key or a malformed token must NOT land on disk as
    # ``experimental_bearer_token`` (useless to Codex AND a plaintext-secret
    # leak). Degrade to env_key auth and warn loudly about the Windows-UWP
    # consequence rather than raising, so a bad token never blocks terminal start.
    effective_token = bearer_token
    if bearer_token is not None and not _is_proxy_token_format(bearer_token):
        logger.warning(
            "Refusing to persist a non-proxy-format Codex bearer token; "
            "falling back to env_key auth. Windows UWP Codex will be unable to "
            "authenticate directly. Set %s=1 to override during a token-format "
            "rollout.",
            _ALLOW_ANY_CODEX_TOKEN_ENV,
        )
        effective_token = None

    normalized = _normalize_codex_settings(
        parse_codex_settings(settings),
        proxy_base_url,
        bearer_token=effective_token,
    )
    # ``persisted_token`` tracks the token that actually lands on disk; it can
    # be downgraded from ``effective_token`` by the #4 opt-out below. It also
    # gates the POSIX chmod so we never claim file-permission protection for a
    # config that carries no secret.
    persisted_token = effective_token
    # Hold the process-wide lock across load->mutate->write so a concurrent
    # clear_codex_bearer_token (or another write_codex_settings) cannot interleave
    # and lose fields via a stale-snapshot overwrite.
    with _CODEX_CONFIG_LOCK:
        # Issue #1828 finding #4 (improvement): respect a user's sidecar request
        # to never persist the bearer token, even when a valid proxy token is
        # available. Read the sidecar inside the lock to avoid TOCTOU with our
        # own config writes.
        if persisted_token and not _should_codex_persist_bearer_token(base_dir):
            logger.warning(
                "Codex bearer-token persistence disabled via "
                "~/.codex/openace_state.json (persist_bearer_token=false); "
                "using env_key auth. Windows UWP Codex will be unable to "
                "authenticate directly."
            )
            persisted_token = None
        merged = _deep_merge_dicts(_load_toml_file(config_path), normalized)
        # Re-calibrate auth on the *active* provider of the merged config (not a
        # hard-coded "openace"), so a custom model_provider's token is set on the
        # right entry (Issue #1828 finding #2).
        active_name = _active_codex_provider_name(merged)
        provider = merged.get("model_providers", {}).get(active_name)
        if isinstance(provider, dict):
            _set_codex_auth_mode(provider, bearer_token=persisted_token)
        _atomic_write_text(config_path, dump_toml(merged))
    # Ensure config file has secure permissions when it carries a bearer token.
    # chmod(0o600) only enforces ACLs on POSIX; on Windows it merely toggles the
    # read-only attribute and leaves the file world-readable, so callers relying
    # on file-permission isolation on Windows must layer a Win32 ACL (icacls /
    # pywin32) on top rather than trusting the POSIX bits here.
    if persisted_token and os.name == "posix":
        config_path.chmod(0o600)
    return config_path


def clear_codex_bearer_token(home_dir: Path | None = None) -> None:
    """Remove a persisted Codex bearer token from ``~/.codex/config.toml``.

    The Windows-UWP launch path writes ``experimental_bearer_token`` into the
    on-disk Codex config because Codex desktop cannot read environment
    variables. That token is a still-valid proxy token until server-side
    expiry, so it must be scrubbed as soon as the terminal session that needed
    it stops or exits. This helper rewrites the config to drop the bearer
    token and fall back to ``env_key`` auth, preserving all non-sensitive
    user preferences and proxy routing.

    Only the *active* provider's token is cleared (Issue #1828 finding #2):
    ``config.toml`` is shared across terminals and a format match cannot
    distinguish a token Open ACE wrote from a proxy token the user configured
    for another upstream under a non-active provider, so a full scan would risk
    scrubbing the wrong secret. Stale tokens on non-active providers are a
    known, accepted leftover, not in scope here.

    Args:
        home_dir: Optional home directory path; defaults to ``Path.home()``.

    This is idempotent: a missing config file or a config without a bearer
    token is a no-op.
    """
    base_dir = home_dir or Path.home()
    config_path = base_dir / ".codex" / "config.toml"
    if not config_path.exists():
        return
    # Hold the process-wide lock across load->mutate->write so a concurrent
    # write_codex_settings cannot interleave and lose fields via a stale-snapshot
    # overwrite. The early-return no-op checks stay outside the lock only when
    # the file is absent; once it exists we serialize the full RMW.
    with _CODEX_CONFIG_LOCK:
        try:
            merged = _load_toml_file(config_path)
        except OSError:
            return
        providers = merged.get("model_providers")
        if not isinstance(providers, dict):
            logger.warning(
                "Codex config %s is missing model_providers; cannot scrub bearer token",
                config_path,
            )
            return
        active_name = _active_codex_provider_name(merged)
        active = providers.get(active_name)
        if not isinstance(active, dict) or "experimental_bearer_token" not in active:
            return
        # Re-apply env-key auth, which also pops the bearer token, then persist.
        _set_codex_auth_mode(active, bearer_token=None)
        try:
            _atomic_write_text(config_path, dump_toml(merged))
        except OSError as exc:
            logger.warning("Failed to clear Codex bearer token from %s: %s", config_path, exc)


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
