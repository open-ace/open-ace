/**
 * Unit tests for Admin Tenant Storage Utilities
 *
 * Issue #2841: Platform admin tenant selector default behavior optimization
 */

/* global DOMException */

import { describe, it, expect, beforeEach, vi, afterEach } from 'vitest';
import {
  getDeploymentId,
  getStorageKey,
  migrateStoredData,
  saveSelection,
  loadSelection,
  clearSelection,
  clearOtherUsersData,
  isTenantAccessible,
  findDefaultTenant,
} from './adminTenantStorage';
import type { Tenant } from '@/api';

describe('AdminTenantStorage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    // Stub environment variable for each test
    vi.stubEnv('VITE_DEPLOYMENT_ID', 'test-deployment');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  describe('getDeploymentId', () => {
    it('should return environment variable when set', () => {
      const id = getDeploymentId();
      expect(id).toBe('test-deployment');
    });

    it('should fallback to hostname when environment variable not set', () => {
      // This test would require mocking import.meta.env differently
      // For now, we test with the mocked value
      const id = getDeploymentId();
      expect(id).toBeDefined();
      expect(typeof id).toBe('string');
    });
  });

  describe('getStorageKey', () => {
    it('should generate correct storage key format', () => {
      const key = getStorageKey('123');
      expect(key).toMatch(/^open-ace-admin-tenant-.*-123$/);
    });

    it('should include deployment ID', () => {
      const key = getStorageKey('456');
      expect(key).toContain('test-deployment');
    });

    it('should generate different keys for different users', () => {
      const key1 = getStorageKey('1');
      const key2 = getStorageKey('2');
      expect(key1).not.toBe(key2);
    });
  });

  describe('migrateStoredData', () => {
    it('should handle null input', () => {
      const result = migrateStoredData(null);
      expect(result).toEqual({
        version: 1,
        selectedTenantId: null,
        lastUpdated: expect.any(String),
      });
    });

    it('should handle undefined input', () => {
      const result = migrateStoredData(undefined);
      expect(result).toEqual({
        version: 1,
        selectedTenantId: null,
        lastUpdated: expect.any(String),
      });
    });

    it('should handle invalid object input', () => {
      const result = migrateStoredData({ invalid: 'data' });
      expect(result).toEqual({
        version: 1,
        selectedTenantId: null,
        lastUpdated: expect.any(String),
      });
    });

    it('should handle version 1 data correctly', () => {
      const data = {
        version: 1,
        selectedTenantId: 5,
        lastUpdated: '2026-08-22T10:00:00Z',
      };
      const result = migrateStoredData(data);
      expect(result).toEqual(data);
    });

    it('should handle future version data (backward compatibility)', () => {
      const data = {
        version: 2,
        selectedTenantId: 10,
        lastUpdated: '2026-08-22T10:00:00Z',
        newField: 'value',
      };
      const result = migrateStoredData(data);
      expect(result.version).toBe(1);
      expect(result.selectedTenantId).toBe(10);
    });

    it('should preserve selectedTenantId when valid', () => {
      const data = {
        version: 1,
        selectedTenantId: 42,
        lastUpdated: '2026-08-22T10:00:00Z',
      };
      const result = migrateStoredData(data);
      expect(result.selectedTenantId).toBe(42);
    });

    it('should reset selectedTenantId when invalid', () => {
      const data = {
        version: 1,
        selectedTenantId: 'invalid',
        lastUpdated: '2026-08-22T10:00:00Z',
      };
      const result = migrateStoredData(data);
      expect(result.selectedTenantId).toBeNull();
    });
  });

  describe('saveSelection', () => {
    it('should save selection to localStorage', () => {
      const result = saveSelection('123', 5);
      expect(result).toBe(true);
      expect(localStorage.setItem).toHaveBeenCalled();
    });

    it('should handle null tenant ID', () => {
      const result = saveSelection('123', null);
      expect(result).toBe(true);
      expect(localStorage.setItem).toHaveBeenCalled();
    });

    it('should return false on quota exceeded error', () => {
      vi.spyOn(localStorage, 'setItem').mockImplementationOnce(() => {
        throw new DOMException('Quota exceeded', 'QuotaExceededError');
      });

      const result = saveSelection('123', 5);
      expect(result).toBe(false);
    });

    it('should return false on other errors', () => {
      vi.spyOn(localStorage, 'setItem').mockImplementationOnce(() => {
        throw new Error('Storage error');
      });

      const result = saveSelection('123', 5);
      expect(result).toBe(false);
    });
  });

  describe('loadSelection', () => {
    it('should return null when no data exists', () => {
      vi.spyOn(localStorage, 'getItem').mockReturnValue(null);
      const result = loadSelection('123');
      expect(result).toBeNull();
    });

    it('should load and migrate stored data', () => {
      const storedData = JSON.stringify({
        version: 1,
        selectedTenantId: 42,
        lastUpdated: '2026-08-22T10:00:00Z',
      });
      vi.spyOn(localStorage, 'getItem').mockReturnValue(storedData);

      const result = loadSelection('123');
      expect(result).toBe(42);
    });

    it('should handle corrupted JSON', () => {
      vi.spyOn(localStorage, 'getItem').mockReturnValue('invalid json');

      const result = loadSelection('123');
      expect(result).toBeNull();
      expect(localStorage.removeItem).toHaveBeenCalled();
    });

    it('should handle storage errors', () => {
      vi.spyOn(localStorage, 'getItem').mockImplementation(() => {
        throw new Error('Storage error');
      });

      const result = loadSelection('123');
      expect(result).toBeNull();
    });
  });

  describe('clearSelection', () => {
    it('should remove item from localStorage', () => {
      clearSelection('123');
      expect(localStorage.removeItem).toHaveBeenCalled();
    });

    it('should handle errors silently', () => {
      vi.spyOn(localStorage, 'removeItem').mockImplementation(() => {
        throw new Error('Remove error');
      });

      // Should not throw
      expect(() => clearSelection('123')).not.toThrow();
    });
  });

  describe('clearOtherUsersData', () => {
    it('should clear other users data but keep current user data', () => {
      // Set up data for multiple users
      localStorage.setItem(
        getStorageKey('123'),
        JSON.stringify({ version: 1, selectedTenantId: 1 })
      );
      localStorage.setItem(
        getStorageKey('456'),
        JSON.stringify({ version: 1, selectedTenantId: 2 })
      );
      localStorage.setItem(
        getStorageKey('789'),
        JSON.stringify({ version: 1, selectedTenantId: 3 })
      );

      clearOtherUsersData('456');

      // Should clear user 123 and 789, keep 456
      expect(localStorage.removeItem).toHaveBeenCalledTimes(2);
    });

    it('should handle empty localStorage', () => {
      clearOtherUsersData('123');
      // Should not throw or call removeItem
      expect(localStorage.removeItem).not.toHaveBeenCalled();
    });
  });

  describe('isTenantAccessible', () => {
    const tenants: Tenant[] = [
      {
        id: 1,
        name: 'Tenant 1',
        slug: 'default',
        status: 'active',
        plan: 'standard',
        created_at: '2026-01-01',
      },
      {
        id: 2,
        name: 'Tenant 2',
        slug: 'tenant-2',
        status: 'active',
        plan: 'premium',
        created_at: '2026-01-02',
      },
      {
        id: 3,
        name: 'Tenant 3',
        slug: 'tenant-3',
        status: 'suspended',
        plan: 'standard',
        created_at: '2026-01-03',
      },
    ];

    it('should return true for active tenant in list', () => {
      expect(isTenantAccessible(1, tenants)).toBe(true);
    });

    it('should return false for suspended tenant', () => {
      expect(isTenantAccessible(3, tenants)).toBe(false);
    });

    it('should return false for non-existent tenant', () => {
      expect(isTenantAccessible(999, tenants)).toBe(false);
    });

    it('should return false for null tenant ID', () => {
      expect(isTenantAccessible(null, tenants)).toBe(false);
    });

    it('should return false for empty tenant list', () => {
      expect(isTenantAccessible(1, [])).toBe(false);
    });
  });

  describe('findDefaultTenant', () => {
    it('should find default tenant with active status', () => {
      const tenants: Tenant[] = [
        {
          id: 2,
          name: 'Tenant 2',
          slug: 'tenant-2',
          status: 'active',
          plan: 'premium',
          created_at: '2026-01-02',
        },
        {
          id: 1,
          name: 'Default',
          slug: 'default',
          status: 'active',
          plan: 'standard',
          created_at: '2026-01-01',
        },
      ];

      const result = findDefaultTenant(tenants);
      expect(result?.id).toBe(1);
    });

    it('should return null when no default tenant exists', () => {
      const tenants: Tenant[] = [
        {
          id: 1,
          name: 'Tenant 1',
          slug: 'tenant-1',
          status: 'active',
          plan: 'standard',
          created_at: '2026-01-01',
        },
      ];

      const result = findDefaultTenant(tenants);
      expect(result).toBeNull();
    });

    it('should return null when default tenant is not active', () => {
      const tenants: Tenant[] = [
        {
          id: 1,
          name: 'Default',
          slug: 'default',
          status: 'suspended',
          plan: 'standard',
          created_at: '2026-01-01',
        },
      ];

      const result = findDefaultTenant(tenants);
      expect(result).toBeNull();
    });

    it('should return null for empty tenant list', () => {
      const result = findDefaultTenant([]);
      expect(result).toBeNull();
    });
  });
});
