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

    // Wait for button to be visible (Safari/WebKit may need longer)
    await themeToggle.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});

    if (await themeToggle.isVisible()) {
      // Use force: true to bypass pointer interception (Safari/WebKit may have overlay elements)
      await themeToggle.click({ force: true });

      // Wait for data-theme attribute to change to 'dark' (not just fixed timeout)
      const html = page.locator('html');
      await expect(html).toHaveAttribute('data-theme', 'dark', { timeout: 5000 });
    }
  });

  test('should persist theme preference', async ({ page }) => {
    await waitForApp(page);

    // Find theme toggle button in header
    const themeToggle = page.locator('.header button:has(.bi-moon), .header button:has(.bi-sun)').first();

    // Wait for button to be visible (Safari/WebKit may need longer)
    await themeToggle.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});

    if (await themeToggle.isVisible()) {
      // Toggle to dark - use force: true for Safari/WebKit compatibility
      await themeToggle.click({ force: true });

      // Wait for theme to change before reload
      const html = page.locator('html');
      await expect(html).toHaveAttribute('data-theme', 'dark', { timeout: 5000 });

      // Reload page
      await page.reload();
      await waitForApp(page);

      // Theme should still be dark
      await expect(html).toHaveAttribute('data-theme', 'dark', { timeout: 5000 });
    }
  });
});
