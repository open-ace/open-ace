/**
 * Tests for permission utilities
 */

import { describe, it, expect } from 'vitest';
import {
  isAdminRole,
  isAdmin,
  canAccessManageMode,
  isPlatformAdmin,
  isTenantAdmin,
  canManageAllTenants,
  canManageTenant,
} from './permissions';

// Helper to create mock user
const createMockUser = (role: string, tenant_id?: number | null) => ({
  id: '1',
  username: 'testuser',
  role,
  tenant_id: tenant_id ?? null,
  must_change_password: false,
});

describe('permission utilities', () => {
  describe('isAdminRole', () => {
    it('should return true for admin roles', () => {
      expect(isAdminRole('admin')).toBe(true);
      expect(isAdminRole('platform_admin')).toBe(true);
      expect(isAdminRole('tenant_admin')).toBe(true);
    });

    it('should return false for non-admin roles', () => {
      expect(isAdminRole('user')).toBe(false);
      expect(isAdminRole('manager')).toBe(false);
      expect(isAdminRole(null)).toBe(false);
      expect(isAdminRole(undefined)).toBe(false);
    });
  });

  describe('isAdmin', () => {
    it('should return true for admin users', () => {
      expect(isAdmin(createMockUser('admin'))).toBe(true);
      expect(isAdmin(createMockUser('platform_admin'))).toBe(true);
      expect(isAdmin(createMockUser('tenant_admin'))).toBe(true);
    });

    it('should return false for non-admin users', () => {
      expect(isAdmin(createMockUser('user'))).toBe(false);
      expect(isAdmin(createMockUser('manager'))).toBe(false);
      expect(isAdmin(null)).toBe(false);
      expect(isAdmin(undefined)).toBe(false);
    });
  });

  describe('canAccessManageMode', () => {
    it('should return true for admin and manager roles', () => {
      expect(canAccessManageMode(createMockUser('admin'))).toBe(true);
      expect(canAccessManageMode(createMockUser('platform_admin'))).toBe(true);
      expect(canAccessManageMode(createMockUser('tenant_admin'))).toBe(true);
      expect(canAccessManageMode(createMockUser('manager'))).toBe(true);
    });

    it('should return false for regular users', () => {
      expect(canAccessManageMode(createMockUser('user'))).toBe(false);
      expect(canAccessManageMode(null)).toBe(false);
      expect(canAccessManageMode(undefined)).toBe(false);
    });
  });

  describe('isPlatformAdmin', () => {
    it('should return true only for platform_admin', () => {
      expect(isPlatformAdmin(createMockUser('platform_admin'))).toBe(true);
      expect(isPlatformAdmin(createMockUser('admin'))).toBe(false);
      expect(isPlatformAdmin(createMockUser('tenant_admin'))).toBe(false);
    });

    it('should return false for null/undefined', () => {
      expect(isPlatformAdmin(null)).toBe(false);
      expect(isPlatformAdmin(undefined)).toBe(false);
    });
  });

  describe('isTenantAdmin', () => {
    it('should return true for tenant_admin with tenant_id', () => {
      expect(isTenantAdmin(createMockUser('tenant_admin', 1))).toBe(true);
    });

    it('should return false for tenant_admin without tenant_id', () => {
      expect(isTenantAdmin(createMockUser('tenant_admin', null))).toBe(false);
      expect(isTenantAdmin(createMockUser('tenant_admin'))).toBe(false);
    });

    it('should return false for other roles', () => {
      expect(isTenantAdmin(createMockUser('admin'))).toBe(false);
      expect(isTenantAdmin(createMockUser('platform_admin'))).toBe(false);
    });
  });

  describe('canManageAllTenants', () => {
    it('should return true for platform_admin', () => {
      expect(canManageAllTenants(createMockUser('platform_admin'))).toBe(true);
    });

    it('should return true for admin (legacy compatibility)', () => {
      expect(canManageAllTenants(createMockUser('admin'))).toBe(true);
    });

    it('should return false for tenant_admin', () => {
      expect(canManageAllTenants(createMockUser('tenant_admin'))).toBe(false);
      expect(canManageAllTenants(createMockUser('tenant_admin', 1))).toBe(false);
    });

    it('should return false for manager', () => {
      expect(canManageAllTenants(createMockUser('manager'))).toBe(false);
    });

    it('should return false for regular user', () => {
      expect(canManageAllTenants(createMockUser('user'))).toBe(false);
    });

    it('should return false for null/undefined', () => {
      expect(canManageAllTenants(null)).toBe(false);
      expect(canManageAllTenants(undefined)).toBe(false);
    });
  });

  describe('canManageTenant', () => {
    it('should return true for platform_admin for any tenant', () => {
      expect(canManageTenant(createMockUser('platform_admin'), 1)).toBe(true);
      expect(canManageTenant(createMockUser('platform_admin'), 999)).toBe(true);
    });

    it('should return true for admin (legacy) for any tenant', () => {
      expect(canManageTenant(createMockUser('admin'), 1)).toBe(true);
      expect(canManageTenant(createMockUser('admin'), 999)).toBe(true);
    });

    it('should return true for tenant_admin for their own tenant', () => {
      expect(canManageTenant(createMockUser('tenant_admin', 1), 1)).toBe(true);
    });

    it('should return false for tenant_admin for other tenants', () => {
      expect(canManageTenant(createMockUser('tenant_admin', 1), 2)).toBe(false);
    });

    it('should return false for tenant_admin without tenant_id', () => {
      expect(canManageTenant(createMockUser('tenant_admin'), 1)).toBe(false);
    });

    it('should return false for manager', () => {
      expect(canManageTenant(createMockUser('manager'), 1)).toBe(false);
    });

    it('should return false for null/undefined user', () => {
      expect(canManageTenant(null, 1)).toBe(false);
      expect(canManageTenant(undefined, 1)).toBe(false);
    });
  });
});
