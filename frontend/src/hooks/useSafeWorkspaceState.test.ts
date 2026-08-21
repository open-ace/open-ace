/**
 * useSafeWorkspaceState Hook tests (Issue #2953)
 * Tests for centralized workspace state validation
 */

import { describe, it, expect } from 'vitest';
import { validateActiveTabId, validateTabsOrder } from './useSafeWorkspaceState';
import type { WorkspaceTab } from '@/store';

describe('useSafeWorkspaceState Hook (Issue #2953)', () => {
  describe('validateActiveTabId', () => {
    it('should return empty string when tabs array is empty', () => {
      expect(validateActiveTabId('any-id', [])).toBe('');
    });

    it('should return the activeTabId if it exists in tabs', () => {
      const tabs: WorkspaceTab[] = [
        { id: 'tab-1', title: 'Tab 1', createdAt: Date.now() },
        { id: 'tab-2', title: 'Tab 2', createdAt: Date.now() },
      ];
      expect(validateActiveTabId('tab-2', tabs)).toBe('tab-2');
    });

    it('should return first tab id if activeTabId does not exist in tabs', () => {
      const tabs: WorkspaceTab[] = [
        { id: 'tab-1', title: 'Tab 1', createdAt: Date.now() },
        { id: 'tab-2', title: 'Tab 2', createdAt: Date.now() },
      ];
      expect(validateActiveTabId('non-existent', tabs)).toBe('tab-1');
    });

    it('should return empty string if tabs is empty array', () => {
      expect(validateActiveTabId('any-id', [])).toBe('');
    });
  });

  describe('validateTabsOrder', () => {
    it('should return empty array when both inputs are empty', () => {
      expect(validateTabsOrder([], [])).toEqual([]);
    });

    it('should return empty array when tabsOrder is empty', () => {
      const tabs: WorkspaceTab[] = [{ id: 'tab-1', title: 'Tab 1', createdAt: Date.now() }];
      expect(validateTabsOrder([], tabs)).toEqual([]);
    });

    it('should filter out IDs not in tabs', () => {
      const tabs: WorkspaceTab[] = [
        { id: 'tab-1', title: 'Tab 1', createdAt: Date.now() },
        { id: 'tab-2', title: 'Tab 2', createdAt: Date.now() },
      ];
      const tabsOrder = ['tab-1', 'invalid-tab', 'tab-2', 'another-invalid'];
      expect(validateTabsOrder(tabsOrder, tabs)).toEqual(['tab-1', 'tab-2']);
    });

    it('should preserve valid IDs in order', () => {
      const tabs: WorkspaceTab[] = [
        { id: 'tab-1', title: 'Tab 1', createdAt: Date.now() },
        { id: 'tab-2', title: 'Tab 2', createdAt: Date.now() },
        { id: 'tab-3', title: 'Tab 3', createdAt: Date.now() },
      ];
      const tabsOrder = ['tab-3', 'tab-1', 'tab-2'];
      expect(validateTabsOrder(tabsOrder, tabs)).toEqual(['tab-3', 'tab-1', 'tab-2']);
    });

    it('should return all IDs when all are valid', () => {
      const tabs: WorkspaceTab[] = [
        { id: 'tab-1', title: 'Tab 1', createdAt: Date.now() },
        { id: 'tab-2', title: 'Tab 2', createdAt: Date.now() },
      ];
      const tabsOrder = ['tab-1', 'tab-2'];
      expect(validateTabsOrder(tabsOrder, tabs)).toEqual(['tab-1', 'tab-2']);
    });

    it('should handle empty tabs array', () => {
      const tabsOrder = ['tab-1', 'tab-2'];
      expect(validateTabsOrder(tabsOrder, [])).toEqual([]);
    });
  });

  describe('useSafeWorkspaceState hook integration', () => {
    // Note: Testing the hook directly requires a React test renderer
    // The following tests verify the exported helper functions
    // For full hook testing, see the validation tests in store.validation.test.ts

    it('should validate tabsOrder against tabs (simulated)', () => {
      const tabs: WorkspaceTab[] = [
        { id: 'tab-1', title: 'Tab 1', createdAt: Date.now() },
        { id: 'tab-2', title: 'Tab 2', createdAt: Date.now() },
      ];
      const tabsOrder = ['tab-1', 'non-existent', 'tab-2'];

      // Simulate what useSafeWorkspaceState does
      const validOrder = validateTabsOrder(tabsOrder, tabs);

      expect(validOrder).toEqual(['tab-1', 'tab-2']);
      expect(validOrder).toHaveLength(2);
    });
  });
});
