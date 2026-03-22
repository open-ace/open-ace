/**
 * E2E Test Helpers
 */

import { Page } from '@playwright/test';

/**
 * Login helper function
 */
export async function login(page: Page, username = 'admin', password = 'admin123') {
  await page.goto('/login');
  await page.waitForLoadState('networkidle');

  // Fill login form
  await page.locator('#username').fill(username);
  await page.locator('#password').fill(password);
  
  // Submit form
  await page.locator('button[type="submit"]').click();

  // Wait for redirect to dashboard or messages
  try {
    await page.waitForURL(/\/(dashboard|messages|\/$)/, { timeout: 15000 });
  } catch {
    // If redirect fails, try navigating to dashboard
    await page.goto('/');
  }

  // Wait for page to be ready
  await page.waitForLoadState('networkidle');
}

/**
 * Wait for app to be ready
 */
export async function waitForApp(page: Page) {
  await page.waitForLoadState('networkidle');
  // Wait for main content to be visible
  await page.locator('main').waitFor({ state: 'visible', timeout: 10000 }).catch(() => {});
}