/**
 * Theme E2E Tests
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Theme Switching', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should have default light theme', async ({ page }) => {
    await waitForApp(page);

    // Check for light theme (default)
    const html = page.locator('html');
    const theme = await html.getAttribute('data-theme');

    // Default should be light or no theme attribute
    expect(theme === 'light' || theme === null || theme === '').toBeTruthy();
  });

  test('should toggle to dark theme', async ({ page }) => {
    await waitForApp(page);

    // Find theme toggle button in header
    const themeToggle = page.locator('.header button:has(.bi-moon), .header button:has(.bi-sun)').first();

    if (await themeToggle.isVisible()) {
      await themeToggle.click();
      await page.waitForTimeout(500);

      // Check for dark theme
      const html = page.locator('html');
      const theme = await html.getAttribute('data-theme');
      expect(theme).toBe('dark');
    }
  });

  test('should persist theme preference', async ({ page }) => {
    await waitForApp(page);

    // Find theme toggle button in header
    const themeToggle = page.locator('.header button:has(.bi-moon), .header button:has(.bi-sun)').first();

    if (await themeToggle.isVisible()) {
      // Toggle to dark
      await themeToggle.click();
      await page.waitForTimeout(500);

      // Reload page
      await page.reload();
      await waitForApp(page);

      // Theme should still be dark
      const html = page.locator('html');
      const theme = await html.getAttribute('data-theme');
      expect(theme).toBe('dark');
    }
  });
});
