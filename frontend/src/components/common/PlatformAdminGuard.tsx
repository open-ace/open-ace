/**
 * PlatformAdminGuard - Route guard for platform admin only routes
 *
 * Protects routes that require platform-level admin access.
 * Redirects to dashboard when accessed by tenant_admin or other non-platform-admin roles.
 *
 * Usage:
 * ```tsx
 * <Route
 *   path="tenants"
 *   element={
 *     <PlatformAdminGuard>
 *       <TenantManagement />
 *     </PlatformAdminGuard>
 *   }
 * />
 * ```
 */

import React, { useEffect } from 'react';
import { Navigate } from 'react-router-dom';
import { useAuth } from '@/hooks';
import { useToast } from '@/components/common';
import { canManageAllTenants } from '@/utils/permissions';
import { useAppStore } from '@/store';
import { t } from '@/i18n';

interface PlatformAdminGuardProps {
  /** Children to render when user has platform admin access */
  children: React.ReactNode;
}

/**
 * PlatformAdminGuard component
 *
 * Checks if the current user has platform admin access (admin or platform_admin role).
 * If not, redirects to /manage/dashboard and shows a toast notification.
 *
 * This guard is used for routes that manage global resources like tenant list,
 * which should only be accessible to platform administrators, not tenant admins.
 */
export const PlatformAdminGuard: React.FC<PlatformAdminGuardProps> = ({ children }) => {
  const { user } = useAuth();
  const toast = useToast();
  const language = useAppStore((state) => state.language);

  useEffect(() => {
    // Show toast only when redirecting (i.e., when access is denied)
    if (user && !canManageAllTenants(user)) {
      toast.error(t('platformAdminOnly', language));
    }
  }, [user, toast, language]);

  // Wait for auth to load
  if (!user) {
    return null;
  }

  // User doesn't have platform admin access - redirect
  if (!canManageAllTenants(user)) {
    return <Navigate to="/manage/dashboard" replace />;
  }

  // User has platform admin access - render children
  return <>{children}</>;
};
