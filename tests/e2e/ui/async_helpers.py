"""Shared helpers for async Playwright UI E2E tests."""

from __future__ import annotations


async def login_as(page, base_url: str, username: str, password: str) -> None:
    """Log in and wait until the backend session is visible to the browser."""
    await page.goto(f"{base_url}/login")
    await page.wait_for_selector("#username", timeout=10000)
    await page.fill("#username", username)
    await page.fill("#password", password)
    await page.click('button[type="submit"]')
    await page.wait_for_function(
        """async () => {
            const response = await fetch('/api/auth/check', { credentials: 'include' });
            if (!response.ok) return false;
            const data = await response.json();
            return Boolean(data.authenticated && data.user && data.user.role);
        }""",
        timeout=15000,
    )
    await page.wait_for_timeout(750)


async def open_work_or_assert_unconfigured(page, base_url: str) -> bool:
    """Open /work and return True when the default env has no workspace URL."""
    await page.goto(f"{base_url}/work")
    await page.wait_for_load_state("domcontentloaded")
    await page.wait_for_selector("main, .work-layout", timeout=15000)
    config_unavailable = await page.evaluate(
        """async () => {
            const response = await fetch('/api/workspace/config', { credentials: 'include' });
            if (!response.ok) return false;
            const config = await response.json();
            return !config.enabled || !(config.url || config.web_url || config.workspace_url);
        }"""
    )
    if config_unavailable:
        return True
    unconfigured = page.get_by_text("Workspace not configured")
    if await unconfigured.count() > 0 and await unconfigured.first.is_visible():
        return True
    return False
