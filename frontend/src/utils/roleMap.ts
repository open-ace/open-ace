/**
 * Role normalization & display-label mapping.
 *
 * Background: the `role` field of conversation messages has two competing
 * spellings in the database:
 *   - autonomous agent writes `role='tool'` (OpenAI/Anthropic API standard)
 *   - OpenClaw importer writes `role='toolResult'` (legacy spelling)
 *
 * The two never compared equal, so the "Tool Result" role filter silently
 * returned an empty list (see the conversation-history filter bug). Rather
 * than migrate historical data or alter the API protocol, we normalize once
 * at the data boundary and drive all downstream role checks through this
 * single source of truth.
 *
 * Canonical value: the API-standard `'tool'`. `normalizeMessageRole()` is the
 * only sanctioned way to coerce a raw role string; everything else consumes
 * the normalized value.
 */

import { t, type Language } from '@/i18n';

/**
 * The set of canonical role values the frontend reasons about. This is the
 * normalized form — raw values like `'toolResult'` are collapsed to `'tool'`
 * before reaching this union.
 */
export type NormalizedMessageRole = 'user' | 'assistant' | 'system' | 'tool';

/**
 * Raw role strings known to appear in persisted data, including the legacy
 * `'toolResult'` spelling. Listed for documentation; the normalizer only needs
 * to know about the tool/toolResult collision.
 */
export type RawMessageRole = NormalizedMessageRole | 'toolResult';

/** Legacy spelling that must fold into the canonical `'tool'` value. */
const TOOL_RESULT_ROLE = 'toolResult' as const;

/**
 * Coerce a raw role string into its canonical form.
 *
 * - `'toolResult'` → `'tool'` (the historical divergence this module exists to fix)
 * - any other string is returned unchanged; unknown values pass through so the
 *   UI keeps rendering instead of hiding data behind a silent rewrite.
 */
export function normalizeMessageRole(role: string | null | undefined): string {
  if (role === TOOL_RESULT_ROLE) {
    return 'tool';
  }
  return role ?? '';
}

/**
 * True when a (possibly raw) role identifies a tool result. Centralizes the
 * tool/toolResult equivalence so callers never spell out both literals.
 */
export function isToolRole(role: string | null | undefined): boolean {
  const normalized = normalizeMessageRole(role);
  return normalized === 'tool';
}

/**
 * i18n keys backing each role's localized label. Kept in one place so the
 * badge, the filter button text, and the empty-state message all agree.
 */
const ROLE_LABEL_KEYS: Record<string, string> = {
  user: 'messageRoleUser',
  assistant: 'messageRoleAssistant',
  system: 'messageRoleSystem',
  tool: 'messageRoleToolResult',
};

/**
 * Render a role as a localized display label.
 *
 * Accepts raw values (the legacy `'toolResult'` is accepted for symmetry with
 * `normalizeMessageRole`), normalizes first, then translates. Unknown roles
 * fall back to the raw value rather than an empty string so an unexpected
 * role is still legible while debugging.
 */
export function getRoleLabel(role: string | null | undefined, language?: Language): string {
  const normalized = normalizeMessageRole(role);
  const key = ROLE_LABEL_KEYS[normalized];
  if (!key) {
    return normalized;
  }
  return t(key, language);
}
