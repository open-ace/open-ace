#!/usr/bin/env python3
"""
Feishu Group Cache Module

Caches Feishu group/chatter information to avoid frequent API calls.
Fetches group details from Feishu API when needed.
"""

import json
import time
import requests
from typing import Optional, Dict
from pathlib import Path

# Cache file location
CACHE_DIR = Path.home() / ".open-ace"
CACHE_FILE = CACHE_DIR / "feishu_groups.json"
CACHE_TTL = 86400  # Cache TTL in seconds (24 hours) - groups don't change often


def ensure_cache_dir():
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_cache() -> Dict:
    """Load group cache from file."""
    ensure_cache_dir()
    if not CACHE_FILE.exists():
        return {"groups": {}, "last_updated": 0}

    try:
        with open(CACHE_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"groups": {}, "last_updated": 0}


def save_cache(cache: Dict):
    """Save group cache to file."""
    ensure_cache_dir()
    with open(CACHE_FILE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_feishu_token(app_id: str, app_secret: str) -> Optional[str]:
    """Get Feishu API access token using tenant access token."""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {
        "app_id": app_id,
        "app_secret": app_secret
    }

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("code") == 0:
            return data.get("tenant_access_token")
        else:
            print(f"Failed to get Feishu token: {data}")
            return None
    except Exception as e:
        print(f"Error getting Feishu token: {e}")
        return None


def get_group_subject(group_id: str, token: str) -> Optional[str]:
    """Get group subject from Feishu API using internal API."""
    # Use the chat get API to get group info
    url = f"https://open.feishu.cn/open-apis/chat/v4/chat/{group_id}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("code") == 0:
            chat_info = data.get("data", {})
            return chat_info.get("name")
        else:
            print(f"Failed to get group info for {group_id}: {data}")
            return None
    except Exception as e:
        print(f"Error getting group info: {e}")
        return None


def get_group_subject_from_conversation_label(label: str, app_id: str, app_secret: str) -> Optional[str]:
    """Get group subject using conversation_label."""
    # conversation_label format in Feishu is typically: chat_[chat_id]_[timestamp]
    if not label or not label.startswith("chat_"):
        return None

    # Extract chat_id from conversation_label
    parts = label.split("_")
    if len(parts) < 2:
        return None

    # Try to get chat_id (it could be parts[1] or combined from parts[1:])
    # Format: chat_[chat_id]_[timestamp] or chat_[chat_id]_[other_info]
    chat_id = "_".join(parts[1:-1]) if len(parts) > 2 else parts[1]

    if not chat_id:
        return None

    cache = load_cache()

    # Check cache first
    if chat_id in cache["groups"]:
        group_cache = cache["groups"][chat_id]
        if time.time() - group_cache.get("cached_at", 0) < CACHE_TTL:
            return group_cache.get("name")

    # Get access token
    token = get_feishu_token(app_id, app_secret)
    if not token:
        return None

    # Fetch group info from API
    group_name = get_group_subject(chat_id, token)

    if group_name:
        # Cache the result
        cache["groups"][chat_id] = {
            "name": group_name,
            "cached_at": time.time()
        }
        save_cache(cache)

    return group_name


def get_group_name(group_id: str, app_id: str, app_secret: str) -> Optional[str]:
    """Get group name from cache or API."""
    cache = load_cache()

    # Check cache first
    if group_id in cache["groups"]:
        group_cache = cache["groups"][group_id]
        if time.time() - group_cache.get("cached_at", 0) < CACHE_TTL:
            return group_cache.get("name")

    # Get access token
    token = get_feishu_token(app_id, app_secret)
    if not token:
        return None

    # Fetch group info from API
    group_name = get_group_subject(group_id, token)

    if group_name:
        # Cache the result
        cache["groups"][group_id] = {
            "name": group_name,
            "cached_at": time.time()
        }
        save_cache(cache)

    return group_name


def get_group_name_from_conversation_label(label: str, app_id: str, app_secret: str) -> Optional[str]:
    """Get group name using conversation_label."""
    if not label:
        return None

    cache = load_cache()

    # Check cache first
    if label in cache["groups"]:
        group_cache = cache["groups"][label]
        if time.time() - group_cache.get("cached_at", 0) < CACHE_TTL:
            return group_cache.get("name")

    # Try to get group name from API
    group_name = get_group_subject_from_conversation_label(label, app_id, app_secret)

    if group_name:
        # Cache the result
        cache["groups"][label] = {
            "name": group_name,
            "cached_at": time.time()
        }
        save_cache(cache)

    return group_name


def clear_cache():
    """Clear group cache."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    print("Feishu group cache cleared.")


def list_cached_groups():
    """List all cached groups."""
    cache = load_cache()
    print(f"Cached groups ({len(cache['groups'])}):")
    for group_id, group_cache in cache["groups"].items():
        name = group_cache.get("name", "Unknown")
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
            # Test fetching a group
            label_or_id = sys.argv[2]
            app_id = sys.argv[3]
            app_secret = sys.argv[4] if len(sys.argv) > 4 else None

            if not app_secret:
                app_secret = input("Enter App Secret: ")

            name = get_group_name_from_conversation_label(label_or_id, app_id, app_secret)
            print(f"Group {label_or_id}: {name or 'Not found'}")
    else:
        print("Usage:")
        print("  python3 feishu_group_cache.py clear     - Clear group cache")
        print("  python3 feishu_group_cache.py list      - List cached groups")
        print("  python3 feishu_group_cache.py test <conversation_label|group_id> <app_id> [app_secret]")
        print("                                           - Test fetching group info")
