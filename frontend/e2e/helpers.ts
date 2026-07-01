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

  // Wait for redirect — admin goes to /manage/*, regular users to /work/*
  try {
    await page.waitForURL(/\/(manage|work)\//, { timeout: 15000 });
  } catch {
    // Fallback: wait for any URL change away from /login
    const navigated = await page.waitForURL(/^(?!.*\/login).+$/, { timeout: 5000 }).then(() => true).catch(() => false);
    if (!navigated) {
      console.warn(`Login redirect failed — still at ${page.url()}, navigating to /`);
      await page.goto('/');
    }
  }

  // Handle ForceChangePasswordModal if it appears (for users with must_change_password=true)
  const skipButton = page.locator('button:has-text("Skip"), button:has-text("跳过")');
  // Use waitFor with short timeout to avoid race condition (isVisible is non-waiting)
  const modalVisible = await skipButton.waitFor({ state: 'visible', timeout: 2000 }).then(() => true).catch(() => false);
  if (modalVisible) {
    // Click Skip button to dismiss the modal
    await skipButton.click();
    // Wait for modal to disappear
    await page.locator('[role="dialog"]').waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
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

/**
 * Get a locator for the sidebar — works in both Work and Manage modes.
 * Work mode uses `nav.sidebar`, Manage mode uses `nav.manage-sidebar`.
 */
export function getSidebarLocator(page: Page) {
  return page.locator('nav.sidebar, nav.manage-sidebar').first();
}

/**
 * Open the sidebar on mobile viewports by clicking the hamburger button.
 * On desktop viewports, the sidebar is always visible so this is a no-op.
 */
export async function ensureSidebarVisible(page: Page) {
  const viewport = page.viewportSize();
  if (viewport && viewport.width < 768) {
    const hamburger = page.locator('.hamburger-btn');
    // Wait for hamburger button to be visible and stable (Mobile Safari needs longer timeout)
    await hamburger.waitFor({ state: 'visible', timeout: 15000 }).catch(() => {});
    if (await hamburger.isVisible()) {
      // Use force: true for Safari/WebKit compatibility (overlay elements may intercept)
      await hamburger.click({ force: true });
      // Increased timeout for Mobile Safari sidebar animation
      await getSidebarLocator(page).waitFor({ state: 'visible', timeout: 20000 });
    }
  }
}
