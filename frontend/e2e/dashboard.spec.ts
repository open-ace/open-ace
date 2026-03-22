/**
 * Dashboard E2E Tests
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Dashboard Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display dashboard title', async ({ page }) => {
    await waitForApp(page);
    // Check for dashboard heading
    const title = page.locator('.dashboard h2, .dashboard-header h2').first();
    await expect(title).toBeVisible({ timeout: 10000 });
  });

  test('should display statistics cards', async ({ page }) => {
    await waitForApp(page);
    // Check for usage cards
    const statCards = page.locator('.usage-card, .card');
    await expect(statCards.first()).toBeVisible({ timeout: 10000 });
  });

  test('should display today usage section', async ({ page }) => {
    await waitForApp(page);
    // Look for today's usage section heading
    const todaySection = page.locator('.dashboard-section h5').first();
    await expect(todaySection).toBeVisible({ timeout: 10000 });
  });

  test('should display trend chart', async ({ page }) => {
    await waitForApp(page);
    // Look for chart canvas
    const chart = page.locator('canvas');
    await expect(chart.first()).toBeVisible({ timeout: 15000 });
  });

  test('should refresh data when refresh button clicked', async ({ page }) => {
    await waitForApp(page);

    // Find refresh button
    const refreshBtn = page.locator('button:has-text("Refresh"), button:has-text("刷新"), .dashboard-controls button:has(.bi-arrow-clockwise)').first();

    if (await refreshBtn.isVisible()) {
      await refreshBtn.click();
      // Wait for loading state to complete
      await page.waitForLoadState('networkidle');
    }
  });

  test('should be responsive on mobile', async ({ page }) => {
    // Set mobile viewport
    await page.setViewportSize({ width: 375, height: 667 });
    await waitForApp(page);

    // Check that content is still visible
    const content = page.locator('.dashboard, main').first();
    await expect(content).toBeVisible();
  });
});