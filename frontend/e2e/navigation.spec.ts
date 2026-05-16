/**
 * Navigation E2E Tests
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp, ensureSidebarVisible, getSidebarLocator } from './helpers';

test.describe('Navigation', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display sidebar navigation', async ({ page }) => {
    await waitForApp(page);
    await ensureSidebarVisible(page);
    const sidebar = getSidebarLocator(page);
    await expect(sidebar).toBeVisible({ timeout: 10000 });
  });

  test('should navigate to messages page', async ({ page }) => {
    await waitForApp(page);
    await ensureSidebarVisible(page);

    // Find and click messages link
    const sidebar = getSidebarLocator(page);
    const messagesLink = sidebar.locator('.nav-item .nav-link, .nav-item-link').filter({ hasText: /messages|消息/i }).first();

    if (await messagesLink.isVisible()) {
      await messagesLink.click();
      await page.waitForLoadState('networkidle');

      // Verify URL changed
      expect(page.url()).toMatch(/messages/);
    }
  });

  test('should navigate to analysis page', async ({ page }) => {
    await waitForApp(page);
    await ensureSidebarVisible(page);

    // Find and click analysis link
    const sidebar = getSidebarLocator(page);
    const analysisLink = sidebar.locator('.nav-item .nav-link, .nav-item-link').filter({ hasText: /trend|趋势|analysis/i }).first();

    if (await analysisLink.isVisible()) {
      await analysisLink.click();
      await page.waitForLoadState('networkidle');

      // Verify URL changed
      expect(page.url()).toMatch(/analysis|trend/);
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
      const sidebar = getSidebarLocator(page);
      await expect(sidebar).toBeVisible();
    }
  });

  test('should display hamburger button on mobile in manage mode', async ({ page }) => {
    // ManageLayout renders <Header /> (non-compact) which includes hamburger-btn
    await page.goto('/manage/dashboard');
    await waitForApp(page);
    await page.setViewportSize({ width: 375, height: 667 });

    // Hamburger button should be visible on mobile
    const hamburger = page.locator('.hamburger-btn');
    await expect(hamburger).toBeVisible();
  });
});
