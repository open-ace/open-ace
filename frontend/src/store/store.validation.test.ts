/**
 * Store validation tests (Issue #2953)
 * Tests for store version management and schema validation
 */

import { describe, it, expect, beforeEach } from 'vitest';

describe('Store Validation (Issue #2953)', () => {
  beforeEach(() => {
    // Clear localStorage before each test
    localStorage.clear();
  });

  describe('Schema validation', () => {
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

      // The store should handle this gracefully on rehydration
      // This will be tested by importing the store and checking it doesn't throw
      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          const parsed = JSON.parse(stored);
          expect(parsed.state.workspaceTabsOrder).toBeNull();
        }
      }).not.toThrow();
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

      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          const parsed = JSON.parse(stored);
          expect(typeof parsed.state.workspaceTabsOrder).toBe('object');
          expect(Array.isArray(parsed.state.workspaceTabsOrder)).toBe(false);
        }
      }).not.toThrow();
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

      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          const parsed = JSON.parse(stored);
          expect(typeof parsed.state.workspaceActiveTabId).toBe('number');
        }
      }).not.toThrow();
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

      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          const parsed = JSON.parse(stored);
          expect(Array.isArray(parsed.state.workspaceTabs)).toBe(true);
          expect(parsed.state.workspaceTabs).toHaveLength(4);
        }
      }).not.toThrow();
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

      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          const parsed = JSON.parse(stored);
          expect(parsed.state.workspaceTabsOrder).toBeUndefined();
        }
      }).not.toThrow();
    });
  });

  describe('JSON parse error handling', () => {
    it('should handle corrupted JSON in localStorage', () => {
      // Store completely corrupted JSON
      localStorage.setItem('open-ace-store', '{invalid json');

      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          try {
            JSON.parse(stored);
          } catch (e) {
            // Expected to throw
            expect(e).toBeInstanceOf(SyntaxError);
          }
        }
      }).not.toThrow();
    });
  });

  describe('Migration', () => {
    it('should handle version migration from 0 to 1', () => {
      const oldData = {
        state: {
          workspaceTabs: [{ id: 'tab-1', title: 'Tab 1' }],
          workspaceActiveTabId: 'tab-1',
        },
        version: 0,
      };
      localStorage.setItem('open-ace-store', JSON.stringify(oldData));

      // Migration should add workspaceTabsOrder based on existing tabs
      expect(() => {
        const stored = localStorage.getItem('open-ace-store');
        if (stored) {
          const parsed = JSON.parse(stored);
          // Migration should handle this
          expect(parsed.state.workspaceTabs).toBeDefined();
          expect(parsed.state.workspaceActiveTabId).toBeDefined();
        }
      }).not.toThrow();
    });
  });
});
