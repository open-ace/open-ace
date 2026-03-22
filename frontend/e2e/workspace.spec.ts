/**
 * Workspace Page E2E Tests
 *
 * Tests for workspace features:
 * - Workspace iframe loading
 * - Workspace configuration
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Workspace Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display workspace page', async ({ page }) => {
    await page.goto('/workspace');
    await waitForApp(page);

    const workspaceContent = page.locator('.workspace').first();
    await expect(workspaceContent).toBeVisible({ timeout: 10000 });
  });

  test('should display either iframe or not configured message', async ({ page }) => {
    await page.goto('/workspace');
    await waitForApp(page);

    // Look for either iframe or not configured message
    const iframe = page.locator('iframe');
    const notConfigured = page.locator('.workspace h4, .workspace .text-muted');

    // Either iframe or not configured message should be visible
    const iframeVisible = await iframe.isVisible().catch(() => false);
    const notConfiguredVisible = await notConfigured.first().isVisible().catch(() => false);

    expect(iframeVisible || notConfiguredVisible).toBeTruthy();
  });

  test('should have correct iframe src if configured', async ({ page }) => {
    await page.goto('/workspace');
    await waitForApp(page);

    const iframe = page.locator('iframe').first();

    if (await iframe.isVisible()) {
      const src = await iframe.getAttribute('src');
      expect(src).not.toBeNull();
      expect(src).not.toBe('');
    }
  });

  test('should have full height iframe', async ({ page }) => {
    await page.goto('/workspace');
    await waitForApp(page);

    const iframe = page.locator('iframe').first();

    if (await iframe.isVisible()) {
      // Check iframe has height style
      const height = await iframe.evaluate((el) => {
        return el.style.height || el.getAttribute('height');
      });

      // Height should be set (either as style or attribute)
      expect(height).toBeTruthy();
    }
  });
});

test.describe('Workspace Page - Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should navigate to workspace from sidebar', async ({ page }) => {
    await waitForApp(page);

    // Find workspace link in sidebar (8th nav item)
    const workspaceLink = page.locator('nav.sidebar .nav-item:nth-child(8) .nav-link').first();

    if (await workspaceLink.isVisible()) {
      await workspaceLink.click();
      await page.waitForLoadState('networkidle');

      // Verify URL changed to workspace
      expect(page.url()).toContain('workspace');
    }
  });
});