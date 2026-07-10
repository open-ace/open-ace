/**
 * Tests for SSO Fallback Constants
 */

import { describe, it, expect } from 'vitest';
import {
  PROVIDER_ICONS,
  FALLBACK_PREDEFINED_PROVIDERS,
  getProviderIcon,
  validatePredefinedProvider,
  validatePredefinedProviders,
  sortProvidersByName,
} from './ssoFallback';

describe('SSO Fallback Constants', () => {
  describe('FALLBACK_PREDEFINED_PROVIDERS', () => {
    it('should contain all expected providers', () => {
      const providerNames = FALLBACK_PREDEFINED_PROVIDERS.map((p) => p.name);
      expect(providerNames).toContain('google');
      expect(providerNames).toContain('microsoft');
      expect(providerNames).toContain('github');
      expect(providerNames).toContain('okta');
      expect(providerNames).toContain('auth0');
    });

    it('should have 5 providers total', () => {
      expect(FALLBACK_PREDEFINED_PROVIDERS.length).toBe(5);
    });

    it('should have all required fields for each provider', () => {
      FALLBACK_PREDEFINED_PROVIDERS.forEach((provider) => {
        expect(provider.name).toBeTruthy();
        expect(provider.type).toMatch(/^(oauth2|oidc)$/);
        expect(provider.display_name).toBeTruthy();
        expect(provider.icon).toBeTruthy();
      });
    });

    it('should have auth0 icon defined', () => {
      const auth0 = FALLBACK_PREDEFINED_PROVIDERS.find((p) => p.name === 'auth0');
      expect(auth0).toBeDefined();
      expect(auth0?.icon).toBe('bi-shield-lock');
    });
  });

  describe('PROVIDER_ICONS', () => {
    it('should have icons for all fallback providers', () => {
      FALLBACK_PREDEFINED_PROVIDERS.forEach((provider) => {
        expect(PROVIDER_ICONS[provider.name]).toBe(provider.icon);
      });
    });
  });

  describe('getProviderIcon', () => {
    it('should return correct icon for known providers', () => {
      expect(getProviderIcon('google')).toBe('bi-google');
      expect(getProviderIcon('microsoft')).toBe('bi-microsoft');
      expect(getProviderIcon('github')).toBe('bi-github');
      expect(getProviderIcon('okta')).toBe('bi-shield-lock');
      expect(getProviderIcon('auth0')).toBe('bi-shield-lock');
    });

    it('should be case-insensitive', () => {
      expect(getProviderIcon('GOOGLE')).toBe('bi-google');
      expect(getProviderIcon('Microsoft')).toBe('bi-microsoft');
    });

    it('should return type-based default for unknown providers', () => {
      expect(getProviderIcon('unknown', 'oidc')).toBe('bi-shield-lock');
      expect(getProviderIcon('unknown', 'oauth2')).toBe('bi-key');
    });

    it('should return bi-key as ultimate fallback', () => {
      expect(getProviderIcon('unknown')).toBe('bi-key');
    });
  });

  describe('validatePredefinedProvider', () => {
    it('should validate a valid provider', () => {
      const provider = {
        name: 'test',
        type: 'oidc',
        display_name: 'Test Provider',
        icon: 'bi-test',
      };
      const result = validatePredefinedProvider(provider);
      expect(result).toEqual(provider);
    });

    it('should use default type for invalid type', () => {
      const provider = {
        name: 'test',
        type: 'invalid',
      };
      const result = validatePredefinedProvider(provider);
      expect(result?.type).toBe('oidc');
    });

    it('should use name as display_name fallback', () => {
      const provider = {
        name: 'test',
      };
      const result = validatePredefinedProvider(provider);
      expect(result?.display_name).toBe('test');
    });

    it('should return null for invalid name', () => {
      expect(validatePredefinedProvider({})).toBeNull();
      expect(validatePredefinedProvider({ name: '' })).toBeNull();
      expect(validatePredefinedProvider({ name: null })).toBeNull();
      expect(validatePredefinedProvider(null)).toBeNull();
      expect(validatePredefinedProvider(undefined)).toBeNull();
      expect(validatePredefinedProvider('string')).toBeNull();
    });

    it('should handle optional icon', () => {
      const provider = { name: 'test' };
      const result = validatePredefinedProvider(provider);
      expect(result?.icon).toBeUndefined();
    });

    it('should accept valid icon', () => {
      const provider = { name: 'test', icon: 'bi-test' };
      const result = validatePredefinedProvider(provider);
      expect(result?.icon).toBe('bi-test');
    });

    it('should reject non-string icon', () => {
      const provider = { name: 'test', icon: 123 };
      const result = validatePredefinedProvider(provider);
      expect(result?.icon).toBeUndefined();
    });
  });

  describe('validatePredefinedProviders', () => {
    it('should validate array of providers', () => {
      const providers = [
        { name: 'google', type: 'oidc' },
        { name: 'github', type: 'oauth2' },
      ];
      const result = validatePredefinedProviders(providers);
      expect(result.length).toBe(2);
      expect(result[0].name).toBe('google');
      expect(result[1].name).toBe('github');
    });

    it('should filter out invalid providers', () => {
      const providers = [
        { name: 'google', type: 'oidc' },
        { name: '', type: 'oauth2' }, // invalid
        { name: 'github', type: 'oauth2' },
      ];
      const result = validatePredefinedProviders(providers);
      expect(result.length).toBe(2);
    });

    it('should return fallback for empty array', () => {
      const result = validatePredefinedProviders([]);
      expect(result).toEqual(FALLBACK_PREDEFINED_PROVIDERS);
    });

    it('should return fallback for all-invalid array', () => {
      const providers = [{}, { name: '' }, null];
      const result = validatePredefinedProviders(providers);
      expect(result).toEqual(FALLBACK_PREDEFINED_PROVIDERS);
    });

    it('should return fallback for non-array input', () => {
      expect(validatePredefinedProviders(null as unknown)).toEqual(FALLBACK_PREDEFINED_PROVIDERS);
      expect(validatePredefinedProviders({} as unknown)).toEqual(FALLBACK_PREDEFINED_PROVIDERS);
    });
  });

  describe('sortProvidersByName', () => {
    it('should sort providers alphabetically by display_name', () => {
      const providers = [
        { name: 'z', type: 'oidc' as const, display_name: 'Z Provider' },
        { name: 'a', type: 'oidc' as const, display_name: 'A Provider' },
        { name: 'm', type: 'oidc' as const, display_name: 'M Provider' },
      ];
      const sorted = sortProvidersByName(providers);
      expect(sorted[0].name).toBe('a');
      expect(sorted[1].name).toBe('m');
      expect(sorted[2].name).toBe('z');
    });

    it('should not mutate original array', () => {
      const providers = [
        { name: 'z', type: 'oidc' as const, display_name: 'Z' },
        { name: 'a', type: 'oidc' as const, display_name: 'A' },
      ];
      sortProvidersByName(providers);
      expect(providers[0].name).toBe('z');
    });
  });
});
