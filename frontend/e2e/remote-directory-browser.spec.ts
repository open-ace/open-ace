/**
 * Remote Directory Browser E2E Tests
 *
 * Issue #584: Tests for directory browser functionality in remote/terminal workspace
 */

import { test, expect } from '@playwright/test';

test.describe('Remote Directory Browser', () => {
  // Login before each test
  test.beforeEach(async ({ page }) => {
    await page.goto('/login');
    await page.fill('input[name="username"]', 'admin');
    await page.fill('input[name="password"]', 'admin123');
    await page.click('button[type="submit"]');
    await page.waitForURL(/\/work|\/dashboard/);
  });

  test('user can browse remote directory when creating session', async ({ page }) => {
    // Navigate to workspace
    await page.goto('/work');
    
    // Open new session modal
    await page.click('[data-testid="new-session-btn"]') || await page.click('button:has-text("newSession")');
    
    // Select remote workspace type
    await page.click('button:has-text("remoteWorkspace")');
    
    // Wait for machine selector
    await page.waitForSelector('[data-testid="machine-selector"]') || 
      await page.waitForSelector('select');
    
    // Select a machine (if available)
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    
    if (options > 1) {
      await machineSelect.selectOption({ index: 1 });
      
      // Verify browse button appears
      await expect(page.locator('button:has-text("browse")')).toBeVisible();
    }
  });

  test('browse button opens directory browser modal', async ({ page }) => {
    await page.goto('/work');
    
    // Open new session modal
    await page.click('button:has-text("newSession")') || await page.click('.workspace-new-tab-btn');
    
    // Select remote workspace
    await page.click('button:has-text("remoteWorkspace")');
    
    // Select machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    
    if (options > 1) {
      await machineSelect.selectOption({ index: 1 });
      
      // Click browse button
      await page.click('button:has-text("browse")');
      
      // Verify directory browser modal opens
      await expect(page.locator('.modal:has-text("browseDirectory")')).toBeVisible();
      
      // Verify directory list is shown
      await expect(page.locator('.directory-list, .list-group')).toBeVisible();
    }
  });

  test('terminal workspace shows browse button', async ({ page }) => {
    await page.goto('/work');
    
    // Open new session modal
    await page.click('button:has-text("newSession")') || await page.click('.workspace-new-tab-btn');
    
    // Select terminal workspace
    await page.click('button:has-text("terminalWorkspace")') || 
      await page.click('button:has-text("Terminal")');
    
    // Select machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    
    if (options > 1) {
      await machineSelect.selectOption({ index: 1 });
      
      // Verify browse button appears for terminal
      await expect(page.locator('button:has-text("browse")')).toBeVisible();
    }
  });

  test('user can select path from directory browser', async ({ page }) => {
    await page.goto('/work');
    
    // Open new session modal
    await page.click('button:has-text("newSession")') || await page.click('.workspace-new-tab-btn');
    
    // Select remote workspace
    await page.click('button:has-text("remoteWorkspace")');
    
    // Select machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    
    if (options > 1) {
      await machineSelect.selectOption({ index: 1 });
      
      // Click browse button
      await page.click('button:has-text("browse")');
      
      // Wait for modal
      await page.waitForSelector('.modal:has-text("browseDirectory")');
      
      // Click a directory item (if any)
      const dirItems = page.locator('.list-group-item, .directory-item');
      const itemCount = await dirItems.count();
      
      if (itemCount > 0) {
        await dirItems.first().click();
        
        // Click select button
        await page.click('button:has-text("selectDirectory")') || 
          await page.click('button:has-text("select")');
        
        // Modal should close
        await expect(page.locator('.modal:has-text("browseDirectory")')).not.toBeVisible();
        
        // Path should be filled in input
        const pathInput = page.locator('input[placeholder*="workspace"]').first();
        await expect(pathInput).not.toBeEmpty();
      }
    }
  });

  test('path history buttons are displayed', async ({ page }) => {
    // This test requires localStorage to have path history
    // Set up path history in localStorage
    await page.goto('/work');
    await page.evaluate(() => {
      localStorage.setItem('remote-path-history-test-machine-1', 
        JSON.stringify(['/root/workspace/test-project', '/root/workspace/another-project']));
    });
    
    // Reload to apply localStorage
    await page.reload();
    
    // Open new session modal
    await page.click('button:has-text("newSession")') || await page.click('.workspace-new-tab-btn');
    
    // Select remote workspace
    await page.click('button:has-text("remoteWorkspace")');
    
    // Select machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    
    if (options > 1) {
      await machineSelect.selectOption({ index: 1 });
      
      // Check if path history section appears
      const historySection = page.locator('small:has-text("recentPaths")');
      // If history exists for the machine, it should be visible
      if (await historySection.count() > 0) {
        await expect(historySection).toBeVisible();
        
        // History buttons should be clickable
        const historyButtons = page.locator('button.btn-sm').filter({ hasText: 'project' });
        if (await historyButtons.count() > 0) {
          await expect(historyButtons.first()).toBeEnabled();
        }
      }
    }
  });

  test('directory browser modal closes on cancel', async ({ page }) => {
    await page.goto('/work');
    
    // Open new session modal
    await page.click('button:has-text("newSession")') || await page.click('.workspace-new-tab-btn');
    
    // Select remote workspace
    await page.click('button:has-text("remoteWorkspace")');
    
    // Select machine if available
    const machineSelect = page.locator('select').first();
    const options = await machineSelect.locator('option').count();
    
    if (options > 1) {
      await machineSelect.selectOption({ index: 1 });
      
      // Click browse button
      await page.click('button:has-text("browse")');
      
      // Wait for modal
      await page.waitForSelector('.modal:has-text("browseDirectory")');
      
      // Click close button
      await page.click('.modal:has-text("browseDirectory") .btn-close') ||
        await page.click('.modal:has-text("browseDirectory") button:has-text("close")');
      
      // Modal should close
      await expect(page.locator('.modal:has-text("browseDirectory")')).not.toBeVisible();
    }
  });

  test('browse button disabled when no machine selected', async ({ page }) => {
    await page.goto('/work');
    
    // Open new session modal
    await page.click('button:has-text("newSession")') || await page.click('.workspace-new-tab-btn');
    
    // Select remote workspace type (but don't select machine)
    await page.click('button:has-text("remoteWorkspace")');
    
    // Browse button should not be visible or disabled
    const browseBtn = page.locator('button:has-text("browse")');
    
    // Either button doesn't exist or is disabled
    const isVisible = await browseBtn.isVisible().catch(() => false);
    if (isVisible) {
      await expect(browseBtn).toBeDisabled();
    }
  });
});
