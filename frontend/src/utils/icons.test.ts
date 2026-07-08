/**
 * Tests for Icon Utilities
 */

import { describe, it, expect } from 'vitest';
import { getProviderIcon, hasProviderIcon, getSupportedProviders } from './icons';

describe('getProviderIcon', () => {
  it('should return correct icon for known providers', () => {
    expect(getProviderIcon('google')).toBe('bi-google');
    expect(getProviderIcon('microsoft')).toBe('bi-microsoft');
    expect(getProviderIcon('github')).toBe('bi-github');
    expect(getProviderIcon('okta')).toBe('bi-shield-lock');
    expect(getProviderIcon('auth0')).toBe('bi-shield-check');
  });

  it('should be case-insensitive', () => {
    expect(getProviderIcon('Google')).toBe('bi-google');
    expect(getProviderIcon('MICROSOFT')).toBe('bi-microsoft');
    expect(getProviderIcon('GitHub')).toBe('bi-github');
  });

  it('should return default icon for unknown providers', () => {
    expect(getProviderIcon('unknown')).toBe('bi-key');
    expect(getProviderIcon('random')).toBe('bi-key');
    expect(getProviderIcon('')).toBe('bi-key');
  });
});

describe('hasProviderIcon', () => {
  it('should return true for known providers', () => {
    expect(hasProviderIcon('google')).toBe(true);
    expect(hasProviderIcon('microsoft')).toBe(true);
    expect(hasProviderIcon('github')).toBe(true);
    expect(hasProviderIcon('okta')).toBe(true);
    expect(hasProviderIcon('auth0')).toBe(true);
  });

  it('should be case-insensitive', () => {
    expect(hasProviderIcon('Google')).toBe(true);
    expect(hasProviderIcon('MICROSOFT')).toBe(true);
  });

  it('should return false for unknown providers', () => {
    expect(hasProviderIcon('unknown')).toBe(false);
    expect(hasProviderIcon('random')).toBe(false);
    expect(hasProviderIcon('')).toBe(false);
  });
});

describe('getSupportedProviders', () => {
  it('should return array of supported provider names', () => {
    const providers = getSupportedProviders();
    expect(providers).toContain('google');
    expect(providers).toContain('microsoft');
    expect(providers).toContain('github');
    expect(providers).toContain('okta');
    expect(providers).toContain('auth0');
  });

  it('should return exactly 5 providers', () => {
    expect(getSupportedProviders().length).toBe(5);
  });
});