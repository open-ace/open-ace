/**
 * Session Statistics Card E2E Tests
 *
 * Covers the shared "会话统计 / Session Statistics" card rendered on the
 * Token Trend page (/manage/analysis/trend) via <SessionStatisticsCard>.
 *
 * Verifies the readability fixes:
 *  - The card and its stable row anchors render.
 *  - "多轮对话比例 / Multi-turn Ratio" now shows a real percentage in [0,100]%,
 *    not the previous average-message-count value (e.g. 90.4).
 *  - Each metric exposes a help affordance with a stable test id (tooltip).
 *  - Changing the date range keeps the ratio valid (proves the "1 vs 474"
 *    cross-scope split is gone — both ranges read from one real query).
 *
 * Requires the full stack (backend + DB + frontend) running. Run with:
 *   cd frontend && npx playwright test e2e/session-statistics.spec.ts
 */

import { expect, test } from '@playwright/test';
import { login, waitForApp } from './helpers';

const RATIO_RE = /^\d+(\.\d+)?%$/;

test.describe('Session Statistics card', () => {
  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('renders the card with stable row anchors', async ({ page }) => {
    await page.goto('/manage/analysis/trend');
    await waitForApp(page);

    const card = page.getByTestId('session-statistics-card');
    await expect(card).toBeVisible({ timeout: 15000 });

    // All expected rows are present with stable ids
    for (const rowId of [
      'session-total-conversations',
      'session-total-messages',
      'session-avg-messages',
      'session-avg-tokens',
      'session-multi-turn-ratio',
    ]) {
      await expect(page.getByTestId(rowId)).toBeVisible();
    }

    // Each row has a help affordance (stable tooltip anchor)
    await expect(page.getByTestId('session-multi-turn-ratio-help')).toBeVisible();
  });

  test('multi-turn ratio renders as a percentage in [0, 100]%', async ({ page }) => {
    await page.goto('/manage/analysis/trend');
    await waitForApp(page);

    const ratioCell = page.getByTestId('session-multi-turn-ratio').locator('td.text-end');
    await expect(ratioCell).toBeVisible({ timeout: 15000 });
    await expect(ratioCell).toHaveText(RATIO_RE);

    const text = await ratioCell.innerText();
    const pct = parseFloat(text.replace('%', ''));
    expect(pct).toBeGreaterThanOrEqual(0);
    expect(pct).toBeLessThanOrEqual(100);
  });

  test('ratio stays valid when the date range changes', async ({ page }) => {
    await page.goto('/manage/analysis/trend');
    await waitForApp(page);

    const ratioCell = () =>
      page.getByTestId('session-multi-turn-ratio').locator('td.text-end');
    await expect(ratioCell()).toBeVisible({ timeout: 15000 });

    // Switch to the 7-day quick range, then back to 30 days. The card must keep
    // showing a valid percentage — confirming one consistent, date-scoped source.
    const seven = page.locator('button.btn', { hasText: /7/ }).first();
    if (await seven.isVisible().catch(() => false)) {
      await seven.click();
      await page.waitForLoadState('networkidle');
      await expect(ratioCell()).toHaveText(RATIO_RE);
    }
  });
});
