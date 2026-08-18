/**
 * Policy API - Central Policy & Approval Engine API client.
 *
 * Provides CRUD operations for policy rules and read access to policy decisions.
 * All endpoints require admin role and respect the policy.enabled feature flag.
 *
 * When policy.enabled is false, endpoints return {success: false, disabled: true}
 * instead of the normal response. This client detects and handles that case.
 */

import { apiClient } from './client';

// Policy Types (must match backend PolicyType enum)
export type PolicyType = 'model' | 'provider' | 'tool_action' | 'file_path' | 'command';

// Pattern Types (must match backend PatternType enum)
export type PatternType = 'glob' | 'regex';

// Policy Effects (must match backend PolicyEffect enum)
export type PolicyEffect = 'allow' | 'deny' | 'require_approval';

// Decision types (must match backend Decision enum)
export type DecisionType = 'allow' | 'deny' | 'require_human';

/**
 * Policy Rule - One immutable versioned snapshot of a policy rule.
 * Matches backend PolicyRule dataclass.
 */
export interface PolicyRule {
  id: number | null;
  rule_key: string;
  name: string;
  version: number;
  is_current: boolean;
  enabled: boolean;
  // Scope (all nullable = wildcard)
  tenant_id: number | null;
  project_path: string | null;
  machine_id: string | null;
  user_id: number | null;
  team_id: string | null;
  // Target
  policy_type: PolicyType;
  // Match
  pattern_type: PatternType;
  pattern: string | null;
  value_list: string[];
  tool_name: string | null;
  action: string | null;
  // Result
  effect: PolicyEffect;
  // Ordering
  priority: number;
  is_default: boolean;
  // Approval TTL override (seconds); null = use config default
  approval_ttl_seconds: number | null;
  // Audit
  created_by: number | null;
  created_at: string | null;
  superseded_at: string | null;
  description: string | null;
}

/**
 * Policy Decision - The authoritative control-plane decision record.
 * Matches backend PolicyDecision dataclass.
 */
export interface PolicyDecision {
  id: number | null;
  decision_id: string;
  request_id: string | null;
  run_id: string | null;
  session_id: string | null;
  // Scope
  tenant_id: number | null;
  workspace_scope: string | null;
  machine_id: string | null;
  // Target
  model: string | null;
  provider: string | null;
  tool_name: string | null;
  action: string | null;
  resource_target: string | null;
  // Fingerprint
  args_digest: string | null;
  normalization_profile_id: string | null;
  normalization_profile_version: number | null;
  fingerprint_hash: string | null;
  // Matched rule
  policy_rule_id: number | null;
  policy_rule_version: number | null;
  // Decision
  decision: DecisionType;
  reason: string | null;
  // Lifecycle
  reviewer_identity: string | null;
  issued_at: string | null;
  expires_at: string | null;
  consumed_at: string | null;
  remote_response_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/**
 * Create/Update Policy Rule Request.
 * Matches backend _parse_rule_body expectations.
 */
export interface CreatePolicyRuleRequest {
  rule_key: string;
  name: string;
  policy_type: PolicyType;
  effect: PolicyEffect;
  pattern_type?: PatternType;
  pattern?: string;
  value_list?: string[];
  tool_name?: string;
  action?: string;
  tenant_id?: number | null;
  project_path?: string | null;
  machine_id?: string | null;
  user_id?: number | null;
  team_id?: string | null;
  priority?: number;
  is_default?: boolean;
  enabled?: boolean;
  approval_ttl_seconds?: number | null;
  description?: string;
}

/**
 * Response from policy rules list endpoint.
 */
interface PolicyRulesListResponse {
  success: boolean;
  disabled?: boolean;
  rules?: PolicyRule[];
  total?: number;
}

/**
 * Response from policy rule create/update endpoint.
 */
interface PolicyRuleResponse {
  success: boolean;
  disabled?: boolean;
  rule?: PolicyRule;
}

/**
 * Response from policy decisions endpoint.
 */
interface PolicyDecisionsResponse {
  success: boolean;
  disabled?: boolean;
  decisions?: PolicyDecision[];
  total?: number;
  error?: string;
}

/**
 * Response from toggle rule endpoint.
 */
interface ToggleRuleResponse {
  success: boolean;
  disabled?: boolean;
  error?: string;
}

/**
 * Error thrown when policy feature is disabled.
 * The backend returns {success: false, disabled: true} instead of a normal response
 * when policy.enabled is false.
 */
export class PolicyDisabledError extends Error {
  constructor() {
    super('Policy feature is disabled');
    this.name = 'PolicyDisabledError';
  }
}

/**
 * Check if a response indicates the policy feature is disabled.
 */
function checkDisabled(response: { success: boolean; disabled?: boolean }): void {
  if (response.disabled === true) {
    throw new PolicyDisabledError();
  }
}

/**
 * Policy API client.
 */
export const policyApi = {
  /**
   * List current (latest version) policy rules.
   * @param includeDisabled - Include disabled rules in the response
   */
  async getRules(includeDisabled: boolean = false): Promise<PolicyRule[]> {
    const params: Record<string, string> = {};
    if (includeDisabled) {
      params.include_disabled = 'true';
    }
    const response = await apiClient.get<PolicyRulesListResponse>('/api/policy/rules', params);
    checkDisabled(response);
    return response.rules ?? [];
  },

  /**
   * Create a new policy rule (first version of a rule_key).
   * @param data - Rule creation data
   */
  async createRule(data: CreatePolicyRuleRequest): Promise<{ success: boolean; rule: PolicyRule }> {
    const response = await apiClient.post<PolicyRuleResponse>('/api/policy/rules', data);
    checkDisabled(response);
    return { success: true, rule: response.rule! };
  },

  /**
   * Versioned edit: supersede the current version and insert a new one.
   * The body is the full new rule definition (same fields as POST).
   * Prior versions remain immutable for audit.
   * @param ruleKey - The unique key of the rule to update
   * @param data - New rule definition
   */
  async updateRule(
    ruleKey: string,
    data: CreatePolicyRuleRequest
  ): Promise<{ success: boolean; rule: PolicyRule }> {
    const response = await apiClient.put<PolicyRuleResponse>(
      `/api/policy/rules/${encodeURIComponent(ruleKey)}`,
      data
    );
    checkDisabled(response);
    return { success: true, rule: response.rule! };
  },

  /**
   * Toggle enabled on the current version of a rule.
   * @param ruleId - The database ID of the rule
   * @param enabled - New enabled state
   */
  async toggleRule(ruleId: number, enabled: boolean): Promise<{ success: boolean }> {
    const response = await apiClient.patch<ToggleRuleResponse>(
      `/api/policy/rules/${ruleId}/enabled`,
      { enabled }
    );
    checkDisabled(response);
    return { success: true };
  },

  /**
   * List policy decisions, filtered by session_id.
   * @param sessionId - Session ID to filter by (required)
   * @param limit - Maximum number of decisions to return (default 100, max 1000)
   */
  async getDecisions(sessionId: string, limit: number = 100): Promise<PolicyDecision[]> {
    const params: Record<string, string> = {
      session_id: sessionId,
      limit: String(Math.min(Math.max(limit, 1), 1000)),
    };
    const response = await apiClient.get<PolicyDecisionsResponse>('/api/policy/decisions', params);
    checkDisabled(response);
    return response.decisions ?? [];
  },
};
