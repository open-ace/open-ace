/**
 * Permission utilities for role-based access control
 */

import type { User } from '@/types';

/**
 * Check if user can manage all tenants (global admin)
 * @param user - Current user
 * @returns true if user has admin role
 */
export function canManageAllTenants(user: User | null | undefined): boolean {
  return user?.role === 'admin';
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
