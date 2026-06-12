/**
 * Query Key Matcher - Query key matching utilities
 *
 * Supports:
 * - Exact matching: ['users'] only matches ['users']
 * - Prefix matching: ['dashboard'] matches ['dashboard', 'stats'] and ['dashboard', 'charts']
 * - Exclusion rules: exclude specific query keys from matching
 */

import type { QueryKey } from '@tanstack/react-query';

/**
 * QueryKeyMatchMode - Matching mode for query keys
 */
export type QueryKeyMatchMode = 'exact' | 'prefix';

/**
 * QueryKeyMatcherConfig - Configuration for query key matching
 */
export interface QueryKeyMatcherConfig {
  keys: QueryKey[];
  mode: QueryKeyMatchMode;
  exclude?: QueryKey[]; // Query keys to exclude from matching
}

/**
 * Hash a query key to a string for comparison
 */
export function hashQueryKey(key: QueryKey): string {
  return JSON.stringify(key);
}

/**
 * Check if two query keys match exactly
 */
export function exactMatch(key1: QueryKey, key2: QueryKey): boolean {
  if (key1.length !== key2.length) {
    return false;
  }

  return key1.every((item, index) => {
    const item1 = item;
    const item2 = key2[index];
    // Compare primitives
    if (typeof item1 !== 'object' && typeof item2 !== 'object') {
      return item1 === item2;
    }
    // Compare objects by JSON serialization
    return JSON.stringify(item1) === JSON.stringify(item2);
  });
}

/**
 * Check if key2 starts with key1 (prefix matching)
 */
export function prefixMatch(prefix: QueryKey, key: QueryKey): boolean {
  if (prefix.length > key.length) {
    return false;
  }

  return prefix.every((item, index) => {
    const prefixItem = item;
    const keyItem = key[index];
    // Compare primitives
    if (typeof prefixItem !== 'object' && typeof keyItem !== 'object') {
      return prefixItem === keyItem;
    }
    // Compare objects by JSON serialization
    return JSON.stringify(prefixItem) === JSON.stringify(keyItem);
  });
}

/**
 * Check if a query key should be excluded based on exclusion rules
 */
export function shouldExclude(key: QueryKey, excludeList: QueryKey[]): boolean {
  return excludeList.some((excludeKey) => {
    // Check exact match first
    if (exactMatch(excludeKey, key)) {
      return true;
    }
    // Also check prefix match for exclusion (if key starts with exclude key)
    if (prefixMatch(excludeKey, key)) {
      return true;
    }
    return false;
  });
}

/**
 * Match a query key against a matcher configuration
 */
export function matchQueryKey(
  key: QueryKey,
  matcher: QueryKeyMatcherConfig
): boolean {
  // First check exclusion rules
  if (matcher.exclude && shouldExclude(key, matcher.exclude)) {
    return false;
  }

  // Then check matching rules
  return matcher.keys.some((matcherKey) => {
    if (matcher.mode === 'exact') {
      return exactMatch(matcherKey, key);
    } else {
      return prefixMatch(matcherKey, key);
    }
  });
}

/**
 * Filter a list of query keys based on a matcher configuration
 */
export function filterQueryKeys(
  keys: QueryKey[],
  matcher: QueryKeyMatcherConfig
): QueryKey[] {
  return keys.filter((key) => matchQueryKey(key, matcher));
}

/**
 * Create a matcher config from a simple key array
 * Convenience function for common use cases
 */
export function createMatcherConfig(
  keys: QueryKey[],
  mode: QueryKeyMatchMode = 'prefix',
  exclude?: QueryKey[]
): QueryKeyMatcherConfig {
  return {
    keys,
    mode,
    exclude,
  };
}
