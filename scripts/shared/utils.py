#!/usr/bin/env python3
"""
AI Token Usage - Utils Module

Provides utility functions for the ai_token_usage project.
"""

import os
import re
import sys
from typing import Any, Optional
import logging

# Ensure scripts directory is in path for standalone script execution
_script_dir = os.path.dirname(os.path.abspath(__file__))
_scripts_dir = os.path.dirname(_script_dir)
if _scripts_dir not in sys.path:
    sys.path.insert(0, _scripts_dir)

# Use standard import after path setup
from shared import config

CONFIG_PATH = config.CONFIG_PATH


def format_tokens(tokens: int) -> str:
    """Format token count with human-readable units (K, M, B)."""
    if tokens >= 1_000_000_000:
        return f"{tokens / 1_000_000_000:.2f}B"
    elif tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.2f}M"
    elif tokens >= 1_000:
        return f"{tokens / 1_000:.2f}K"
    else:
        return str(tokens)


def parse_date(date_str: str) -> Optional[str]:
    """Validate and normalize a date string (YYYY-MM-DD)."""
    if not date_str:
        return None
    try:
        from datetime import datetime

        datetime.strptime(date_str, "%Y-%m-%d")
        return date_str
    except ValueError:
        return None


# Pattern to match placeholder values like <HOST_NAME>, <hostname>, etc.
_PLACEHOLDER_PATTERN = re.compile(r"^<[A-Za-z_]+>$")


def _is_placeholder(value: str) -> bool:
    """Check if a value is a placeholder like <HOST_NAME>."""
    if not value:
        return False
    return bool(_PLACEHOLDER_PATTERN.match(value))


def load_config(config_path: Optional[str] = None) -> dict:
    """Load configuration from JSON file."""
    import json
    import os
    import platform

    if config_path is None:
        config_path = CONFIG_PATH

    config = {}
    if os.path.exists(config_path):
        with open(config_path) as f:
            config = json.load(f)

    # If host_name is not set or is a placeholder, use system hostname
    host_name = config.get("host_name")
    if not host_name or _is_placeholder(host_name):
        config["host_name"] = platform.node()

    # Also clean up placeholder hostnames in tools config
    tools = config.get("tools", {})
    for tool_name, tool_cfg in tools.items():
        if isinstance(tool_cfg, dict):
            hostname = tool_cfg.get("hostname")
            if hostname and _is_placeholder(hostname):
                tool_cfg["hostname"] = platform.node()

    return config


def save_config(config: dict, config_path: Optional[str] = None) -> None:
    """Save configuration to JSON file."""
    import json
    import os

    if config_path is None:
        config_path = CONFIG_PATH

    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(config, f, indent=2)


def get_today() -> str:
    """Get today's date in YYYY-MM-DD format."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d")


def get_days_ago(days: int) -> str:
    """Get the date that was 'days' days ago."""
    from datetime import datetime, timedelta

    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def aggregate_daily_stats(entries: list[dict]) -> dict:
    """Aggregate daily statistics from multiple entries."""
    total = sum(e.get("tokens_used", 0) for e in entries)
    input_total = sum(e.get("input_tokens", 0) for e in entries)
    output_total = sum(e.get("output_tokens", 0) for e in entries)
    cache_total = sum(e.get("cache_tokens", 0) for e in entries)

    all_models = set()
    for e in entries:
        if e.get("models_used"):
            all_models.update(e["models_used"])

    return {
        "total_tokens": total,
        "input_tokens": input_total,
        "output_tokens": output_total,
        "cache_tokens": cache_total,
        "models": sorted(all_models),
    }


_logger = logging.getLogger("fetch_dedup")


def _message_has_text(content: Any) -> str:
    """Return the real text in a message content blob, or '' if none.

    Handles the three content shapes the fetchers store:
      * a plain string (qwen user text)
      * a JSON array of {"type":"text","text":...} parts (claude assistant)
      * a JSON string like '["some text"]' (claude assistant json.dumps form)
    Returns the concatenated text, so callers can tell a thinking-only row
    (empty) from a row carrying the final answer.
    """
    if not content:
        return ""
    if isinstance(content, str):
        s = content.strip()
        if not s:
            return ""
        # claude assistant content is stored as json.dumps([text,...])
        if s[0] == "[":
            import json as _json

            try:
                arr = _json.loads(s)
            except (ValueError, TypeError):
                return s
            if isinstance(arr, list):
                texts = [
                    p if isinstance(p, str)
                    else (p.get("text", "") if isinstance(p, dict) else "")
                    for p in arr
                ]
                return "\n".join(t for t in texts if t)
            return s
        return s
    if isinstance(content, list):
        return "\n".join(
            p.get("text", "") if isinstance(p, dict) else (p if isinstance(p, str) else "")
            for p in content
        )
    return str(content)


def warn_if_skipped_message_has_text(
    existing_row: Any,
    incoming_msg: dict,
    session_id: str,
    message_id: str,
    source: str,
) -> None:
    """Observability guard for fetch_* message dedup (#723).

    When a fetched JSONL line is skipped because a row for its
    ``(session_id, role, message_id)`` already exists, check whether the
    incoming line carries real text that the existing row does NOT. If so, that
    text would be silently dropped — the exact regression that lost claude
    review conclusions (one message.id split into a thinking line + a text line;
    the thinking line won the insert and the text line was dropped as a dup).

    This logs a WARNING so that if any tool's JSONL format changes to split one
    message across multiple lines sharing an id (or the per-tool merge step is
    bypassed), the data loss is detected instead of silent. Tools whose format
    gives every line a distinct id (qwen, codex) never trigger it.
    """
    try:
        existing_content = ""
        if existing_row:
            # existing_row is a row tuple/dict from "SELECT id, content ...".
            content = None
            if isinstance(existing_row, (tuple, list)):
                content = existing_row[1] if len(existing_row) > 1 else None
            elif isinstance(existing_row, dict):
                content = existing_row.get("content")
            existing_content = _message_has_text(content)
        incoming_text = _message_has_text(incoming_msg.get("content", ""))
        # Only warn when the incoming line has text the stored row lacks.
        if incoming_text and incoming_text not in existing_content:
            _logger.warning(
                "%s: dedup skipped a line for session=%s message_id=%s that "
                "carries text NOT in the stored row (%d chars dropped). "
                "This indicates one logical message split across lines sharing "
                "an id; content may be lost.",
                source,
                str(session_id)[:8],
                str(message_id)[:12] if message_id else "<none>",
                len(incoming_text),
            )
    except Exception:
        # Observability must never break a fetch run.
        pass
