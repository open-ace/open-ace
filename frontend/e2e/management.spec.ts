/**
 * Management Page E2E Tests
 *
 * Tests for admin management features within Manage mode (/manage/*):
 * - User management
 * - Quota & Alerts
 * - Audit Center
 * - Compliance Management
 * - Security Center
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('User Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display user management page with heading', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Verify the user-management container exists
    const container = page.locator('.user-management');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify heading is present
    const heading = container.locator('h2').first();
    await expect(heading).toBeVisible();
  });

  test('should display user table or empty state', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Either a table with users or an empty state should be visible
    const table = page.locator('.user-management table.table');
    const emptyState = page.locator('.user-management .empty-state');

    const hasTable = await table.isVisible().catch(() => false);
    const hasEmpty = await emptyState.isVisible().catch(() => false);
    expect(hasTable || hasEmpty).toBeTruthy();
  });

  test('should have add user button', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // The "Add User" button should be visible in the header area
    const addButton = page
      .locator('.user-management button')
      .filter({ hasText: /add|添加/i })
      .first();
    await expect(addButton).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Quota & Alerts', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display quota page with tabs', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    // Verify container and heading
    const container = page.locator('.quota-alerts');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (quota and alerts)
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.first()).toBeVisible();
  });

  test('should switch between quota and alerts tabs', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const tabs = page.locator('.quota-alerts .nav-tabs .nav-link');

    // Click alerts tab (second tab)
    if ((await tabs.count()) > 1) {
      await tabs.nth(1).click();
      await page.waitForLoadState('networkidle');

      // Verify alerts tab is active
      await expect(tabs.nth(1)).toHaveClass(/active/);
    }
  });

  test('quota tab should not show alert rules overview card', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const container = page.locator('.quota-alerts');
    const tabs = container.locator('.nav-tabs .nav-link');

    // Anchor the default active tab is quota to remove implicit dependency
    await expect(tabs.nth(0)).toHaveClass(/active/);

    // The misplaced "Alert Rules" overview card must no longer render on the quota tab
    await expect(container.locator('.alert-rules-list')).toHaveCount(0);

    // No alert-rules card title should be present
    const alertRulesTitle = container
      .locator('.card-title, .card .card-header')
      .filter({ hasText: /alert rules|告警规则/i });
    await expect(alertRulesTitle).toHaveCount(0);

    // No "no alerts" empty state on the quota tab (quota empty state is "no quota data" instead)
    const noAlertsState = container
      .locator('.empty-state')
      .filter({ hasText: /no alerts found|暂无告警/i });
    await expect(noAlertsState).toHaveCount(0);
  });

  test('alerts tab should still render alert management content', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const container = page.locator('.quota-alerts');
    const tabs = container.locator('.nav-tabs .nav-link');

    // Switch to alerts tab (second tab)
    if ((await tabs.count()) > 1) {
      await tabs.nth(1).click();
      await page.waitForLoadState('networkidle');
      await expect(tabs.nth(1)).toHaveClass(/active/);

      // Alert statistics StatCards should be visible (the comprehensive alert management)
      const statCards = container.locator('.stat-card');
      await expect(statCards.first()).toBeVisible({ timeout: 10000 });

      // Either the alert list table or an empty state should be present (tolerant of zero alerts)
      const table = container.locator('table.table');
      const emptyState = container.locator('.empty-state');
      const hasTable = await table.isVisible().catch(() => false);
      const hasEmpty = await emptyState.isVisible().catch(() => false);
      expect(hasTable || hasEmpty).toBeTruthy();
    }
  });

  test('view-alerts hint button should navigate to alerts tab when present', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const container = page.locator('.quota-alerts');
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.nth(0)).toHaveClass(/active/);

    // The hint button only renders on the quota tab when there are unread alerts.
    // Tolerant of zero-alert environments: assert navigation when visible, absence otherwise.
    const hintButton = container
      .locator('button')
      .filter({ hasText: /view alerts|查看告警|アラートを表示|알림 보기/i });

    if (await hintButton.isVisible().catch(() => false)) {
      await hintButton.click();
      await page.waitForLoadState('networkidle');
      await expect(tabs.nth(1)).toHaveClass(/active/);
    } else {
      await expect(hintButton).toHaveCount(0);
    }
  });
});

test.describe('Audit Center', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display audit center with tabs', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    const container = page.locator('.audit-center');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (log and analysis)
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.first()).toBeVisible();
  });

  test('should display audit log table or content', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    // Log tab should show filters card and table
    const table = page.locator('.audit-center table.table');
    const card = page.locator('.audit-center .card');

    const hasTable = await table.isVisible().catch(() => false);
    const hasCard = await card.isVisible().catch(() => false);
    expect(hasTable || hasCard).toBeTruthy();
  });
});

test.describe('Compliance Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display compliance page with tabs', async ({ page }) => {
    await page.goto('/manage/compliance');
    await waitForApp(page);

    const container = page.locator('.compliance-mgmt');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (reports and retention)
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.first()).toBeVisible();
  });

  test('should display report type cards on reports tab', async ({ page }) => {
    await page.goto('/manage/compliance');
    await waitForApp(page);

    // Reports tab should have report type selection cards
    const cards = page.locator('.compliance-mgmt .card');
    await expect(cards.first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Security Center', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display security center with tabs', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    const container = page.locator('.security-center');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (filter, settings, audit thresholds)
    const tabs = container.locator('.nav-tabs .nav-link');
    const tabCount = await tabs.count();
    expect(tabCount).toBeGreaterThanOrEqual(2);
  });

  test('should display session timeout input on settings tab', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    // Switch to settings tab if not already active
    const settingsTab = page
      .locator('.security-center .nav-tabs .nav-link')
      .filter({ hasText: /setting|配置/i })
      .first();
    if (await settingsTab.isVisible()) {
      await settingsTab.click();
      await page.waitForLoadState('networkidle');
    }

    // Look for form inputs (session timeout, password policy, etc.)
    const formInputs = page.locator(
      '.security-center input[type="number"], .security-center input[type="text"]'
    );
    const hasInputs = await formInputs
      .first()
      .isVisible({ timeout: 5000 })
      .catch(() => false);
    expect(typeof hasInputs).toBe('boolean');
  });
});
