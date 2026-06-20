/**
 * Tests for MessageRole type/constant alignment (single source of truth).
 *
 * Regression guard for the role-string drift bug: backend stores tool
 * messages with role="tool", so the frontend constant must use the same
 * literal. If the union and the constant object ever diverge, the tool
 * message filter silently returns zero results.
 */

import { describe, it, expect } from 'vitest';
import { MessageRole } from '@/types';

describe('MessageRole constant', () => {
  it('uses "tool" (not "toolResult") to match backend storage', () => {
    // Backend writes role="tool" (agent_runner.py). The filter value must
    // equal this literal or `msg.role === roleFilter` is always false.
    expect(MessageRole.TOOL).toBe('tool');
    expect(MessageRole.TOOL).not.toBe('toolResult');
  });

  it('exposes stable literals for all four roles', () => {
    expect(MessageRole.USER).toBe('user');
    expect(MessageRole.ASSISTANT).toBe('assistant');
    expect(MessageRole.SYSTEM).toBe('system');
  });

  it('keeps all constant values assignable to the MessageRole union', () => {
    // Compile-time assertion: each constant value must be a valid MessageRole.
    const roles: import('@/types').MessageRole[] = [
      MessageRole.USER,
      MessageRole.ASSISTANT,
      MessageRole.SYSTEM,
      MessageRole.TOOL,
    ];
    expect(new Set(roles).size).toBe(roles.length);
  });
});
