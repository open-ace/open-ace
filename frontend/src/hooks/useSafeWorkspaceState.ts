/**
 * useSafeWorkspaceState - Centralized validation for workspace state
 * Issue #2953: Defensive validation to prevent runtime errors during session restoration
 *
 * This hook provides safe, validated values for workspace state, handling:
 * - Invalid localStorage data (null, objects, strings instead of arrays)
 * - Missing fields from older versions
 * - Type mismatches
 */

import { useMemo } from 'react';
import {
  useWorkspaceTabs,
  useWorkspaceActiveTabId,
  useWorkspaceTabsOrder,
  type WorkspaceTab,
} from '@/store';

/**
 * Validated and safe workspace state
 */
interface SafeWorkspaceState {
  /** Validated tabs array (never null, always an array) */
  tabs: WorkspaceTab[];
  /** Validated tabs order (never null, always a string array, IDs validated against tabs) */
  tabsOrder: string[];
  /** Validated active tab ID (never null, always a string) */
  activeTabId: string;
}

/**
 * Hook that provides validated workspace state values.
 * All values are guaranteed to be valid types, with invalid data filtered out.
 * Issue #2953: tabsOrder is additionally validated to ensure IDs exist in tabs
 *
 * @returns SafeWorkspaceState - Validated workspace state
 */
export const useSafeWorkspaceState = (): SafeWorkspaceState => {
  const storedTabs = useWorkspaceTabs();
  const storedActiveTabId = useWorkspaceActiveTabId();
  const storedTabsOrder = useWorkspaceTabsOrder();

  return useMemo(() => {
    // Validate storedTabs - must be array with valid items
    const safeTabs: WorkspaceTab[] = Array.isArray(storedTabs)
      ? storedTabs.filter((tab) => {
          if (typeof tab !== 'object' || tab === null) {
            console.warn('[useSafeWorkspaceState] Invalid tab item removed:', tab);
            return false;
          }
          if (typeof tab.id !== 'string') {
            console.warn('[useSafeWorkspaceState] Tab missing valid id:', tab);
            return false;
          }
          return true;
        })
      : [];

    // Validate storedActiveTabId - must be string
    const safeActiveTabId: string = typeof storedActiveTabId === 'string' ? storedActiveTabId : '';

    // Validate storedTabsOrder - must be array of strings
    const validTabsOrder: string[] = Array.isArray(storedTabsOrder)
      ? storedTabsOrder.filter((id) => {
          if (typeof id !== 'string') {
            console.warn('[useSafeWorkspaceState] Invalid tabsOrder item removed:', id);
            return false;
          }
          return true;
        })
      : [];

    // Issue #2953: Additionally validate that tabsOrder IDs exist in tabs
    const tabIds = new Set(safeTabs.map((t) => t.id));
    const safeTabsOrder = validTabsOrder.filter((id) => {
      if (!tabIds.has(id)) {
        console.warn('[useSafeWorkspaceState] Tab ID in order not found in tabs:', id);
        return false;
      }
      return true;
    });

    return {
      tabs: safeTabs,
      tabsOrder: safeTabsOrder,
      activeTabId: safeActiveTabId,
    };
  }, [storedTabs, storedActiveTabId, storedTabsOrder]);
};

/**
 * Validate if an active tab ID is valid for a given set of tabs
 *
 * @param activeTabId - The active tab ID to validate
 * @param tabs - Array of tabs to check against
 * @returns The valid active tab ID, or first tab's ID if invalid, or empty string if no tabs
 */
export const validateActiveTabId = (activeTabId: string, tabs: WorkspaceTab[]): string => {
  if (tabs.length === 0) return '';
  if (tabs.find((t) => t.id === activeTabId)) return activeTabId;
  // Fall back to first tab
  return tabs[0]?.id ?? '';
};

/**
 * Filter tabsOrder to only include IDs that exist in the tabs array
 * Issue #2953: Ensure orderedTabs doesn't reference non-existent tabs
 *
 * @param tabsOrder - The tabs order array to validate
 * @param tabs - Array of tabs to check against
 * @returns Validated tabs order
 */
export const validateTabsOrder = (tabsOrder: string[], tabs: WorkspaceTab[]): string[] => {
  const tabIds = new Set(tabs.map((t) => t.id));
  return tabsOrder.filter((id) => {
    if (!tabIds.has(id)) {
      console.warn('[validateTabsOrder] Tab ID in order not found in tabs:', id);
      return false;
    }
    return true;
  });
};

export default useSafeWorkspaceState;
