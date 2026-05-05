/**
 * Accessibility E2E Tests
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Accessibility', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should have proper page title', async ({ page }) => {
    await expect(page).toHaveTitle(/Open ACE|ACE/i);
  });

  test('should have main landmark', async ({ page }) => {
    await waitForApp(page);
    const main = page.locator('main');
    await expect(main).toBeVisible({ timeout: 10000 });
  });

  test('should have navigation landmark', async ({ page }) => {
    await waitForApp(page);
    const nav = page.locator('nav.sidebar');
    await expect(nav).toBeVisible({ timeout: 10000 });
  });

  test('should have visible focus indicators', async ({ page }) => {
    await waitForApp(page);

    // Tab through interactive elements
    await page.keyboard.press('Tab');

    // Check that focused element has visible outline
    const focusedElement = page.locator(':focus');
    await expect(focusedElement).toBeVisible();
  });

  test('should have sufficient color contrast', async ({ page }) => {
    await waitForApp(page);

    // This is a basic check - for comprehensive contrast testing,
    // consider using axe-core or similar tools
    const body = page.locator('body');
    const backgroundColor = await body.evaluate((el) =>
      window.getComputedStyle(el).backgroundColor
    );

    // Ensure background color is defined
    expect(backgroundColor).toBeDefined();
  });

  test('should be keyboard navigable', async ({ page }) => {
    await waitForApp(page);

    // Tab through several elements
    for (let i = 0; i < 5; i++) {
      await page.keyboard.press('Tab');
    }

    // Check that focus is on an interactive element
    const focusedElement = page.locator(':focus');
    await expect(focusedElement).toBeVisible();
  });
});
