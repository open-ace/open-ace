#!/usr/bin/env python3
"""
DingTalk User Cache Module

Caches DingTalk user information to avoid frequent API calls.
Fetches user details from DingTalk APIs when needed.
"""

from __future__ import annotations
import json
import logging
import time
from pathlib import Path
from typing import Optional, cast

import requests

logger = logging.getLogger(__name__)

CACHE_DIR = Path.home() / ".open-ace"
CACHE_FILE = CACHE_DIR / "dingtalk_users.json"
# DingTalk recycles userids after deletion; a 1h window let a recycled id be
# served stale for too long. 10 minutes bounds the stale-identity window while
# still amortizing API calls within a typical import session.
CACHE_TTL = 600  # 10 minutes

# DingTalk recycles userids after deletion, so a cached entry keyed only by
# userid can be silently re-bound to a different human. We additionally record a
# stable, DingTalk-wide identity (unionid/open_id) alongside each entry and
# detect mismatches on refresh to defend against that.
CACHE_IDENTITY_FIELDS = ("unionid", "open_id")


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


def get_dingtalk_access_token(app_key: str, app_secret: str) -> str | None:
    """Get DingTalk access token for an internal application."""
    url = "https://api.dingtalk.com/v1.0/oauth2/accessToken"
    payload = {"appKey": app_key, "appSecret": app_secret}

    try:
        response = requests.post(url, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()
        return cast(str | None, data.get("accessToken"))
    except Exception as e:
        logger.error("Error getting DingTalk access token: %s", e)
        return None


def _cache_identity_for(user_info: dict) -> str | None:
    """Return the stable identity field stored alongside a cached user (if any)."""
    for field in CACHE_IDENTITY_FIELDS:
        value = user_info.get(field)
        if value:
            return str(value)
    return None


def get_user_info(user_id: str, app_key: str, app_secret: str) -> dict | None:
    """Get user info from DingTalk API."""
    cache = load_cache()
    if user_id in cache["users"]:
        user_cache = cache["users"][user_id]
        cached_at = user_cache.get("cached_at", 0)
        cached_identity = user_cache.get("identity")
        if (
            time.time() - cached_at < CACHE_TTL
            # When we have a stable identity on record, keep using the cached entry.
            # If no identity was ever recorded (legacy entries), the TTL alone gates it.
            and (cached_identity is not None or not CACHE_IDENTITY_FIELDS)
        ):
            return cast(dict | None, user_cache.get("data"))

    token = get_dingtalk_access_token(app_key, app_secret)
    if not token:
        return None

    url = "https://oapi.dingtalk.com/topapi/v2/user/get"
    headers = {"x-acs-dingtalk-access-token": token}
    payload = {"userid": user_id}

    try:
        response = requests.post(url, headers=headers, json=payload, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("errcode") == 0:
            user_info = data.get("result", {})
            cache["users"][user_id] = {
                "data": user_info,
                "cached_at": time.time(),
                "identity": _cache_identity_for(user_info),
            }
            save_cache(cache)
            return cast(dict | None, user_info)
    except Exception:
        # Never log the raw request object: the access_token lives in the URL/headers.
        logger.exception("Error getting DingTalk user info for userid %s", user_id)

    return None


def get_user_display_name(user_id: str, app_key: str, app_secret: str) -> str | None:
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
    return cast(str | None, display_name)


def get_user_display_name_from_cache(user_id: str) -> str | None:
    """Get user display name from local cache without API call."""
    cache = load_cache()
    if user_id not in cache["users"]:
        return None

    user_cache = cache["users"][user_id]
    if time.time() - user_cache.get("cached_at", 0) >= CACHE_TTL:
        return None

    user_data = user_cache.get("data", {})
    return cast(
        str | None,
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
