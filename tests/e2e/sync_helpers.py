"""Synchronous Playwright helpers shared by standalone E2E scripts."""

from __future__ import annotations

from datetime import datetime, timedelta
from urllib.parse import urlparse

import requests
from playwright.sync_api import Page


def expected_default_date_range(days: int = 30, *, now: datetime | None = None) -> tuple[str, str]:
    """Expected value of the frontend's default range for ``days`` days.

    The product contract (#3276) is exactly ``days`` calendar days through
    today, inclusive on both ends, as local YYYY-MM-DD: start = today -
    (days - 1), end = today (backend queries are date >= start AND
    date <= end, so the window must contain exactly N days). Computed with
    pure-Python local calendar arithmetic — the same local-calendar
    semantics as the frontend's toLocalDateString, never UTC/toISOString.

    ``now`` is injectable so unit tests can pin fixed dates (cross-month,
    cross-year, leap-February anchors); e2e callers use the runner's local
    clock, which matches the page under test (CI runners and their
    headless browsers share the UTC local zone).
    """
    reference = now or datetime.now()
    if days <= 0:
        # Mirrors the frontend guard: a non-positive window degenerates to
        # today..today rather than a silently inverted/future range.
        today = reference.strftime("%Y-%m-%d")
        return today, today
    start = reference - timedelta(days=days - 1)
    return start.strftime("%Y-%m-%d"), reference.strftime("%Y-%m-%d")


def login_as(
    page: Page,
    base_url: str,
    username: str = "admin",
    password: str = "admin123",
    *,
    timeout: int = 10000,
) -> None:
    """Log in through the current React login form and wait for any app page."""

    page.goto(f"{base_url}/login", wait_until="domcontentloaded")
    user_input = page.locator(
        "#username, input[name='username'], input[autocomplete='username']"
    ).first
    pass_input = page.locator("#password, input[name='password'], input[type='password']").first

    user_input.wait_for(state="visible", timeout=timeout)
    user_input.fill(username)
    pass_input.fill(password)
    page.locator(
        "button[type='submit'], button:has-text('Login'), button:has-text('登录')"
    ).first.click()
    page.wait_for_url(lambda url: "/login" not in url, timeout=timeout)


def login_context_via_api(
    context,
    base_url: str,
    username: str = "admin",
    password: str = "admin123",
    *,
    timeout: int = 10,
) -> str:
    """Authenticate by API and inject the session cookie into a Playwright context."""

    response = requests.post(
        f"{base_url}/api/auth/login",
        json={"username": username, "password": password},
        timeout=timeout,
    )
    response.raise_for_status()
    token = response.cookies.get("session_token")
    if not token:
        raise AssertionError("No session_token cookie in login response")

    parsed = urlparse(base_url)
    context.add_cookies(
        [
            {
                "name": "session_token",
                "value": token,
                "domain": parsed.hostname or "localhost",
                "path": "/",
            }
        ]
    )
    return token
