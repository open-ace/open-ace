/**
 * Navigation E2E Tests
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp, ensureSidebarVisible } from './helpers';

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display sidebar navigation', async ({ page }) => {
    await waitForApp(page);
    await ensureSidebarVisible(page);
    const sidebar = page.locator('nav.sidebar').first();
    await expect(sidebar).toBeVisible({ timeout: 10000 });
  });

  test('should navigate to messages page', async ({ page }) => {
    await waitForApp(page);
    await ensureSidebarVisible(page);

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
    await ensureSidebarVisible(page);

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

    // Click hamburger button to open sidebar
    const menuToggle = page.locator('.hamburger-btn');

    if (await menuToggle.isVisible()) {
      await menuToggle.click();

      // Sidebar should be visible after toggle
      const sidebar = page.locator('nav.sidebar').first();
      await expect(sidebar).toBeVisible();
    }
  });

  test('should open and close sidebar via hamburger and overlay on mobile', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await waitForApp(page);

    const hamburger = page.locator('.hamburger-btn');
    const sidebar = page.locator('nav.sidebar').first();
    const overlay = page.locator('.sidebar-overlay');

    // Open via hamburger
    await expect(hamburger).toBeVisible();
    await hamburger.click();
    await expect(sidebar).toBeVisible();
    await expect(overlay).toBeVisible();

    // Close via overlay click
    await overlay.click();
    await expect(sidebar).not.toBeVisible();

    // Open again
    await hamburger.click();
    await expect(sidebar).toBeVisible();

    // Close via nav item click
    const navLink = sidebar.locator('.nav-item .nav-link').first();
    await navLink.click();
    await expect(sidebar).not.toBeVisible();
  });
});
