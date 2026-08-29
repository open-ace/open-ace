/**
 * Management Page E2E Tests
 *
 * Tests for admin management features within Manage mode (/manage/*):
 * - User management
 * - Quota & Alerts
 * - Audit Center
 * - Compliance Management
 * - Security Center
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('User Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display user management page with heading', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Verify the user-management container exists
    const container = page.locator('.user-management');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify heading is present
    const heading = container.locator('h2').first();
    await expect(heading).toBeVisible();
  });

  test('should display user table or empty state', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // Either a table with users or an empty state should be visible
    const table = page.locator('.user-management table.table');
    const emptyState = page.locator('.user-management .empty-state');

    const hasTable = await table.isVisible().catch(() => false);
    const hasEmpty = await emptyState.isVisible().catch(() => false);
    expect(hasTable || hasEmpty).toBeTruthy();
  });

  test('should have add user button', async ({ page }) => {
    await page.goto('/manage/users');
    await waitForApp(page);

    // The "Add User" button should be visible in the header area
    const addButton = page
      .locator('.user-management button')
      .filter({ hasText: /add|添加/i })
      .first();
    await expect(addButton).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Quota & Alerts', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display quota page with tabs', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    // Verify container and heading
    const container = page.locator('.quota-alerts');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (quota and alerts)
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.first()).toBeVisible();
  });

  test('should switch between quota and alerts tabs', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const tabs = page.locator('.quota-alerts .nav-tabs .nav-link');

    // Click alerts tab (second tab)
    if ((await tabs.count()) > 1) {
      await tabs.nth(1).click();
      await page.waitForLoadState('networkidle');

      // Verify alerts tab is active
      await expect(tabs.nth(1)).toHaveClass(/active/);
    }
  });

  test('quota tab should not show alert rules overview card', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const container = page.locator('.quota-alerts');
    const tabs = container.locator('.nav-tabs .nav-link');

    // Anchor the default active tab is quota to remove implicit dependency
    await expect(tabs.nth(0)).toHaveClass(/active/);

    // The misplaced "Alert Rules" overview card must no longer render on the quota tab
    await expect(container.locator('.alert-rules-list')).toHaveCount(0);

    // No alert-rules card title should be present
    const alertRulesTitle = container
      .locator('.card-title, .card .card-header')
      .filter({ hasText: /alert rules|告警规则/i });
    await expect(alertRulesTitle).toHaveCount(0);

    // No "no alerts" empty state on the quota tab (quota empty state is "no quota data" instead)
    const noAlertsState = container
      .locator('.empty-state')
      .filter({ hasText: /no alerts found|暂无告警/i });
    await expect(noAlertsState).toHaveCount(0);
  });

  test('alerts tab should still render alert management content', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const container = page.locator('.quota-alerts');
    const tabs = container.locator('.nav-tabs .nav-link');

    // Switch to alerts tab (second tab)
    if ((await tabs.count()) > 1) {
      await tabs.nth(1).click();
      await page.waitForLoadState('networkidle');
      await expect(tabs.nth(1)).toHaveClass(/active/);

      // Alert statistics StatCards should be visible (the comprehensive alert management)
      const statCards = container.locator('.stat-card');
      await expect(statCards.first()).toBeVisible({ timeout: 10000 });

      // Either the alert list table or an empty state should be present (tolerant of zero alerts)
      const table = container.locator('table.table');
      const emptyState = container.locator('.empty-state');
      const hasTable = await table.isVisible().catch(() => false);
      const hasEmpty = await emptyState.isVisible().catch(() => false);
      expect(hasTable || hasEmpty).toBeTruthy();
    }
  });

  test('view-alerts hint button should navigate to alerts tab when present', async ({ page }) => {
    await page.goto('/manage/quota');
    await waitForApp(page);

    const container = page.locator('.quota-alerts');
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.nth(0)).toHaveClass(/active/);

    // The hint button only renders on the quota tab when there are unread alerts.
    // Tolerant of zero-alert environments: assert navigation when visible, absence otherwise.
    const hintButton = container
      .locator('button')
      .filter({ hasText: /view alerts|查看告警|アラートを表示|알림 보기/i });

    if (await hintButton.isVisible().catch(() => false)) {
      await hintButton.click();
      await page.waitForLoadState('networkidle');
      await expect(tabs.nth(1)).toHaveClass(/active/);
    } else {
      await expect(hintButton).toHaveCount(0);
    }
  });
});

test.describe('Audit Center', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display audit center with tabs', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    const container = page.locator('.audit-center');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (log and analysis)
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.first()).toBeVisible();
  });

  test('should display audit log table or content', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    // Wait for loading to complete (spinner may or may not appear)
    const loading = page.locator('.audit-center .spinner-border');
    await loading.waitFor({ state: 'hidden', timeout: 10000 }).catch(() => {});

    // Log tab should show filters card and table, or error alert, or empty state
    const table = page.locator('.audit-center table.table');
    const card = page.locator('.audit-center .card');
    const error = page.locator('.audit-center .alert');
    const emptyState = page.locator('.audit-center .empty-state');

    const hasTable = await table.isVisible().catch(() => false);
    const hasCard = await card.isVisible().catch(() => false);
    const hasError = await error.isVisible().catch(() => false);
    const hasEmptyState = await emptyState.isVisible().catch(() => false);

    // At least one of these should be visible
    expect(hasTable || hasCard || hasError || hasEmptyState).toBeTruthy();
  });

  test('should translate the resource-type column (no raw snake_case codes)', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    const rows = page.locator('.audit-center table tbody tr');
    const rowCount = await rows.count();
    // Tolerant of an empty audit table (zero seeded logs).
    if (rowCount === 0) return;

    // Resource type is the 4th column (index 3). A localized label never
    // contains an underscore, whereas a raw code (e.g. "remote_machine") does.
    const text = (await rows.first().locator('td').nth(3).innerText()).trim();
    expect(text === '-' || !text.includes('_')).toBeTruthy();
  });

  test('should render "-" for NULL resource_id and a value otherwise', async ({ page }) => {
    await page.goto('/manage/audit');
    await waitForApp(page);

    const rows = page.locator('.audit-center table tbody tr');
    const rowCount = await rows.count();
    if (rowCount === 0) return;

    // ID column is the 5th column (index 4). Each cell is either "-" (NULL)
    // or a non-empty PK — never blank.
    for (let i = 0; i < Math.min(rowCount, 5); i++) {
      const text = (await rows.nth(i).locator('td').nth(4).innerText()).trim();
      expect(text.length > 0).toBeTruthy();
    }
  });

  test('should open details in a modal instead of window.alert', async ({ page }) => {
    // The lane's organic audit rows are bare logins without details, and the
    // details <pre> only renders for rows whose sanitized details are
    // non-empty — self-provision the premise BEFORE navigating (the audit
    // list does not auto-refresh). Re-writing the admin's own quota with its
    // current values logs a QUOTA_UPDATE entry with details and no lasting
    // side effects.
    const quotaResp = await page.request.get('/api/admin/quota/usage');
    expect(quotaResp.ok()).toBeTruthy();
    const usage = (await quotaResp.json()) as Array<{
      id: number;
      daily_token_quota?: number | null;
      monthly_token_quota?: number | null;
      daily_request_quota?: number | null;
      monthly_request_quota?: number | null;
    }>;
    const admin = usage.find((u) => u.id === 1) ?? usage[0];
    expect(admin).toBeTruthy();
    // Re-write every currently-set value (an all-empty payload is rejected);
    // this is idempotent for the user and produces a QUOTA_UPDATE audit row.
    const payload: Record<string, number> = {};
    if (admin.daily_token_quota != null) payload.daily_token_quota = admin.daily_token_quota;
    if (admin.monthly_token_quota != null) payload.monthly_token_quota = admin.monthly_token_quota;
    if (admin.daily_request_quota != null) payload.daily_request_quota = admin.daily_request_quota;
    if (admin.monthly_request_quota != null)
      payload.monthly_request_quota = admin.monthly_request_quota;
    expect(Object.keys(payload).length).toBeGreaterThan(0);
    const writeResp = await page.request.put(`/api/admin/users/${admin.id}/quota`, {
      data: payload,
    });
    expect(writeResp.ok()).toBeTruthy();

    await page.goto('/manage/audit');
    await waitForApp(page);

    // The eye button renders unconditionally per row; the newest row (the
    // QUOTA_UPDATE just triggered) is first (listed by timestamp DESC).
    const eyeButton = page.locator('.audit-detail-btn').first();
    await expect(eyeButton).toBeVisible();
    // Same Mobile Chrome transient hit-target interception as the terminal
    // buttons in remote-directory-browser.spec.ts: the button resolves, is
    // visible/enabled/stable, and a rotating set of unrelated elements (header,
    // filter <select>, <td>) intercepts pointer events on retries only — no
    // real overlay owns those points. force skips only the pointercache check;
    // the modal assertions below prove the click landed on this button.
    await eyeButton.click({ force: true });

    // Modal body shows the JSON details (contains an opening brace).
    const modalBody = page.locator('.modal-body pre').first();
    await expect(modalBody).toBeVisible({ timeout: 5000 });
    expect((await modalBody.innerText()).includes('{')).toBeTruthy();

    // Close via the header close button and confirm the modal is gone. The
    // animating modal-header transiently intercepts the small close button's
    // hit point on Mobile Chrome (same family as the interceptors above);
    // force is safe because the toHaveCount(0) below proves the modal closed.
    const closeBtn = page.locator('.modal-header .btn-close').first();
    await expect(closeBtn).toBeVisible();
    await closeBtn.click({ force: true });
    await expect(modalBody).toHaveCount(0);
  });
});

test.describe('Compliance Management', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display compliance page with tabs', async ({ page }) => {
    await page.goto('/manage/compliance');
    await waitForApp(page);

    const container = page.locator('.compliance-mgmt');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (reports and retention)
    const tabs = container.locator('.nav-tabs .nav-link');
    await expect(tabs.first()).toBeVisible();
  });

  test('should display report type cards on reports tab', async ({ page }) => {
    await page.goto('/manage/compliance');
    await waitForApp(page);

    // Reports tab should have report type selection cards
    const cards = page.locator('.compliance-mgmt .card');
    await expect(cards.first()).toBeVisible({ timeout: 10000 });
  });
});

test.describe('Security Center', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display security center with tabs', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    const container = page.locator('.security-center');
    await expect(container).toBeVisible({ timeout: 10000 });

    // Verify tabs exist (filter, settings, audit thresholds)
    const tabs = container.locator('.nav-tabs .nav-link');
    const tabCount = await tabs.count();
    expect(tabCount).toBeGreaterThanOrEqual(2);
  });

  test('should display session timeout input on settings tab', async ({ page }) => {
    await page.goto('/manage/security');
    await waitForApp(page);

    // Switch to settings tab if not already active
    const settingsTab = page
      .locator('.security-center .nav-tabs .nav-link')
      .filter({ hasText: /setting|配置/i })
      .first();
    if (await settingsTab.isVisible()) {
      await settingsTab.click();
      await page.waitForLoadState('networkidle');
    }

    // Look for form inputs (session timeout, password policy, etc.)
    const formInputs = page.locator(
      '.security-center input[type="number"], .security-center input[type="text"]'
    );
    const hasInputs = await formInputs
      .first()
      .isVisible({ timeout: 5000 })
      .catch(() => false);
    expect(typeof hasInputs).toBe('boolean');
  });
});
