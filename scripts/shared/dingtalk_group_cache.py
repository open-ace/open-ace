#!/usr/bin/env python3
"""
DingTalk Group Cache Module

Caches DingTalk group information to avoid frequent API calls.
Fetches group details from DingTalk APIs when needed.
"""

import json
import re
import time
from pathlib import Path
from typing import Optional, cast

import requests

from dingtalk_user_cache import get_dingtalk_access_token

CACHE_DIR = Path.home() / ".open-ace"
CACHE_FILE = CACHE_DIR / "dingtalk_groups.json"
CACHE_TTL = 86400  # 24 hours


def ensure_cache_dir():
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_cache() -> dict:
    """Load group cache from file."""
    ensure_cache_dir()
    if not CACHE_FILE.exists():
        return {"groups": {}, "last_updated": 0}

    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return cast(dict, json.load(f))
    except (OSError, json.JSONDecodeError):
        return {"groups": {}, "last_updated": 0}


def save_cache(cache: dict):
    """Save group cache to file."""
    ensure_cache_dir()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def extract_chat_id(label: str) -> Optional[str]:
    """Extract a DingTalk chat ID from a metadata label."""
    if not label:
        return None

    stripped = label.strip()
    if stripped.startswith("chat"):
        return stripped

    match = re.search(r"(chat[a-zA-Z0-9_-]+)", stripped)
    if match:
        return match.group(1)

    return None


def get_group_info(chat_id: str, app_key: str, app_secret: str) -> Optional[dict]:
    """Get group info from DingTalk API."""
    cache = load_cache()
    if chat_id in cache["groups"]:
        group_cache = cache["groups"][chat_id]
        if time.time() - group_cache.get("cached_at", 0) < CACHE_TTL:
            return cast(Optional[dict], group_cache.get("data"))

    token = get_dingtalk_access_token(app_key, app_secret)
    if not token:
        return None

    url = "https://oapi.dingtalk.com/chat/get"
    params = {"access_token": token, "chatid": chat_id}

    try:
        response = requests.get(url, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("errcode") == 0:
            group_info = data
            cache["groups"][chat_id] = {"data": group_info, "cached_at": time.time()}
            save_cache(cache)
            return cast(Optional[dict], group_info)
    except Exception as e:
        print(f"Error getting DingTalk group info: {e}")

    return None


def get_group_name(chat_id: str, app_key: str, app_secret: str) -> Optional[str]:
    """Get group name from cache or DingTalk API."""
    if not chat_id:
        return None

    group_info = get_group_info(chat_id, app_key, app_secret)
    if not group_info:
        return None

    return cast(Optional[str], group_info.get("name") or group_info.get("title"))


def get_group_name_from_conversation_label(
    label: str, app_key: str, app_secret: str
) -> Optional[str]:
    """Resolve group name using a conversation label that may embed chat ID."""
    cache = load_cache()
    if label in cache["groups"]:
        group_cache = cache["groups"][label]
        if time.time() - group_cache.get("cached_at", 0) < CACHE_TTL:
            data = group_cache.get("data", {})
            return cast(Optional[str], data.get("name") or data.get("title"))

    chat_id = extract_chat_id(label)
    if not chat_id:
        return None

    group_info = get_group_info(chat_id, app_key, app_secret)
    if not group_info:
        return None

    cache["groups"][label] = {"data": group_info, "cached_at": time.time()}
    save_cache(cache)
    return cast(Optional[str], group_info.get("name") or group_info.get("title"))


def clear_cache():
    """Clear group cache."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    print("DingTalk group cache cleared.")


def list_cached_groups():
    """List all cached groups."""
    cache = load_cache()
    print(f"Cached DingTalk groups ({len(cache['groups'])}):")
    for group_id, group_cache in cache["groups"].items():
        data = group_cache.get("data", {})
        name = data.get("name") or data.get("title") or "Unknown"
        cached_ago = time.time() - group_cache.get("cached_at", 0)
        print(f"  {group_id}: {name} (cached {cached_ago:.0f}s ago)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "clear":
            clear_cache()
        elif command == "list":
            list_cached_groups()
        elif command == "test" and len(sys.argv) >= 4:
            label_or_id = sys.argv[2]
            app_key = sys.argv[3]
            app_secret = sys.argv[4] if len(sys.argv) > 4 else None

            if not app_secret:
                app_secret = input("Enter App Secret: ")

            name = get_group_name_from_conversation_label(label_or_id, app_key, app_secret)
            print(f"Group {label_or_id}: {name or 'Not found'}")
    else:
        print("Usage:")
        print("  python3 dingtalk_group_cache.py clear     - Clear group cache")
        print("  python3 dingtalk_group_cache.py list      - List cached groups")
        print("  python3 dingtalk_group_cache.py test <conversation_label|chat_id> <app_key> [app_secret]")
        print("                                             - Test fetching group info")
