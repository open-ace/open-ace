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
  // The modal is loaded lazily via Suspense, so it may take time to appear in CI environments
  // Use a longer polling loop to handle slow CI network conditions
  const modalDialog = page.locator('[role="dialog"][aria-modal="true"]');
  const skipButton = modalDialog.locator('button:has-text("Skip"), button:has-text("跳过")');

  // Poll for modal appearance for up to 15 seconds (3 iterations of 5s each)
  // This handles slow CI networks where lazy-loaded components take time to load
  let modalHandled = false;
  for (let i = 0; i < 3; i++) {
    const modalVisible = await modalDialog.isVisible().catch(() => false);
    if (modalVisible) {
      // Wait for Skip button to be visible and clickable
      await skipButton.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
      // Click Skip button to dismiss the modal
      await skipButton.click({ force: true });
      // Wait for modal to disappear completely
      await modalDialog.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
      modalHandled = true;
      break;
    }
    // Wait 5 seconds before checking again
    await page.waitForTimeout(5000);
  }

  // Log if modal was not handled (for debugging CI failures)
  if (!modalHandled) {
    const finalCheck = await modalDialog.isVisible().catch(() => false);
    if (finalCheck) {
      console.warn('ForceChangePasswordModal is still visible after 15s polling - test may fail');
    }
  }

  // Wait for page to be ready
  await page.waitForLoadState('networkidle');
}

/**
 * Wait for app to be ready
 */
export async function waitForApp(page: Page) {
  await page.waitForLoadState('networkidle');

  // Fallback: handle ForceChangePasswordModal if login helper missed it
  const modalDialog = page.locator('[role="dialog"][aria-modal="true"]');
  const isModalVisible = await modalDialog.isVisible().catch(() => false);
  if (isModalVisible) {
    const skipButton = modalDialog.locator('button:has-text("Skip"), button:has-text("跳过")');
    if (await skipButton.isVisible().catch(() => false)) {
      await skipButton.click({ force: true });
      await modalDialog.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
    }
  }

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

/**
 * Close the sidebar on mobile viewports by clicking the hamburger button.
 * On desktop viewports, the sidebar is always visible so this is a no-op.
 * Useful when the sidebar covers main content and intercepts pointer events.
 */
export async function ensureSidebarHidden(page: Page) {
  const viewport = page.viewportSize();
  if (viewport && viewport.width < 768) {
    const sidebar = getSidebarLocator(page);
    if (await sidebar.isVisible()) {
      const hamburger = page.locator('.hamburger-btn');
      await hamburger.click({ force: true });
      await sidebar.waitFor({ state: 'hidden', timeout: 5000 }).catch(() => {});
    }
  }
}
