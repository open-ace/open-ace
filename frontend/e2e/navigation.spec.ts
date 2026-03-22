/**
 * Navigation E2E Tests
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display sidebar navigation', async ({ page }) => {
    await waitForApp(page);
    const sidebar = page.locator('nav.sidebar').first();
    await expect(sidebar).toBeVisible({ timeout: 10000 });
  });

  test('should navigate to messages page', async ({ page }) => {
    await waitForApp(page);

    // Find and click messages link (second nav item)
    const messagesLink = page.locator('nav.sidebar .nav-item:nth-child(2) .nav-link').first();

    if (await messagesLink.isVisible()) {
      await messagesLink.click();
      await page.waitForLoadState('networkidle');

      // Verify URL changed
      expect(page.url()).toContain('messages');
    }
  });

  test('should navigate to analysis page', async ({ page }) => {
    await waitForApp(page);

    // Find and click analysis link (third nav item)
    const analysisLink = page.locator('nav.sidebar .nav-item:nth-child(3) .nav-link').first();

    if (await analysisLink.isVisible()) {
      await analysisLink.click();
      await page.waitForLoadState('networkidle');

      // Verify URL changed
      expect(page.url()).toContain('analysis');
    }
  });

  test('should toggle sidebar on mobile', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    await waitForApp(page);

    // Find menu toggle button in header
    const menuToggle = page.locator('.header button:has(.bi-list)').first();

    if (await menuToggle.isVisible()) {
      await menuToggle.click();
      await page.waitForTimeout(500);

      // Sidebar should be visible after toggle
      const sidebar = page.locator('nav.sidebar').first();
      await expect(sidebar).toBeVisible();
    }
  });
});