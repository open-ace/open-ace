import { describe, expect, it } from 'vitest';
import {
  AUDIT_ACTION_OPTIONS_FALLBACK,
  AUDIT_CATEGORIES_FALLBACK,
  buildAuditActionMappings,
} from './useAuditActions';

describe('buildAuditActionMappings', () => {
  it('maps action values to related resource types', () => {
    const mappings = buildAuditActionMappings(
      AUDIT_ACTION_OPTIONS_FALLBACK,
      AUDIT_CATEGORIES_FALLBACK
    );

    expect(mappings.actionToResourceTypes.login).toEqual(['session']);
    expect(mappings.actionToResourceTypes.user_create).toEqual(['user']);
    expect(mappings.actionToResourceTypes.data_export).toEqual([
      'analytics_report',
      'analytics',
      'data',
    ]);
    expect(mappings.actionToResourceTypes.agent_token_rotate).toEqual([
      'remote_machine',
      'agent_token',
    ]);
  });

  it('builds reverse resource-to-category mapping for fallback data', () => {
    const mappings = buildAuditActionMappings(
      AUDIT_ACTION_OPTIONS_FALLBACK,
      AUDIT_CATEGORIES_FALLBACK
    );

    expect(mappings.resourceToCategories.session).toContain('auth');
    expect(mappings.resourceToCategories.user).toEqual(['user_management', 'permission']);
    expect(mappings.resourceToCategories.remote_machine).toEqual(['agent']);
  });
});
