/**
 * FeatureRouteGuard - Generic feature flag route guard component
 *
 * Protects routes based on feature flag status. Redirects to a specified path
 * when the feature is disabled, while waiting for config to load first.
 *
 * Usage:
 * ```tsx
 * <FeatureRouteGuard
 *   enabled={modelGatewayEnabled}
 *   redirectPath="/manage/dashboard"
 * >
 *   <ModelGatewayConfig />
 * </FeatureRouteGuard>
 * ```
 */

import React from 'react';
import { Navigate } from 'react-router-dom';
import { useAppStore } from '@/store';
import { PageSkeleton } from './DashboardSkeleton';

interface FeatureRouteGuardProps {
  /** Whether the feature is enabled (from store) */
  enabled: boolean;
  /** Path to redirect to when feature is disabled */
  redirectPath: string;
  /** Children to render when feature is enabled */
  children: React.ReactNode;
}

/**
 * FeatureRouteGuard component
 *
 * Waits for feature flags to load (configLoaded === true), then checks if
 * the feature is enabled. If disabled, redirects to the specified path.
 * If enabled, renders the children.
 *
 * The guard uses replace navigation to prevent browser history issues,
 * ensuring users cannot use the back button to bypass the guard.
 */
export const FeatureRouteGuard: React.FC<FeatureRouteGuardProps> = ({
  enabled,
  redirectPath,
  children,
}) => {
  const configLoaded = useAppStore((state) => state.configLoaded);

  // Wait for feature flags to load before making any decision
  if (!configLoaded) {
    return <PageSkeleton />;
  }

  // Feature is disabled - redirect to safe path
  if (!enabled) {
    return <Navigate to={redirectPath} replace />;
  }

  // Feature is enabled - render children
  return <>{children}</>;
};