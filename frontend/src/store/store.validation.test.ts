/**
 * Store validation tests (Issue #2953)
 * Tests for store version management and schema validation
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { validateStoredState, migrate, isValidWorkspaceTab } from './index';
import type { PersistedState, WorkspaceTab } from './index';

describe('Store Validation (Issue #2953)', () => {
  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear();
  });

  describe('isValidWorkspaceTab', () => {
    it('should return true for valid tab', () => {
      const validTab: WorkspaceTab = {
        id: 'tab-1',
        title: 'Tab 1',
        createdAt: Date.now(),
      };
      expect(isValidWorkspaceTab(validTab)).toBe(true);
    });

    it('should return false for null', () => {
      expect(isValidWorkspaceTab(null)).toBe(false);
    });

    it('should return false for non-object', () => {
      expect(isValidWorkspaceTab('string')).toBe(false);
      expect(isValidWorkspaceTab(123)).toBe(false);
    });

    it('should return false for missing id', () => {
      expect(isValidWorkspaceTab({ title: 'No ID' })).toBe(false);
    });

    it('should return false for missing title', () => {
      expect(isValidWorkspaceTab({ id: 'tab-1' })).toBe(false);
    });

    it('should return false for id not string', () => {
      expect(isValidWorkspaceTab({ id: 123, title: 'Tab' })).toBe(false);
    });
  });

  describe('validateStoredState', () => {
    it('should return empty object for null input', () => {
      expect(validateStoredState(null)).toEqual({});
    });

    it('should return empty object for non-object input', () => {
      expect(validateStoredState('string')).toEqual({});
      expect(validateStoredState(123)).toEqual({});
    });

    it('should validate valid workspaceTabs array', () => {
      const state = {
        workspaceTabs: [
          { id: 'tab-1', title: 'Tab 1' },
          { id: 'tab-2', title: 'Tab 2' },
        ],
      };
      const result = validateStoredState(state);
      expect(result.workspaceTabs).toHaveLength(2);
      expect(result.workspaceTabs?.[0].id).toBe('tab-1');
    });

    it('should filter invalid items from workspaceTabs', () => {
      const state = {
        workspaceTabs: [
          { id: 'valid-tab', title: 'Valid Tab' },
          { invalid: 'tab' }, // Missing id and title
          null,
          'invalid',
        ],
      };
      const result = validateStoredState(state);
      expect(result.workspaceTabs).toHaveLength(1);
      expect(result.workspaceTabs?.[0].id).toBe('valid-tab');
    });

    it('should reject non-array workspaceTabs', () => {
      const state = {
        workspaceTabs: 'not-an-array',
      };
      const result = validateStoredState(state);
      expect(result.workspaceTabs).toBeUndefined();
    });

    it('should validate valid workspaceActiveTabId', () => {
      const state = {
        workspaceActiveTabId: 'tab-1',
      };
      const result = validateStoredState(state);
      expect(result.workspaceActiveTabId).toBe('tab-1');
    });

    it('should reject non-string workspaceActiveTabId', () => {
      const state = {
        workspaceActiveTabId: 123,
      };
      const result = validateStoredState(state);
      expect(result.workspaceActiveTabId).toBeUndefined();
    });

    it('should validate valid workspaceTabsOrder', () => {
      const state = {
        workspaceTabsOrder: ['tab-1', 'tab-2'],
      };
      const result = validateStoredState(state);
      expect(result.workspaceTabsOrder).toEqual(['tab-1', 'tab-2']);
    });

    it('should filter non-string items from workspaceTabsOrder', () => {
      const state = {
        workspaceTabsOrder: ['tab-1', 123, 'tab-2', null],
      };
      const result = validateStoredState(state);
      expect(result.workspaceTabsOrder).toEqual(['tab-1', 'tab-2']);
    });

    it('should reject non-array workspaceTabsOrder', () => {
      const state = {
        workspaceTabsOrder: 'not-an-array',
      };
      const result = validateStoredState(state);
      expect(result.workspaceTabsOrder).toBeUndefined();
    });

    it('should validate theme values', () => {
      expect(validateStoredState({ theme: 'light' }).theme).toBe('light');
      expect(validateStoredState({ theme: 'dark' }).theme).toBe('dark');
      expect(validateStoredState({ theme: 'system' }).theme).toBe('system');
      expect(validateStoredState({ theme: 'invalid' }).theme).toBeUndefined();
    });

    it('should validate appMode values', () => {
      expect(validateStoredState({ appMode: 'work' }).appMode).toBe('work');
      expect(validateStoredState({ appMode: 'history' }).appMode).toBe('history');
      expect(validateStoredState({ appMode: 'settings' }).appMode).toBe('settings');
      expect(validateStoredState({ appMode: 'analysis' }).appMode).toBe('analysis');
      expect(validateStoredState({ appMode: 'invalid' }).appMode).toBeUndefined();
    });

    it('should validate boolean fields', () => {
      expect(validateStoredState({ sidebarCollapsed: true }).sidebarCollapsed).toBe(true);
      expect(validateStoredState({ enableTabNotifications: false }).enableTabNotifications).toBe(false);
      expect(validateStoredState({ sidebarCollapsed: 'yes' }).sidebarCollapsed).toBeUndefined();
    });
  });

  describe('migrate', () => {
    it('should migrate from version 0 to version 1 with all defaults', () => {
      const oldState = {
        workspaceTabs: [{ id: 'tab-1', title: 'Tab 1' }],
        workspaceActiveTabId: 'tab-1',
      };
      const result = migrate(oldState, 0);

      expect(result.workspaceTabs).toHaveLength(1);
      expect(result.workspaceActiveTabId).toBe('tab-1');
      // Should add workspaceTabsOrder based on existing tabs
      expect(result.workspaceTabsOrder).toEqual(['tab-1']);
      // Should fill in defaults
      expect(result.theme).toBe('light');
      expect(result.language).toBe('en');
    });

    it('should preserve existing workspaceTabsOrder during migration', () => {
      const oldState = {
        workspaceTabs: [
          { id: 'tab-1', title: 'Tab 1' },
          { id: 'tab-2', title: 'Tab 2' },
        ],
        workspaceTabsOrder: ['tab-2', 'tab-1'],
      };
      const result = migrate(oldState, 0);

      expect(result.workspaceTabsOrder).toEqual(['tab-2', 'tab-1']);
    });

    it('should handle empty state during migration', () => {
      const result = migrate({}, 0);

      expect(result.workspaceTabs).toEqual([]);
      expect(result.workspaceActiveTabId).toBe('');
      expect(result.workspaceTabsOrder).toEqual([]);
      expect(result.theme).toBe('light');
    });

    it('should handle corrupted state during migration', () => {
      const corruptedState = {
        workspaceTabs: null,
        workspaceActiveTabId: 123,
        workspaceTabsOrder: 'invalid',
      };
      const result = migrate(corruptedState, 0);

      // Should use defaults for corrupted values
      expect(result.workspaceTabs).toEqual([]);
      expect(result.workspaceActiveTabId).toBe('');
      expect(result.workspaceTabsOrder).toEqual([]);
    });

    it('should preserve valid fields and fix invalid ones', () => {
      const mixedState = {
        workspaceTabs: [{ id: 'tab-1', title: 'Tab 1' }],
        workspaceActiveTabId: 'tab-1',
        theme: 'dark',
        language: 'zh',
        sidebarCollapsed: true,
      };
      const result = migrate(mixedState, 0);

      expect(result.workspaceTabs).toHaveLength(1);
      expect(result.workspaceActiveTabId).toBe('tab-1');
      expect(result.theme).toBe('dark');
      expect(result.language).toBe('zh');
      expect(result.sidebarCollapsed).toBe(true);
      // Added defaults
      expect(result.workspaceTabsOrder).toEqual(['tab-1']);
    });
  });

  describe('Integration: localStorage rehydration', () => {
    it('should handle null workspaceTabsOrder', () => {
      // Store corrupted data
      const corruptedData = {
        state: {
          workspaceTabs: [],
          workspaceActiveTabId: '',
          workspaceTabsOrder: null, // Invalid: should be array
        },
        version: 1,
      };
      localStorage.setItem('open-ace-store', JSON.stringify(corruptedData));

      // Validate through migrate function
      const stored = localStorage.getItem('open-ace-store');
      if (stored) {
        const parsed = JSON.parse(stored);
        const result = migrate(parsed.state, parsed.version);
        expect(result.workspaceTabsOrder).toEqual([]);
      }
    });

    it('should handle object instead of array for workspaceTabsOrder', () => {
      const corruptedData = {
        state: {
          workspaceTabs: [],
          workspaceActiveTabId: '',
          workspaceTabsOrder: { invalid: 'object' }, // Invalid: should be array
        },
        version: 1,
      };
      localStorage.setItem('open-ace-store', JSON.stringify(corruptedData));

      const stored = localStorage.getItem('open-ace-store');
      if (stored) {
        const parsed = JSON.parse(stored);
        const result = migrate(parsed.state, parsed.version);
        expect(result.workspaceTabsOrder).toEqual([]);
      }
    });

    it('should handle non-string workspaceActiveTabId', () => {
      const corruptedData = {
        state: {
          workspaceTabs: [],
          workspaceActiveTabId: 123, // Invalid: should be string
          workspaceTabsOrder: [],
        },
        version: 1,
      };
      localStorage.setItem('open-ace-store', JSON.stringify(corruptedData));

      const stored = localStorage.getItem('open-ace-store');
      if (stored) {
        const parsed = JSON.parse(stored);
        const result = migrate(parsed.state, parsed.version);
        expect(result.workspaceActiveTabId).toBe('');
      }
    });

    it('should handle invalid workspaceTab items', () => {
      const corruptedData = {
        state: {
          workspaceTabs: [
            { id: 'valid-tab', title: 'Valid Tab' },
            { invalid: 'tab' }, // Missing required id field
            null, // Invalid: null
            'invalid', // Invalid: string
          ],
          workspaceActiveTabId: '',
          workspaceTabsOrder: [],
        },
        version: 1,
      };
      localStorage.setItem('open-ace-store', JSON.stringify(corruptedData));

      const stored = localStorage.getItem('open-ace-store');
      if (stored) {
        const parsed = JSON.parse(stored);
        const result = migrate(parsed.state, parsed.version);
        expect(result.workspaceTabs).toHaveLength(1);
        expect(result.workspaceTabs[0].id).toBe('valid-tab');
      }
    });

    it('should handle missing workspaceTabsOrder from old version', () => {
      // Simulate old version store without workspaceTabsOrder
      const oldData = {
        state: {
          workspaceTabs: [{ id: 'tab-1', title: 'Tab 1' }],
          workspaceActiveTabId: 'tab-1',
          // workspaceTabsOrder is missing
        },
        version: 0, // Old version
      };
      localStorage.setItem('open-ace-store', JSON.stringify(oldData));

      const stored = localStorage.getItem('open-ace-store');
      if (stored) {
        const parsed = JSON.parse(stored);
        const result = migrate(parsed.state, parsed.version);
        // Migration should add workspaceTabsOrder based on existing tabs
        expect(result.workspaceTabsOrder).toEqual(['tab-1']);
      }
    });

    it('should handle corrupted JSON in localStorage', () => {
      // Store completely corrupted JSON
      localStorage.setItem('open-ace-store', '{invalid json');

      const stored = localStorage.getItem('open-ace-store');
      if (stored) {
        expect(() => {
          try {
            JSON.parse(stored);
          } catch (e) {
            // Expected to throw
            expect(e).toBeInstanceOf(SyntaxError);
          }
        }).not.toThrow();
      }
    });
  });
});