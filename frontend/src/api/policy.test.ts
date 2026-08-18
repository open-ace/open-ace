/**
 * Policy API Client Tests
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { policyApi, PolicyDisabledError } from './policy';
import { apiClient } from './client';

// Mock apiClient
vi.mock('./client', () => ({
  apiClient: {
    get: vi.fn(),
    post: vi.fn(),
    put: vi.fn(),
    patch: vi.fn(),
  },
}));

describe('policyApi', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  describe('getRules', () => {
    it('should return rules array on success', async () => {
      const mockRules = [
        { id: 1, rule_key: 'test-rule', name: 'Test Rule', policy_type: 'model', effect: 'allow' },
      ];
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        success: true,
        rules: mockRules,
        total: 1,
      });

      const result = await policyApi.getRules();
      expect(result).toEqual(mockRules);
      expect(apiClient.get).toHaveBeenCalledWith('/api/policy/rules', {});
    });

    it('should pass includeDisabled parameter', async () => {
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        success: true,
        rules: [],
        total: 0,
      });

      await policyApi.getRules(true);
      expect(apiClient.get).toHaveBeenCalledWith('/api/policy/rules', {
        include_disabled: 'true',
      });
    });

    it('should throw PolicyDisabledError when disabled is true', async () => {
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        success: false,
        disabled: true,
      });

      await expect(policyApi.getRules()).rejects.toThrow(PolicyDisabledError);
    });
  });

  describe('createRule', () => {
    it('should create a rule and return it', async () => {
      const newRule = {
        rule_key: 'new-rule',
        name: 'New Rule',
        policy_type: 'model' as const,
        effect: 'allow' as const,
      };
      const createdRule = { id: 1, ...newRule };

      vi.mocked(apiClient.post).mockResolvedValueOnce({
        success: true,
        rule: createdRule,
      });

      const result = await policyApi.createRule(newRule);
      expect(result).toEqual({ success: true, rule: createdRule });
      expect(apiClient.post).toHaveBeenCalledWith('/api/policy/rules', newRule);
    });

    it('should throw PolicyDisabledError when disabled', async () => {
      vi.mocked(apiClient.post).mockResolvedValueOnce({
        success: false,
        disabled: true,
      });

      await expect(
        policyApi.createRule({ rule_key: 'test', name: 'Test', policy_type: 'model', effect: 'allow' })
      ).rejects.toThrow(PolicyDisabledError);
    });
  });

  describe('updateRule', () => {
    it('should update a rule by key', async () => {
      const updateData = {
        rule_key: 'existing-rule',
        name: 'Updated Rule',
        policy_type: 'model' as const,
        effect: 'deny' as const,
      };
      const updatedRule = { id: 1, ...updateData };

      vi.mocked(apiClient.put).mockResolvedValueOnce({
        success: true,
        rule: updatedRule,
      });

      const result = await policyApi.updateRule('existing-rule', updateData);
      expect(result).toEqual({ success: true, rule: updatedRule });
      expect(apiClient.put).toHaveBeenCalledWith('/api/policy/rules/existing-rule', updateData);
    });

    it('should encode rule key with special characters', async () => {
      vi.mocked(apiClient.put).mockResolvedValueOnce({
        success: true,
        rule: { id: 1 },
      });

      await policyApi.updateRule('rule/with spaces', {
        rule_key: 'rule/with spaces',
        name: 'Test',
        policy_type: 'model',
        effect: 'allow',
      });

      expect(apiClient.put).toHaveBeenCalledWith(
        '/api/policy/rules/rule%2Fwith%20spaces',
        expect.any(Object)
      );
    });
  });

  describe('toggleRule', () => {
    it('should toggle rule enabled state', async () => {
      vi.mocked(apiClient.patch).mockResolvedValueOnce({ success: true });

      const result = await policyApi.toggleRule(123, false);
      expect(result).toEqual({ success: true });
      expect(apiClient.patch).toHaveBeenCalledWith('/api/policy/rules/123/enabled', {
        enabled: false,
      });
    });

    it('should throw PolicyDisabledError when disabled', async () => {
      vi.mocked(apiClient.patch).mockResolvedValueOnce({
        success: false,
        disabled: true,
      });

      await expect(policyApi.toggleRule(123, true)).rejects.toThrow(PolicyDisabledError);
    });
  });

  describe('getDecisions', () => {
    it('should fetch decisions by session ID', async () => {
      const mockDecisions = [
        { id: 1, decision_id: 'd1', decision: 'allow' },
      ];

      vi.mocked(apiClient.get).mockResolvedValueOnce({
        success: true,
        decisions: mockDecisions,
        total: 1,
      });

      const result = await policyApi.getDecisions('session-123');
      expect(result).toEqual(mockDecisions);
      expect(apiClient.get).toHaveBeenCalledWith('/api/policy/decisions', {
        session_id: 'session-123',
        limit: '100',
      });
    });

    it('should clamp limit to valid range', async () => {
      vi.mocked(apiClient.get).mockResolvedValueOnce({
        success: true,
        decisions: [],
        total: 0,
      });

      await policyApi.getDecisions('session-123', 5000);
      expect(apiClient.get).toHaveBeenCalledWith('/api/policy/decisions', {
        session_id: 'session-123',
        limit: '1000', // clamped to max
      });

      await policyApi.getDecisions('session-123', 0);
      expect(apiClient.get).toHaveBeenCalledWith('/api/policy/decisions', {
        session_id: 'session-123',
        limit: '1', // clamped to min
      });
    });
  });
});

describe('PolicyDisabledError', () => {
  it('should have correct name and message', () => {
    const error = new PolicyDisabledError();
    expect(error.name).toBe('PolicyDisabledError');
    expect(error.message).toBe('Policy feature is disabled');
    expect(error instanceof Error).toBe(true);
  });
});