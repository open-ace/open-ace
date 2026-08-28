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
    const navigated = await page
      .waitForURL(/^(?!.*\/login).+$/, { timeout: 5000 })
      .then(() => true)
      .catch(() => false);
    if (!navigated) {
      console.warn(`Login redirect failed — still at ${page.url()}, navigating to /`);
      await page.goto('/');
    }
  }

  // Post-login modal contract (#3214): the seeded-lane user must NOT carry
  // must_change_password (lanes init with OPENACE_DEFAULT_ADMIN_MUST_CHANGE_
  // PASSWORD=false, same contract as the python e2e lanes). If the forced
  // password-change modal DOES appear, the lane is misconfigured and every
  // test would otherwise hang — fail fast with a clear diagnostic instead.
  // The historical "Skip tour" modal is gone from the product (no Skip
  // button exists in any current dialog), so a bounded short wait replaces
  // the old 15s unconditional one (545 logins x 15s of dead waiting would
  // sink the weekly lane on a single worker).
  const modalDialog = page.locator('[role="dialog"][aria-modal="true"]');
  const modalVisible = await modalDialog
    .waitFor({ state: 'visible', timeout: 2500 })
    .then(() => true)
    .catch(() => false);

  if (modalVisible) {
    const forceChange = modalDialog.locator('[data-testid="force-change-password-modal"]');
    if (await forceChange.count()) {
      throw new Error(
        'Lane contract violation: the forced password-change modal appeared after login. ' +
          'The test lane must seed its database with ' +
          'OPENACE_DEFAULT_ADMIN_MUST_CHANGE_PASSWORD=false (see scripts/init_db.py and the ' +
          'weekly-quality workflow) — the modal has no Skip button by design and cannot be ' +
          'dismissed from tests.'
      );
    }
    // Unknown dismissible dialog: tolerate and continue (spec-level
    // assertions remain responsible for the UI state they depend on).
  }

  // Wait for page to be ready
  await page.waitForLoadState('networkidle');
}

/**
 * Wait for app to be ready
 */
export async function waitForApp(page: Page) {
  await page.waitForLoadState('networkidle');

  // Same contract as login(): the forced password-change modal means the
  // lane is misconfigured — fail fast rather than timeout downstream.
  const modalDialog = page.locator('[role="dialog"][aria-modal="true"]');
  const isModalVisible = await modalDialog.isVisible().catch(() => false);
  if (isModalVisible) {
    const forceChange = modalDialog.locator('[data-testid="force-change-password-modal"]');
    if (await forceChange.count()) {
      throw new Error(
        'Lane contract violation: forced password-change modal is open — the lane must seed ' +
          'with OPENACE_DEFAULT_ADMIN_MUST_CHANGE_PASSWORD=false (see scripts/init_db.py).'
      );
    }
  }

  // Wait for main content to be visible
  await page
    .locator('main')
    .waitFor({ state: 'visible', timeout: 10000 })
    .catch(() => {});
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
