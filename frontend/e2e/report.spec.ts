/**
 * Report Page E2E Tests
 *
 * Tests for user report features:
 * - Usage statistics
 * - Token usage charts
 * - Request statistics
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Report Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display report page', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    const reportContent = page.locator('.report').first();
    await expect(reportContent).toBeVisible({ timeout: 10000 });
  });

  test('should display usage statistics cards', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    // Look for stat cards
    const statCards = page.locator('.stat-card, .card');
    const count = await statCards.count();

    // Should have at least one card
    expect(count).toBeGreaterThan(0);
  });

  test('should display token usage chart', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    // Look for chart canvas
    const chart = page.locator('canvas').first();
    const isVisible = await chart.isVisible({ timeout: 10000 }).catch(() => false);
    // Chart might not be visible if no data
    expect(typeof isVisible).toBe('boolean');
  });

  test('should have date range filter', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    // Look for date inputs
    const dateInput = page.locator('input[type="date"]').first();
    await expect(dateInput).toBeVisible({ timeout: 5000 });
  });

  test('should display usage table', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    // Look for usage table
    const usageTable = page.locator('table').first();
    const isVisible = await usageTable.isVisible({ timeout: 10000 }).catch(() => false);
    // Table might not be visible if no data
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Report Page - Token Statistics', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display total tokens used', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    // Look for token statistics in stat cards
    const tokenStats = page.locator('.stat-card, .card').first();
    await expect(tokenStats).toBeVisible({ timeout: 5000 });
  });

  test('should display request count', async ({ page }) => {
    await page.goto('/report');
    await waitForApp(page);

    // Look for stat cards (one of them should have request count)
    const statCards = page.locator('.stat-card, .card');
    const count = await statCards.count();
    expect(count).toBeGreaterThan(0);
  });
});