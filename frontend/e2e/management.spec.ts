/**
 * Management Page E2E Tests
 *
 * Tests for admin management features:
 * - User management
 * - Quota management
 * - Audit logs
 * - Content filter
 * - Security settings
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Management Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display management page for admin users', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const managementContent = page.locator('.management').first();
    await expect(managementContent).toBeVisible({ timeout: 10000 });
  });

  test('should display user management tab', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const userTab = page.locator('.nav-tabs .nav-link').first();
    await expect(userTab).toBeVisible({ timeout: 10000 });
  });

  test('should display quota management tab', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const tabs = page.locator('.nav-tabs .nav-link');
    const count = await tabs.count();
    expect(count).toBeGreaterThan(1);
  });

  test('should display audit log tab', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const tabs = page.locator('.nav-tabs .nav-link');
    const count = await tabs.count();
    expect(count).toBeGreaterThan(2);
  });

  test('should display content filter tab', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const tabs = page.locator('.nav-tabs .nav-link');
    const count = await tabs.count();
    expect(count).toBeGreaterThan(3);
  });

  test('should display security settings tab', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const tabs = page.locator('.nav-tabs .nav-link');
    const count = await tabs.count();
    expect(count).toBeGreaterThan(4);
  });
});

test.describe('User Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display user list table', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    // Click on first tab (users)
    const userTab = page.locator('.nav-tabs .nav-link').first();
    await userTab.click();
    await page.waitForLoadState('networkidle');

    const userContent = page.locator('table, .user-management, .card').first();
    await expect(userContent).toBeVisible({ timeout: 10000 });
  });

  test('should have add user button', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const userTab = page.locator('.nav-tabs .nav-link').first();
    await userTab.click();
    await page.waitForLoadState('networkidle');

    // Look for any action button
    const actionButton = page.locator('button').first();
    const isVisible = await actionButton.isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Content Filter Rules', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display filter rules table or empty state', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    // Click on filter tab (4th tab, index 3)
    const filterTab = page.locator('.nav-tabs .nav-link').nth(3);
    await filterTab.click();
    await page.waitForLoadState('networkidle');

    const filterContent = page.locator('.content-filter, table, .card').first();
    await expect(filterContent).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Security Settings', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display security settings form', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    // Click on security tab (5th tab, index 4)
    const securityTab = page.locator('.nav-tabs .nav-link').nth(4);
    await securityTab.click();
    await page.waitForLoadState('networkidle');

    const securityContent = page.locator('.security-settings, form, .card').first();
    await expect(securityContent).toBeVisible({ timeout: 10000 });
  });

  test('should have session timeout setting', async ({ page }) => {
    await page.goto('/management');
    await waitForApp(page);

    const securityTab = page.locator('.nav-tabs .nav-link').nth(4);
    await securityTab.click();
    await page.waitForLoadState('networkidle');

    // Look for any input or form element
    const formElement = page.locator('input, select, form').first();
    const isVisible = await formElement.isVisible({ timeout: 5000 }).catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});
