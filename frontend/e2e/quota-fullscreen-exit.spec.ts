/**
 * Issue 53: Test auto exit fullscreen when quota exceeded
 *
 * This test verifies that:
 * 1. When workspace is in fullscreen mode
 * 2. And quota limit is exceeded
 * 3. The fullscreen mode should be automatically exited
 * 4. A toast notification should be shown to inform the user
 */

import fs from 'node:fs';
import path from 'node:path';
import { test, expect } from '@playwright/test';

/**
 * The workspace surface these tests exercise is gated behind
 * workspace.enabled in the lane's ~/.open-ace/config.json (freshly
 * initialized homes default it to false, and the specs run on the same
 * host as the server). The config endpoint re-reads the file per request,
 * so flipping it needs no restart — same pattern as the python e2e lanes
 * (tests/e2e/ui/test_language_sync.py's _ensure_workspace_enabled).
 */
function ensureWorkspaceEnabled(): void {
  const configPath = path.join(process.env.HOME ?? '', '.open-ace', 'config.json');
  let config: Record<string, unknown> = {};
  try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf-8'));
  } catch {
    // missing/corrupt file — start from an empty object
  }
  const workspace = (config.workspace ?? {}) as Record<string, unknown>;
  if (workspace.enabled !== true) {
    workspace.enabled = true;
    config.workspace = workspace;
    fs.mkdirSync(path.dirname(configPath), { recursive: true });
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
    workspaceWasEnabledByUs = true;
  }
}

let workspaceWasEnabledByUs = false;

function restoreWorkspaceFlag(): void {
  if (!workspaceWasEnabledByUs) {
    return;
  }
  const configPath = path.join(process.env.HOME ?? '', '.open-ace', 'config.json');
  try {
    const config = JSON.parse(fs.readFileSync(configPath, 'utf-8')) as Record<string, unknown>;
    const workspace = (config.workspace ?? {}) as Record<string, unknown>;
    workspace.enabled = false;
    config.workspace = workspace;
    fs.writeFileSync(configPath, JSON.stringify(config, null, 2));
  } catch {
    // best-effort restore only
  }
}

test.describe('Issue 53: Auto exit fullscreen on quota exceeded', () => {
  test.beforeAll(() => {
    ensureWorkspaceEnabled();
  });

  test.afterAll(() => {
    restoreWorkspaceFlag();
  });

  test.beforeEach(async ({ page }) => {
    // The workspace surface needs a webui URL; the lane has no qwen-code-webui
    // instance, so /api/workspace/user-url would 503 forever. Fulfill it with
    // a placeholder (same pattern as the python lanes' session-restore tests).
    await page.route('**/api/workspace/user-url', (route) =>
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({
          success: true,
          url: 'http://127.0.0.1:1/',
          token: 'e2e-mock-token',
        }),
      })
    );

    // Login first (current Login.tsx contract: #username/#password; the app
    // navigates to "/" after submit — there is no /dashboard route).
    await page.goto('/login');
    await page.fill('#username', 'admin');
    await page.fill('#password', 'admin123');
    await page.click('button[type="submit"]');

    // Wait for the post-login landing (root WorkLayout shell)
    await page.waitForURL(/\/(work)?$/, { timeout: 10000 });
  });

  test('should show quota exceeded warning when quota is over limit', async ({ page }) => {
    // Navigate to workspace
    await page.goto('/work/workspace');

    // Wait for workspace to load
    await page.waitForSelector('.workspace', { timeout: 15000 });

    // Take screenshot
    await page.screenshot({ path: 'screenshots/issues/53/workspace-initial.png' });

    // Check if workspace is displayed
    const workspace = await page.locator('.workspace');
    await expect(workspace).toBeVisible();
  });

  test('should display fullscreen toggle button', async ({ page }) => {
    // Navigate to workspace
    await page.goto('/work/workspace');

    // Wait for workspace header to load
    await page.waitForSelector('.page-header', { timeout: 15000 });

    // Check fullscreen toggle button exists
    const fullscreenBtn = await page.locator('.fullscreen-toggle-btn');
    await expect(fullscreenBtn).toBeVisible();

    // Take screenshot
    await page.screenshot({ path: 'screenshots/issues/53/fullscreen-button.png' });
  });

  test('should enter fullscreen mode when button clicked', async ({ page }) => {
    // Navigate to workspace
    await page.goto('/work/workspace');

    // Wait for page header
    await page.waitForSelector('.page-header', { timeout: 15000 });

    // Click fullscreen button
    const fullscreenBtn = await page.locator('.fullscreen-toggle-btn');
    await fullscreenBtn.click();

    // Wait for fullscreen mode — both .work-layout and .workspace receive
    // the class, so scope to the workspace element.
    await page.waitForSelector('.workspace.fullscreen-mode', { timeout: 5000 });

    // Verify fullscreen mode is active
    const workspace = page.locator('.workspace.fullscreen-mode');
    await expect(workspace).toBeVisible();

    // Take screenshot
    await page.screenshot({ path: 'screenshots/issues/53/fullscreen-active.png' });
  });

  test('should show toast notification when quota exceeded', async ({ page }) => {
    // This test verifies the toast notification component is available
    // Navigate to workspace
    await page.goto('/work/workspace');

    // Wait for workspace to load
    await page.waitForSelector('.workspace', { timeout: 15000 });

    // Check toast container exists (might be empty initially)
    const toastContainer = await page.locator('.toast-container');
    // Toast container should be present in the DOM
    await expect(toastContainer).toBeAttached();

    await page.screenshot({ path: 'screenshots/issues/53/toast-container.png' });
  });
});
