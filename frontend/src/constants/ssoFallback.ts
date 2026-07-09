/**
 * SSO Fallback Constants
 *
 * This file contains fallback constants for SSO providers when API is unavailable.
 *
 * IMPORTANT: This file must be kept in sync with backend PROVIDER_CONFIGS
 * in app/modules/sso/provider.py. When adding or modifying providers in the
 * backend, update this file accordingly.
 */

import type { PredefinedProvider } from '@/api';

/**
 * Default icon based on provider type
 */
export const DEFAULT_ICONS: Record<string, string> = {
  oauth2: 'bi-key',
  oidc: 'bi-shield-lock',
};

/**
 * Icon mapping for predefined providers
 * Maps provider name to Bootstrap Icons class
 */
export const PROVIDER_ICONS: Record<string, string> = {
  google: 'bi-google',
  microsoft: 'bi-microsoft',
  github: 'bi-github',
  okta: 'bi-shield-lock',
  auth0: 'bi-shield-lock',
};

/**
 * Fallback predefined providers list
 * Used when API is unavailable or returns empty/invalid data
 *
 * NOTE: This list must match PROVIDER_CONFIGS in app/modules/sso/provider.py
 */
export const FALLBACK_PREDEFINED_PROVIDERS: PredefinedProvider[] = [
  {
    name: 'google',
    type: 'oidc',
    display_name: 'Google',
    icon: 'bi-google',
  },
  {
    name: 'microsoft',
    type: 'oidc',
    display_name: 'Microsoft',
    icon: 'bi-microsoft',
  },
  {
    name: 'github',
    type: 'oauth2',
    display_name: 'GitHub',
    icon: 'bi-github',
  },
  {
    name: 'okta',
    type: 'oidc',
    display_name: 'Okta',
    icon: 'bi-shield-lock',
  },
  {
    name: 'auth0',
    type: 'oidc',
    display_name: 'Auth0',
    icon: 'bi-shield-lock',
  },
];

/**
 * Get icon for a provider by name
 * Priority: explicit mapping > type-based default
 *
 * @param name - Provider name (e.g., 'google', 'microsoft')
 * @param type - Provider type ('oauth2' or 'oidc'), used for fallback
 * @returns Bootstrap Icons class name
 */
export function getProviderIcon(name: string, type?: string): string {
  const normalizedName = name.toLowerCase();

  // Check explicit mapping first
  if (PROVIDER_ICONS[normalizedName]) {
    return PROVIDER_ICONS[normalizedName];
  }

  // Fall back to type-based default
  if (type && DEFAULT_ICONS[type]) {
    return DEFAULT_ICONS[type];
  }

  // Ultimate fallback
  return 'bi-key';
}

/**
 * Validate a predefined provider object
 * Returns null if invalid, otherwise returns validated object
 *
 * @param provider - Unknown provider object from API
 * @returns Validated PredefinedProvider or null
 */
export function validatePredefinedProvider(provider: unknown): PredefinedProvider | null {
  if (!provider || typeof provider !== 'object') {
    return null;
  }

  const p = provider as Record<string, unknown>;

  // name is required and must be a non-empty string
  if (typeof p.name !== 'string' || !p.name.trim()) {
    return null;
  }

  // type must be valid if provided
  const validTypes = ['oauth2', 'oidc'];
  const type = typeof p.type === 'string' && validTypes.includes(p.type) ? p.type : 'oidc';

  // display_name fallback to name
  const displayName =
    typeof p.display_name === 'string' && p.display_name.trim() ? p.display_name : p.name;

  // icon is optional, must be string if provided
  const icon = typeof p.icon === 'string' && p.icon.trim() ? p.icon : undefined;

  return {
    name: p.name,
    type: type as 'oauth2' | 'oidc',
    display_name: displayName,
    icon,
  };
}

/**
 * Validate an array of predefined providers
 * Filters out invalid entries, returns fallback if all invalid
 *
 * @param providers - Array of unknown provider objects
 * @returns Array of validated PredefinedProvider
 */
export function validatePredefinedProviders(providers: unknown[]): PredefinedProvider[] {
  if (!Array.isArray(providers)) {
    return FALLBACK_PREDEFINED_PROVIDERS;
  }

  const validated = providers
    .map(validatePredefinedProvider)
    .filter((p): p is PredefinedProvider => p !== null);

  // If all providers are invalid, return fallback
  if (validated.length === 0) {
    return FALLBACK_PREDEFINED_PROVIDERS;
  }

  return validated;
}

/**
 * Sort providers by display name alphabetically
 *
 * @param providers - Array of providers to sort
 * @returns Sorted array (does not mutate original)
 */
export function sortProvidersByName(providers: PredefinedProvider[]): PredefinedProvider[] {
  return [...providers].sort((a, b) => a.display_name.localeCompare(b.display_name));
}
