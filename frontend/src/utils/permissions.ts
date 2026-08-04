/**
 * Permission utilities for role-based access control
 */

import type { User } from '@/types';

/**
 * Check if user is an admin (any admin type)
 * Aligns with backend app/models/user.py:112-114
 * @param user - Current user
 * @returns true if user has any admin role
 */
export function isAdmin(user: User | null | undefined): boolean {
  return ['admin', 'platform_admin', 'tenant_admin'].includes(user?.role ?? '');
}

/**
 * Check if user is a platform admin
 * @param user - Current user
 * @returns true if user is platform admin
 */
export function isPlatformAdmin(user: User | null | undefined): boolean {
  return user?.role === 'platform_admin';
}

/**
 * Check if user is a tenant admin
 * @param user - Current user
 * @returns true if user is tenant admin with tenant_id
 */
export function isTenantAdmin(user: User | null | undefined): boolean {
  return user?.role === 'tenant_admin' && user?.tenant_id !== null;
}

/**
 * Check if user can manage all tenants (global admin)
 * @param user - Current user
 * @returns true if user has admin role
 * @deprecated Use isAdmin() instead for consistency with backend
 */
export function canManageAllTenants(user: User | null | undefined): boolean {
  return isAdmin(user);
}

/**
 * Check if user can manage a specific tenant
 * @param user - Current user
 * @param tenantId - Target tenant ID
 * @returns true if user can manage this tenant
 */
export function canManageTenant(user: User | null | undefined, tenantId: number): boolean {
  if (!user) return false;
  if (canManageAllTenants(user)) return true;
  return user.tenant_id === tenantId;
}
