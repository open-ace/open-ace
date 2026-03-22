/**
 * Data Verification Test - Check actual data on pages
 */

import { test, expect } from '@playwright/test';
import { login, waitForApp } from './helpers';

test.describe('Data Verification Test', () => {
  test.beforeEach(async ({ page }) => {
    await login(page, 'admin', 'admin123');
    await waitForApp(page);
  });

  test('verify Dashboard data', async ({ page }) => {
    await page.goto('/');
    await page.waitForTimeout(3000);
    
    // Take full page screenshot
    await page.screenshot({ path: '../../screenshots/data-verification/dashboard-full.png', fullPage: true });
    
    // Check for summary data (total tokens, requests)
    const summaryData = await page.locator('.summary-data, .total-tokens, .total-requests').count();
    console.log(`Dashboard summary data elements: ${summaryData}`);
    
    // Check for today's usage section
    const todaySection = await page.locator('.today-section, .today-usage').count();
    console.log(`Dashboard today section: ${todaySection}`);
    
    // Check for trend chart data
    const chartCanvas = await page.locator('canvas').count();
    console.log(`Dashboard charts: ${chartCanvas}`);
    
    // Get visible text content
    const mainText = await page.locator('main').textContent();
    console.log('Dashboard text content (first 500 chars):');
    console.log(mainText?.substring(0, 500));
    
    // Check for empty state
    const emptyState = await page.locator('.empty-state, .no-data, text=暂无数据').count();
    console.log(`Empty state elements: ${emptyState}`);
  });

  test('verify Messages data', async ({ page }) => {
    await page.goto('/messages');
    await page.waitForTimeout(3000);
    
    await page.screenshot({ path: '../../screenshots/data-verification/messages-full.png', fullPage: true });
    
    // Check for message list
    const messageRows = await page.locator('tr[role="row"], .message-row, tbody tr').count();
    console.log(`Message rows: ${messageRows}`);
    
    // Check for empty state
    const emptyState = await page.locator('.empty-state, .no-data, text=暂无数据, text=No messages').count();
    console.log(`Empty state elements: ${emptyState}`);
    
    // Get visible text content
    const mainText = await page.locator('main').textContent();
    console.log('Messages text content (first 500 chars):');
    console.log(mainText?.substring(0, 500));
  });

  test('verify Analysis data', async ({ page }) => {
    await page.goto('/analysis');
    await page.waitForTimeout(3000);
    
    await page.screenshot({ path: '../../screenshots/data-verification/analysis-full.png', fullPage: true });
    
    // Check for metric cards
    const metricCards = await page.locator('.metric-card, .stat-card, .analysis-card').count();
    console.log(`Analysis metric cards: ${metricCards}`);
    
    // Check for charts
    const charts = await page.locator('canvas, .chart').count();
    console.log(`Analysis charts: ${charts}`);
    
    // Get visible text content
    const mainText = await page.locator('main').textContent();
    console.log('Analysis text content (first 500 chars):');
    console.log(mainText?.substring(0, 500));
  });

  test('verify Report data', async ({ page }) => {
    await page.goto('/report');
    await page.waitForTimeout(3000);
    
    await page.screenshot({ path: '../../screenshots/data-verification/report-full.png', fullPage: true });
    
    // Check for report content
    const reportContent = await page.locator('.report-content, .report-section').count();
    console.log(`Report sections: ${reportContent}`);
    
    // Get visible text content
    const mainText = await page.locator('main').textContent();
    console.log('Report text content (first 500 chars):');
    console.log(mainText?.substring(0, 500));
  });
});
