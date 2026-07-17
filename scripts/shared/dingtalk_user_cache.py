#!/usr/bin/env python3
"""
DingTalk User Cache Module

Caches DingTalk user information to avoid frequent API calls.
Fetches user details from DingTalk APIs when needed.
"""

import json
import time
from pathlib import Path
from typing import Optional, cast

import requests

CACHE_DIR = Path.home() / ".open-ace"
CACHE_FILE = CACHE_DIR / "dingtalk_users.json"
CACHE_TTL = 3600  # 1 hour


def ensure_cache_dir():
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_cache() -> dict:
    """Load user cache from file."""
    ensure_cache_dir()
    if not CACHE_FILE.exists():
        return {"users": {}, "last_updated": 0}

    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return cast(dict, json.load(f))
    except (OSError, json.JSONDecodeError):
        return {"users": {}, "last_updated": 0}


def save_cache(cache: dict):
    """Save user cache to file."""
    ensure_cache_dir()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_dingtalk_access_token(app_key: str, app_secret: str) -> Optional[str]:
    """Get DingTalk access token for an internal application."""
    url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
    payload = {"appKey": app_key, "appSecret": app_secret}

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        return cast(Optional[str], data.get("accessToken"))
    except Exception as e:
        print(f"Error getting DingTalk access token: {e}")
        return None


def get_user_info(user_id: str, app_key: str, app_secret: str) -> Optional[dict]:
    """Get user info from DingTalk API."""
    cache = load_cache()
    if user_id in cache["users"]:
        user_cache = cache["users"][user_id]
        if time.time() - user_cache.get("cached_at", 0) < CACHE_TTL:
            return cast(Optional[dict], user_cache.get("data"))

    token = get_dingtalk_access_token(app_key, app_secret)
    if not token:
        return None

    url = f"https://oapi.dingtalk.com/topapi/v2/user/get?access_token={token}"
    payload = {"userid": user_id}

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("errcode") == 0:
            user_info = data.get("result", {})
            cache["users"][user_id] = {"data": user_info, "cached_at": time.time()}
            save_cache(cache)
            return cast(Optional[dict], user_info)
    except Exception as e:
        print(f"Error getting DingTalk user info: {e}")

    return None


def get_user_display_name(user_id: str, app_key: str, app_secret: str) -> Optional[str]:
    """Get user's display name from DingTalk API."""
    if not user_id:
        return None

    user_info = get_user_info(user_id, app_key, app_secret)
    if not user_info:
        return None

    display_name = (
        user_info.get("name")
        or user_info.get("nick")
        or user_info.get("nickname")
        or user_info.get("realAuthedName")
    )
    return cast(Optional[str], display_name)


def get_user_display_name_from_cache(user_id: str) -> Optional[str]:
    """Get user display name from local cache without API call."""
    cache = load_cache()
    if user_id not in cache["users"]:
        return None

    user_cache = cache["users"][user_id]
    if time.time() - user_cache.get("cached_at", 0) >= CACHE_TTL:
        return None

    user_data = user_cache.get("data", {})
    return cast(
        Optional[str],
        user_data.get("name")
        or user_data.get("nick")
        or user_data.get("nickname")
        or user_data.get("realAuthedName"),
    )


def clear_cache():
    """Clear user cache."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    print("DingTalk user cache cleared.")


def list_cached_users():
    """List all cached users."""
    cache = load_cache()
    print(f"Cached DingTalk users ({len(cache['users'])}):")
    for user_id, user_cache in cache["users"].items():
        user_data = user_cache.get("data", {})
        name = (
            user_data.get("name") or user_data.get("nick") or user_data.get("nickname") or "Unknown"
        )
        cached_ago = time.time() - user_cache.get("cached_at", 0)
        print(f"  {user_id}: {name} (cached {cached_ago:.0f}s ago)")


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        command = sys.argv[1]

        if command == "clear":
            clear_cache()
        elif command == "list":
            list_cached_users()
        elif command == "test" and len(sys.argv) >= 4:
            user_id = sys.argv[2]
            app_key = sys.argv[3]
            app_secret = sys.argv[4] if len(sys.argv) > 4 else None

            if not app_secret:
                app_secret = input("Enter App Secret: ")

            name = get_user_display_name(user_id, app_key, app_secret)
            print(f"User {user_id}: {name or 'Not found'}")
    else:
        print("Usage:")
        print("  python3 dingtalk_user_cache.py clear     - Clear user cache")
        print("  python3 dingtalk_user_cache.py list      - List cached users")
        print("  python3 dingtalk_user_cache.py test <user_id> <app_key> [app_secret]")
        print("                                            - Test fetching user info")
