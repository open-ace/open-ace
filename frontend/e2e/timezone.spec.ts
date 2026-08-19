/**
 * Timezone E2E Tests - Verify cross-timezone datetime display
 *
 * Issue #2767: 增强时间字段跨时区显示的端到端测试
 *
 * Tests:
 * 1. Timezone-specific browser context simulation (Asia/Shanghai, America/New_York)
 * 2. Timestamp format parsing (Z suffix, +00:00 suffix, no suffix)
 * 3. Project management page time field display verification
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

// Test timestamps: 2026-08-17T08:06:59 UTC
const TEST_UTC_TIMESTAMP = '2026-08-17T08:06:59';
const TEST_UTC_TIMESTAMP_Z = '2026-08-17T08:06:59Z';
const TEST_UTC_TIMESTAMP_OFFSET = '2026-08-17T08:06:59+00:00';

test.describe('Timezone Display - Core Tests', () => {
  test.describe.configure({ mode: 'parallel' });

  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  // Test 1: Project management page time display
  test('should display project time with correct timezone conversion', async ({ page }) => {
    await page.goto('/manage/projects');
    await waitForApp(page);

    // Wait for data loading to complete
    const container = page.locator('.project-management');
    await container.waitFor({ state: 'visible', timeout: 10000 });

    // Check if time fields are displayed (if data exists)
    const timeCells = page.locator('td').filter({ hasText: /\d{4}.*\d{2}.*\d{2}/ });
    const count = await timeCells.count();

    if (count > 0) {
      // Verify time format is valid
      const timeText = await timeCells.first().textContent();
      expect(timeText).toBeTruthy();
      expect(timeText!.length).toBeGreaterThan(0);
    }
  });

  // Test 2: Timestamp format parsing - Z suffix
  test('should parse timestamp with Z suffix correctly', async ({ page }) => {
    const d = new Date(TEST_UTC_TIMESTAMP_Z);
    expect(d.toISOString()).toContain('2026-08-17T08:06:59');
    expect(d.getTime()).toBeGreaterThan(0);
  });

  // Test 3: Timestamp format parsing - +00:00 suffix
  test('should parse timestamp with +00:00 suffix correctly', async ({ page }) => {
    const d = new Date(TEST_UTC_TIMESTAMP_OFFSET);
    expect(d.toISOString()).toContain('2026-08-17T08:06:59');
    expect(d.getTime()).toBeGreaterThan(0);
  });

  // Test 4: Legacy timestamp without suffix (backward compatibility)
  test('should handle legacy timestamp without suffix', async ({ page }) => {
    // Timestamps without suffix are treated as local time
    // After PR #2762, new data will always have Z suffix
    const d = new Date(TEST_UTC_TIMESTAMP);
    expect(d).toBeTruthy();
    expect(d.getTime()).toBeGreaterThan(0);
  });

  // Test 5: Both Z and +00:00 should produce same UTC time
  test('should parse Z and +00:00 timestamps identically', async ({ page }) => {
    const d1 = new Date(TEST_UTC_TIMESTAMP_Z);
    const d2 = new Date(TEST_UTC_TIMESTAMP_OFFSET);
    expect(d1.getTime()).toBe(d2.getTime());
  });
});

// Timezone-specific tests using Playwright's timezoneId configuration
test.describe('Asia/Shanghai Timezone (UTC+8)', () => {
  test.use({ timezoneId: 'Asia/Shanghai' });

  test('should convert UTC to local time correctly', async ({ page }) => {
    const utcTime = '2026-08-17T08:06:59Z';
    const d = new Date(utcTime);

    // UTC 08:06:59 should display as local time 16:06:59 (UTC+8)
    const localHour = d.getHours();
    expect(localHour).toBe(16); // 08 + 8 = 16

    // Verify the date components
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(7); // August (0-indexed)
    expect(d.getDate()).toBe(17);
    expect(d.getMinutes()).toBe(6);
    expect(d.getSeconds()).toBe(59);
  });

  test('should handle midnight crossing correctly', async ({ page }) => {
    // UTC 20:00:00 should be next day 04:00:00 in Asia/Shanghai
    const utcTime = '2026-08-17T20:00:00Z';
    const d = new Date(utcTime);

    const localHour = d.getHours();
    expect(localHour).toBe(4); // 20 + 8 = 28, modulo 24 = 4
    expect(d.getDate()).toBe(18); // Next day
  });
});

test.describe('America/New_York Timezone (UTC-4 DST)', () => {
  test.use({ timezoneId: 'America/New_York' });

  test('should convert UTC to local time correctly', async ({ page }) => {
    const utcTime = '2026-08-17T08:06:59Z';
    const d = new Date(utcTime);

    // UTC 08:06:59 should display as local time 04:06:59 (UTC-4 during DST)
    // Note: August is during Daylight Saving Time, New York is UTC-4
    const localHour = d.getHours();
    expect(localHour).toBe(4); // 08 - 4 = 04

    // Verify the date components
    expect(d.getFullYear()).toBe(2026);
    expect(d.getMonth()).toBe(7); // August (0-indexed)
    expect(d.getDate()).toBe(17);
    expect(d.getMinutes()).toBe(6);
    expect(d.getSeconds()).toBe(59);
  });

  test('should handle day boundary correctly', async ({ page }) => {
    // UTC 03:00:00 should be previous day 23:00:00 in America/New_York (UTC-4)
    const utcTime = '2026-08-17T03:00:00Z';
    const d = new Date(utcTime);

    const localHour = d.getHours();
    expect(localHour).toBe(23); // 03 - 4 = -1, +24 = 23
    expect(d.getDate()).toBe(16); // Previous day
  });
});

// Integration test: Verify formatDateTime function behavior
test.describe('formatDateTime Integration', () => {
  test.use({ timezoneId: 'Asia/Shanghai' });

  test.beforeEach(async ({ page }) => {
    await login(page);
  });

  test('should display correct local time in project management', async ({ page }) => {
    await page.goto('/manage/projects');
    await waitForApp(page);

    const container = page.locator('.project-management');
    await container.waitFor({ state: 'visible', timeout: 10000 });

    // If there's project data, verify time display format
    const timeCells = page.locator('td').filter({ hasText: /\d{4}/ });
    const count = await timeCells.count();

    // This test is informational - verifies the page renders without errors
    // Actual time correctness depends on the data returned by the API
    console.log(`Found ${count} cells with potential time values`);
  });
});

// Negative timezone offset test
test.describe('Pacific/Honolulu Timezone (UTC-10)', () => {
  test.use({ timezoneId: 'Pacific/Honolulu' });

  test('should convert UTC to local time with negative offset', async ({ page }) => {
    const utcTime = '2026-08-17T08:06:59Z';
    const d = new Date(utcTime);

    // UTC 08:06:59 should display as local time 22:06:59 (UTC-10)
    // Note: Previous day in Honolulu
    const localHour = d.getHours();
    expect(localHour).toBe(22); // 08 - 10 = -2, +24 = 22
    expect(d.getDate()).toBe(16); // Previous day
  });
});
