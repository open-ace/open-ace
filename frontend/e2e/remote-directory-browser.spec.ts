/**
 * Remote Directory Browser E2E Tests
 *
 * Issue #584: Tests for directory browser functionality in remote/terminal workspace
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Remote Directory Browser', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('new session modal opens and displays workspace type buttons', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

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

  test('selecting remote workspace shows machine list and project path', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();
    await expect(remoteBtn).toHaveAttribute('class', /btn-primary/);

    // Machine area should show (machine list or "No available machines")
    const machineArea = page.locator('.modal').locator('text=Machine');
    await expect(machineArea.first()).toBeVisible();
  });

  test('selecting terminal workspace shows machine list', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();
    await expect(terminalBtn).toHaveAttribute('class', /btn-primary/);

    const machineArea = page.locator('.modal').locator('text=Machine');
    await expect(machineArea.first()).toBeVisible();
  });

  test('browse button appears when machine is selected for remote workspace', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();

    // Check if machines are available
    const noMachines = page.locator('.modal').getByText(/No available machines|没有可用的机器/);
    if (await noMachines.isVisible()) {
      test.skip(true, 'No machines registered - requires setup');
    }

    // Browse button should appear when a machine is selected (auto-select or manual)
    const browseBtn = page.locator('.modal button').filter({ hasText: /浏览|Browse/ });
    await expect(browseBtn.first()).toBeVisible();
  });

  test('project path input appears for terminal workspace when machine selected', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const terminalBtn = page.locator('button').filter({ hasText: /终端|Terminal/ });
    await terminalBtn.click();

    const noMachines = page.locator('.modal').getByText(/No available machines|没有可用的机器/);
    if (await noMachines.isVisible()) {
      test.skip(true, 'No machines registered - requires setup');
    }

    // Working directory label or project path should appear
    const workDirLabel = page.locator('.modal').locator('text=Project Path|Working Directory|工作目录|项目路径');
    await expect(workDirLabel.first()).toBeVisible();
  });

  test('create button state reflects machine selection', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

    const newSessionBtn = page.getByTestId('new-session-btn');
    await expect(newSessionBtn).toBeVisible();
    await newSessionBtn.click();
    await expect(page.locator('.modal')).toBeVisible();

    const remoteBtn = page.locator('button').filter({ hasText: /远程|Remote/ });
    await remoteBtn.click();
    await page.waitForTimeout(500);

    const createBtn = page.locator('.modal button').filter({ hasText: /创建|Create/ });
    await expect(createBtn.first()).toBeVisible();

    // If machines are available, one is auto-selected so Create is enabled
    const noMachines = page.locator('.modal').getByText(/No available machines|没有可用的机器/);
    if (await noMachines.isVisible()) {
      await expect(createBtn.first()).toBeDisabled();
    } else {
      await expect(createBtn.first()).toBeEnabled();
    }
  });

  test('modal can be closed with cancel button', async ({ page }) => {
    await page.goto('/work');
    await waitForApp(page);

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
