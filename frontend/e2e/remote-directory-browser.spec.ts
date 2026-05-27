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

    // Find and click new session button
    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();

    // Wait for modal and verify it's visible
    const modal = page.locator('.modal');
    await modal.waitFor({ state: 'visible', timeout: 5000 });
    expect(await modal.isVisible()).toBe(true);

    // Verify workspace type buttons are present
    const localBtn = page.locator('button').filter({ hasText: /本地|Local/ });
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });

    // All three workspace types should be available
    expect(await localBtn.count()).toBeGreaterThan(0);
    expect(await remoteBtn.count()).toBeGreaterThan(0);
    expect(await terminalBtn.count()).toBeGreaterThan(0);
  });

  test('selecting remote workspace shows machine selector', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();
    await page.locator('.modal').waitFor({ state: 'visible', timeout: 5000 });

    // Click remote workspace button
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();

    // Verify remote workspace is selected (button should have primary styling)
    expect(await remoteBtn.getAttribute('class')).toContain('btn-primary');

    // Verify machine selector appears
    const machineSelect = page.locator('select');
    const selectCount = await machineSelect.count();
    expect(selectCount).toBeGreaterThan(0);
  });

  test('selecting terminal workspace shows machine selector', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();
    await page.locator('.modal').waitFor({ state: 'visible', timeout: 5000 });

    // Click terminal workspace button
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();

    // Verify terminal workspace is selected
    expect(await terminalBtn.getAttribute('class')).toContain('btn-primary');

    // Verify machine selector appears
    const machineSelect = page.locator('select');
    expect(await machineSelect.count()).toBeGreaterThan(0);
  });

  test('browse button appears after machine selection for remote workspace', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();
    await page.locator('.modal').waitFor({ state: 'visible', timeout: 5000 });

    // Select remote workspace
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();

    // Check machine selector and select a machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();

    if (options <= 1) {
      test.skip(true, 'No machines available for selection');
      return;
    }

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
    await page.locator('.modal').waitFor({ state: 'visible', timeout: 5000 });

    // Select terminal workspace
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();

    // Check machine selector and select a machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();

    if (options <= 1) {
      test.skip(true, 'No machines available for selection');
      return;
    }

    await machineSelect.selectOption({ index: 1 });
    await page.waitForTimeout(500);

    // Verify working directory label appears
    const workDirLabel = page.locator('.form-label').filter({ hasText: /工作目录|Working Directory/ });
    expect(await workDirLabel.count()).toBeGreaterThan(0);
  });

  test('create button is disabled without machine selection', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();
    await page.locator('.modal').waitFor({ state: 'visible', timeout: 5000 });

    // Select remote workspace without selecting a machine
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();
    await page.waitForTimeout(500);

    // Verify create button is disabled
    const createBtn = page.locator('.modal button').filter({ hasText: /创建|Create/ });
    if (await createBtn.count() > 0) {
      expect(await createBtn.first().isDisabled()).toBe(true);
    }
  });

  test('modal can be closed with cancel button', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.locator('button').filter({ hasText: /新建会话|New Session/ });
    const btnCount = await newSessionBtn.count();

    if (btnCount === 0) {
      test.skip(true, 'New session button not found - no machines registered');
      return;
    }

    await newSessionBtn.first().click();
    await page.locator('.modal').waitFor({ state: 'visible', timeout: 5000 });

    // Click cancel button
    const cancelBtn = page.locator('.modal button').filter({ hasText: /取消|Cancel/ });
    await cancelBtn.click();

    // Verify modal is closed
    await page.waitForTimeout(500);
    expect(await page.locator('.modal').count()).toBe(0);
  });
});
