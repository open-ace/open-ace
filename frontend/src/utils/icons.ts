/**
 * Icon Utilities
 *
 * Shared icon mapping functions for UI components.
 */

/**
 * SSO Provider icon mapping
 * Maps provider name to Bootstrap Icons class
 */
const PROVIDER_ICONS: Record<string, string> = {
  google: 'bi-google',
  microsoft: 'bi-microsoft',
  github: 'bi-github',
  okta: 'bi-shield-lock',
  auth0: 'bi-shield-check',
};

/**
 * Default icon for unknown providers
 */
const DEFAULT_PROVIDER_ICON = 'bi-key';

/**
 * Get Bootstrap Icons class for an SSO provider
 *
 * @param name - Provider name (e.g., 'google', 'microsoft', 'github')
 * @returns Bootstrap Icons class string (e.g., 'bi-google')
 *
 * @example
 * getProviderIcon('google') // returns 'bi-google'
 * getProviderIcon('unknown') // returns 'bi-key'
 */
export function getProviderIcon(name: string): string {
  return PROVIDER_ICONS[name.toLowerCase()] || DEFAULT_PROVIDER_ICON;
}

/**
 * Check if a provider has a predefined icon
 *
 * @param name - Provider name
 * @returns true if provider has a predefined icon
 */
export function hasProviderIcon(name: string): boolean {
  return Object.prototype.hasOwnProperty.call(PROVIDER_ICONS, name.toLowerCase());
}

/**
 * Get all supported provider names with icons
 *
 * @returns Array of provider names
 */
export function getSupportedProviders(): string[] {
  return Object.keys(PROVIDER_ICONS);
}
