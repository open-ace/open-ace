/**
 * Remote Directory Browser E2E Tests
 *
 * Issue #584: Tests for directory browser functionality in remote/terminal workspace
 */

import { test, expect } from '@playwright/test';
import { login } from './helpers';

test.describe('Remote Directory Browser', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('new session modal opens and displays workspace type buttons', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();

    const modal = page.locator('.modal');
    await expect(modal).toBeVisible();

    const localBtn = page.locator('button').filter({ hasText: /本地|Local/ });
    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });

    await expect(localBtn.first()).toBeVisible();
    await expect(remoteBtn.first()).toBeVisible();
    await expect(terminalBtn.first()).toBeVisible();
  });

  test('selecting remote workspace shows machine selector', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();
    await expect(remoteBtn).toHaveAttribute('class', /btn-primary/);

    const machineSelect = page.locator('select');
    await expect(machineSelect.first()).toBeVisible();
  });

  test('selecting terminal workspace shows machine selector', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();
    await expect(terminalBtn).toHaveAttribute('class', /btn-primary/);

    const machineSelect = page.locator('select');
    await expect(machineSelect.first()).toBeVisible();
  });

  test('browse button appears after machine selection for remote workspace', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();

    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    test.skip(options <= 1, 'No machines registered - requires setup');

    await machineSelect.selectOption({ index: 1 });
    await page.waitForTimeout(500);

    const browseBtn = page.locator('button').filter({ hasText: /浏览|Browse/ });
    await expect(browseBtn.first()).toBeVisible();
  });

  test('working directory input appears for terminal workspace', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();

    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    test.skip(options <= 1, 'No machines registered - requires setup');

    await machineSelect.selectOption({ index: 1 });
    await page.waitForTimeout(500);

    const workDirLabel = page.locator('.form-label').filter({ hasText: /工作目录|Working Directory/ });
    await expect(workDirLabel.first()).toBeVisible();
  });

  test('create button is disabled without machine selection', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();
    await page.waitForTimeout(500);

    const createBtn = page.locator('.modal button').filter({ hasText: /创建|Create/ });
    await expect(createBtn.first()).toBeVisible();
    await expect(createBtn.first()).toBeDisabled();
  });

  test('modal can be closed with cancel button', async ({ page }) => {
    await page.goto('/work');

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const cancelBtn = page.locator('.modal button').filter({ hasText: /取消|Cancel/ });
    await expect(cancelBtn).toBeVisible();
    await cancelBtn.click();

    await page.waitForTimeout(500);
    await expect(page.locator('.modal')).not.toBeVisible();
  });
});
