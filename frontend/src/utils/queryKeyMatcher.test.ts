/**
 * Query Key Matcher Tests
 */

import {
  exactMatch,
  prefixMatch,
  shouldExclude,
  matchQueryKey,
  filterQueryKeys,
  createMatcherConfig,
} from './queryKeyMatcher';
import type { QueryKeyMatchMode, QueryKeyMatcherConfig } from './queryKeyMatcher';

describe('queryKeyMatcher', () => {
  describe('exactMatch', () => {
    it('should match identical keys', () => {
      const key1 = ['users'];
      const key2 = ['users'];
      expect(exactMatch(key1, key2)).toBe(true);
    });

    it('should match keys with identical objects', () => {
      const key1 = ['dashboard', 'today', { tool: 'qwen', host: 'localhost' }];
      const key2 = ['dashboard', 'today', { tool: 'qwen', host: 'localhost' }];
      expect(exactMatch(key1, key2)).toBe(true);
    });

    it('should not match keys with different lengths', () => {
      const key1 = ['users'];
      const key2 = ['users', 'list'];
      expect(exactMatch(key1, key2)).toBe(false);
    });

    it('should not match keys with different values', () => {
      const key1 = ['users'];
      const key2 = ['tenants'];
      expect(exactMatch(key1, key2)).toBe(false);
    });

    it('should not match keys with different objects', () => {
      const key1 = ['dashboard', 'today', { tool: 'qwen' }];
      const key2 = ['dashboard', 'today', { tool: 'claude' }];
      expect(exactMatch(key1, key2)).toBe(false);
    });
  });

  describe('prefixMatch', () => {
    it('should match when key starts with prefix', () => {
      const prefix = ['dashboard'];
      const key = ['dashboard', 'today'];
      expect(prefixMatch(prefix, key)).toBe(true);
    });

    it('should match when key equals prefix', () => {
      const prefix = ['users'];
      const key = ['users'];
      expect(prefixMatch(prefix, key)).toBe(true);
    });

    it('should not match when key is shorter than prefix', () => {
      const prefix = ['dashboard', 'today'];
      const key = ['dashboard'];
      expect(prefixMatch(prefix, key)).toBe(false);
    });

    it('should not match when prefix differs', () => {
      const prefix = ['dashboard'];
      const key = ['messages', 'list'];
      expect(prefixMatch(prefix, key)).toBe(false);
    });

    it('should match prefix with objects', () => {
      const prefix = ['dashboard', 'today', { tool: 'qwen' }];
      const key = ['dashboard', 'today', { tool: 'qwen' }, 'extra'];
      expect(prefixMatch(prefix, key)).toBe(true);
    });
  });

  describe('shouldExclude', () => {
    it('should exclude exact match', () => {
      const key = ['remote', 'sessions'];
      const excludeList = [['remote', 'sessions']];
      expect(shouldExclude(key, excludeList)).toBe(true);
    });

    it('should exclude prefix match', () => {
      const key = ['remote', 'sessions', { machineId: '123' }];
      const excludeList = [['remote', 'sessions']];
      expect(shouldExclude(key, excludeList)).toBe(true);
    });

    it('should not exclude when not in list', () => {
      const key = ['dashboard', 'today'];
      const excludeList = [['remote', 'sessions']];
      expect(shouldExclude(key, excludeList)).toBe(false);
    });

    it('should handle empty exclude list', () => {
      const key = ['users'];
      const excludeList: any[] = [];
      expect(shouldExclude(key, excludeList)).toBe(false);
    });
  });

  describe('matchQueryKey', () => {
    it('should match in exact mode', () => {
      const key = ['users'];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['users']],
        mode: 'exact',
      };
      expect(matchQueryKey(key, matcher)).toBe(true);
    });

    it('should match in prefix mode', () => {
      const key = ['dashboard', 'today'];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['dashboard']],
        mode: 'prefix',
      };
      expect(matchQueryKey(key, matcher)).toBe(true);
    });

    it('should not match excluded key', () => {
      const key = ['remote', 'sessions'];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['remote']],
        mode: 'prefix',
        exclude: [['remote', 'sessions']],
      };
      expect(matchQueryKey(key, matcher)).toBe(false);
    });

    it('should match multiple keys', () => {
      const key = ['messages', 'list'];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['dashboard'], ['messages']],
        mode: 'prefix',
      };
      expect(matchQueryKey(key, matcher)).toBe(true);
    });

    it('should not match when no keys match', () => {
      const key = ['security', 'logs'];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['dashboard'], ['messages']],
        mode: 'prefix',
      };
      expect(matchQueryKey(key, matcher)).toBe(false);
    });
  });

  describe('filterQueryKeys', () => {
    it('should filter keys by matcher', () => {
      const keys = [
        ['dashboard', 'today'],
        ['messages', 'list'],
        ['remote', 'sessions'],
      ];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['dashboard'], ['messages']],
        mode: 'prefix',
        exclude: [['remote', 'sessions']],
      };
      const filtered = filterQueryKeys(keys, matcher);
      expect(filtered).toHaveLength(2);
      expect(filtered[0]).toEqual(['dashboard', 'today']);
      expect(filtered[1]).toEqual(['messages', 'list']);
    });

    it('should return empty array when no matches', () => {
      const keys = [
        ['remote', 'sessions'],
        ['users'],
      ];
      const matcher: QueryKeyMatcherConfig = {
        keys: [['dashboard']],
        mode: 'prefix',
      };
      const filtered = filterQueryKeys(keys, matcher);
      expect(filtered).toHaveLength(0);
    });
  });

  describe('createMatcherConfig', () => {
    it('should create config with defaults', () => {
      const keys = [['dashboard']];
      const config = createMatcherConfig(keys);
      expect(config.keys).toEqual(keys);
      expect(config.mode).toBe('prefix');
      expect(config.exclude).toBeUndefined();
    });

    it('should create config with custom values', () => {
      const keys = [['users']];
      const exclude = [['users', 'admin']];
      const config = createMatcherConfig(keys, 'exact', exclude);
      expect(config.keys).toEqual(keys);
      expect(config.mode).toBe('exact');
      expect(config.exclude).toEqual(exclude);
    });
  });
});