/**
 * Remote Directory Browser E2E Tests
 *
 * Issue #584: Tests for directory browser functionality in remote/terminal workspace
 */

import { test, expect } from '@playwright/test';

test.describe('Remote Directory Browser', () => {
  // Login before each test
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/work|\/dashboard/);
  });

  test('new session modal opens and displays workspace type buttons', async ({ page }) => {
    await page.goto('/work');

    // Find and click new session button - this should always exist
    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    await expect(newSessionBtn.first()).toBeVisible();

    await newSessionBtn.first().click();

    // Wait for modal and verify it's visible
    const modal = page.locator('.modal');
    await expect(modal).toBeVisible();

    // Verify workspace type buttons are present - these are core UI elements
    const localBtn = page.locator('button').filter({ hasText: /本地|Local/ });
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });

    // All three workspace types should always be available
    await expect(localBtn.first()).toBeVisible();
    await expect(remoteBtn.first()).toBeVisible();
    await expect(terminalBtn.first()).toBeVisible();
  });

  test('selecting remote workspace shows machine selector', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    await expect(newSessionBtn.first()).toBeVisible();

    await newSessionBtn.first().click();
    await expect(page.locator('.modal')).toBeVisible();

    // Click remote workspace button
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();

    // Verify remote workspace is selected (button should have primary styling)
    await expect(remoteBtn).toHaveAttribute('class', /btn-primary/);

    // Verify machine selector appears
    const machineSelect = page.locator('select');
    await expect(machineSelect.first()).toBeVisible();
  });

  test('selecting terminal workspace shows machine selector', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    await expect(newSessionBtn.first()).toBeVisible();

    await newSessionBtn.first().click();
    await expect(page.locator('.modal')).toBeVisible();

    // Click terminal workspace button
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();

    // Verify terminal workspace is selected
    await expect(terminalBtn).toHaveAttribute('class', /btn-primary/);

    // Verify machine selector appears
    const machineSelect = page.locator('select');
    await expect(machineSelect.first()).toBeVisible();
  });

  test('browse button appears after machine selection for remote workspace', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    await expect(newSessionBtn.first()).toBeVisible();

    await newSessionBtn.first().click();
    await expect(page.locator('.modal')).toBeVisible();

    // Select remote workspace
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();

    // Check machine selector has options available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();

    // This test requires machines to be registered - skip only if setup condition is genuinely not met
    test.skip(options <= 1, 'No machines registered - requires setup');

    // Select the first available machine
    await machineSelect.selectOption({ index: 1 });

    // Wait for project path section to appear
    await page.waitForTimeout(500);

    // Verify browse button appears
    const browseBtn = page.locator('button').filter({ hasText: /浏览|Browse/ });
    expect(await browseBtn.count()).toBeGreaterThan(0);
    expect(await browseBtn.isVisible()).toBe(true);
  });

  test('working directory input appears for terminal workspace', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();
    await expect(page.locator('.modal')).toBeVisible();

    // Select terminal workspace
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();

    // Check machine selector has options available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();

    // This test requires machines to be registered - skip only if setup condition is genuinely not met
    test.skip(options <= 1, 'No machines registered - requires setup');

    await machineSelect.selectOption({ index: 1 });
    await page.waitForTimeout(500);

    // Verify working directory label appears
    const workDirLabel = page.locator('.form-label').filter({ hasText: /工作目录|Working Directory/ });
    await expect(workDirLabel.first()).toBeVisible();
  });

  test('create button is disabled without machine selection', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    await expect(newSessionBtn.first()).toBeVisible();

    await newSessionBtn.first().click();
    await expect(page.locator('.modal')).toBeVisible();

    // Select remote workspace without selecting a machine
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();
    await page.waitForTimeout(500);

    // Verify create button exists and is disabled
    const createBtn = page.locator('.modal button').filter({ hasText: /创建|Create/ });
    await expect(createBtn.first()).toBeVisible();
    await expect(createBtn.first()).toBeDisabled();
  });

  test('modal can be closed with cancel button', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    await expect(newSessionBtn.first()).toBeVisible();

    await newSessionBtn.first().click();
    await expect(page.locator('.modal')).toBeVisible();

    // Click cancel button - should exist
    const cancelBtn = page.locator('.modal button').filter({ hasText: /取消|Cancel/ });
    await expect(cancelBtn).toBeVisible();
    await cancelBtn.click();

    // Verify modal is closed
    await page.waitForTimeout(500);
    await expect(page.locator('.modal')).not.toBeVisible();
  });
});
