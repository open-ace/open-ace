/**
 * SSO Provider Constants
 *
 * Fallback configuration for predefined SSO providers.
 * Used when backend API does not return icon field.
 */

/**
 * SSO Provider configuration with icon and display name
 */
export interface SSOProviderConfig {
  icon: string;
  displayName: string;
}

/**
 * Predefined SSO provider configurations
 * Maps provider name to icon (Bootstrap Icons) and display name
 */
export const SSO_PROVIDER_CONFIG: Record<string, SSOProviderConfig> = {
  google: {
    icon: 'bi-google',
    displayName: 'Google',
  },
  microsoft: {
    icon: 'bi-microsoft',
    displayName: 'Microsoft',
  },
  github: {
    icon: 'bi-github',
    displayName: 'GitHub',
  },
  okta: {
    icon: 'bi-shield-lock',
    displayName: 'Okta',
  },
  auth0: {
    icon: 'bi-shield-check',
    displayName: 'Auth0',
  },
};

/**
 * Get provider icon from config
 * Returns fallback icon if provider not found
 */
export function getProviderIconFromConfig(name: string): string {
  const config = SSO_PROVIDER_CONFIG[name.toLowerCase()];
  return config?.icon || 'bi-key';
}

/**
 * Get provider display name from config
 * Returns capitalized name if provider not found
 */
export function getProviderDisplayName(name: string): string {
  const config = SSO_PROVIDER_CONFIG[name.toLowerCase()];
  if (config) {
    return config.displayName;
  }
  // Fallback: capitalize first letter
  return name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * Get provider config or fallback
 */
export function getProviderConfig(name: string): SSOProviderConfig | null {
  return SSO_PROVIDER_CONFIG[name.toLowerCase()] || null;
}
