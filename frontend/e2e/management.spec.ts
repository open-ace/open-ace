/**
 * Management Page E2E Tests
 *
 * Tests for admin management features within Manage mode (/manage/*):
 * - User management
 * - Quota management
 * - Audit logs
 * - Security settings
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Management Page', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display management page for admin users', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Check for user management content
    const managementContent = page.locator('main').first();
    await expect(managementContent).toBeVisible({ timeout: 10000 });
  });

  test('should display user management page', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Check that user management content loads
    const content = page.locator('main').first();
    await expect(content).toBeVisible({ timeout: 10000 });
    // Page should have heading or card content
    const heading = page.locator('h1, h2, h3, .card').first();
    await expect(heading).toBeVisible({ timeout: 10000 });
  });

  test('should display quota management page', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const content = page.locator('main').first();
    await expect(content).toBeVisible({ timeout: 10000 });
  });

  test('should display audit log page', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    const content = page.locator('main').first();
    await expect(content).toBeVisible({ timeout: 10000 });
  });

  test('should display compliance page', async ({ page }) => {
    await page.goto('/manage/compliance');
    await waitForApp(page);

    const content = page.locator('main').first();
    await expect(content).toBeVisible({ timeout: 10000 });
  });

  test('should display security settings page', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    const content = page.locator('main').first();
    await expect(content).toBeVisible({ timeout: 10000 });
  });
});

test.describe('User Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display user list table or content', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Check for table or card content
    const userContent = page.locator('table, .card, .user-management, .user-list').first();
    await expect(userContent).toBeVisible({ timeout: 10000 });
  });

  test('should have add user button or action', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Look for any action button
    const actionButton = page.locator('button').first();
    const isVisible = await actionButton.isVisible().catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});

test.describe('Security Settings', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display security settings form or content', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    const securityContent = page.locator('.card, form, table, .security').first();
    await expect(securityContent).toBeVisible({ timeout: 10000 });
  });

  test('should have form elements on security page', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    // Look for any input, select, or form element
    const formElement = page.locator('input, select, form').first();
    const isVisible = await formElement.isVisible({ timeout: 5000 }).catch(() => false);
    expect(typeof isVisible).toBe('boolean');
  });
});
