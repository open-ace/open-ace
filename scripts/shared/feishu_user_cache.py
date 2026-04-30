#!/usr/bin/env python3
"""
Feishu User Cache Module

Caches Feishu user information to avoid frequent API calls.
Fetches user details from Feishu API when needed.
"""

import json
import time
from pathlib import Path
from typing import Dict, Optional

import requests

# Cache file location
CACHE_DIR = Path.home() / ".open-ace"
CACHE_FILE = CACHE_DIR / "feishu_users.json"
CACHE_TTL = 3600  # Cache TTL in seconds (1 hour)


def ensure_cache_dir():
    """Ensure cache directory exists."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)


def load_cache() -> Dict:
    """Load user cache from file."""
    ensure_cache_dir()
    if not CACHE_FILE.exists():
        return {"users": {}, "last_updated": 0}

    try:
        with open(CACHE_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {"users": {}, "last_updated": 0}


def save_cache(cache: Dict):
    """Save user cache to file."""
    ensure_cache_dir()
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def get_feishu_token(app_id: str, app_secret: str) -> Optional[str]:
    """Get Feishu API access token using tenant access token."""
    url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    payload = {"app_id": app_id, "app_secret": app_secret}

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


def get_user_info(user_id: str, app_id: str, app_secret: str) -> Optional[Dict]:
    """Get user info from Feishu API."""
    cache = load_cache()

    # Check cache first
    if user_id in cache["users"]:
        user_cache = cache["users"][user_id]
        if time.time() - user_cache.get("cached_at", 0) < CACHE_TTL:
            return user_cache.get("data")

    # Get access token
    token = get_feishu_token(app_id, app_secret)
    if not token:
        return None

    # Call Feishu user info API
    url = f"https://open.feishu.cn/open-apis/contact/v3/users/{user_id}"
    params = {"user_id_type": "open_id"}  # ou_ prefix indicates open_id
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()

        if data.get("code") == 0:
            user_data = data.get("data", {})
            user_info = user_data.get("user", {})

            # Debug: log available fields
            if not user_info.get("name") and not user_info.get("zh_name"):
                print(
                    f"Warning: User {user_id} has no name field. Available fields: {list(user_info.keys())}"
                )

            # Cache the result (use 'user' nested structure)
            cache["users"][user_id] = {"data": user_info, "cached_at": time.time()}
            save_cache(cache)

            return user_info
        else:
            print(f"Failed to get user info for {user_id}: {data}")
            return None
    except Exception as e:
        print(f"Error getting user info: {e}")
        return None


def get_user_name(user_id: str, app_id: str, app_secret: str) -> Optional[str]:
    """Get user's display name from Feishu API."""
    if not user_id or not user_id.startswith("ou_"):
        return None

    user_info = get_user_info(user_id, app_id, app_secret)
    if user_info:
        # Try to get name in preferred order
        # 1. Custom name (nickname)
        # 2. Chinese name
        # 3. English name
        # 4. Full name

        # Check user_tag for name type
        name = user_info.get("name")  # Default name
        zh_name = user_info.get("zh_name")  # Chinese name
        en_name = user_info.get("en_name")  # English name
        nickname = user_info.get("nickname")  # Nickname

        # Prefer Chinese name, then nickname, then default name
        display_name = zh_name or nickname or name or en_name

        if display_name:
            return display_name

    return None


def get_user_name_from_cache(user_id: str) -> Optional[str]:
    """Get user name from local cache without API call."""
    cache = load_cache()
    if user_id in cache["users"]:
        user_cache = cache["users"][user_id]
        user_data = user_cache.get("data", {})

        # Check if cache is still valid (within TTL)
        if time.time() - user_cache.get("cached_at", 0) < CACHE_TTL:
            name = user_data.get("zh_name") or user_data.get("nickname") or user_data.get("name")
            return name

    return None


def clear_cache():
    """Clear user cache."""
    if CACHE_FILE.exists():
        CACHE_FILE.unlink()
    print("Feishu user cache cleared.")


def list_cached_users():
    """List all cached users."""
    cache = load_cache()
    print(f"Cached users ({len(cache['users'])}):")
    for user_id, user_cache in cache["users"].items():
        user_data = user_cache.get("data", {})
        name = user_data.get("zh_name") or user_data.get("name", "Unknown")
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
            # Test fetching a user
            user_id = sys.argv[2]
            app_id = sys.argv[3]
            app_secret = sys.argv[4] if len(sys.argv) > 4 else None

            if not app_secret:
                app_secret = input("Enter App Secret: ")

            name = get_user_name(user_id, app_id, app_secret)
            print(f"User {user_id}: {name or 'Not found'}")
    else:
        print("Usage:")
        print("  python3 feishu_user_cache.py clear     - Clear user cache")
        print("  python3 feishu_user_cache.py list      - List cached users")
        print("  python3 feishu_user_cache.py test <user_id> <app_id> [app_secret]")
        print("                                           - Test fetching user info")
