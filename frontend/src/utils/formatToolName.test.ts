/**
 * Tests for formatToolName utility function
 */

import { describe, it, expect } from 'vitest';
import { formatToolName, TOOL_DISPLAY_NAMES } from '.';

describe('formatToolName', () => {
  it('should return display name for known tools', () => {
    expect(formatToolName('qwen')).toBe('Qwen');
    expect(formatToolName('claude')).toBe('Claude');
    expect(formatToolName('openclaw')).toBe('OpenClaw');
    expect(formatToolName('openai')).toBe('OpenAI');
    expect(formatToolName('codex')).toBe('Codex');
    expect(formatToolName('zcode')).toBe('ZCode');
    expect(formatToolName('run_shell_command')).toBe('Shell');
  });

  it('should capitalize first letter for unknown tools', () => {
    expect(formatToolName('unknown_tool')).toBe('Unknown_tool');
    expect(formatToolName('mytool')).toBe('Mytool');
    expect(formatToolName('a')).toBe('A');
  });

  it('should handle empty string', () => {
    expect(formatToolName('')).toBe('');
  });

  it('should handle tools with single character', () => {
    expect(formatToolName('x')).toBe('X');
  });
});

describe('TOOL_DISPLAY_NAMES', () => {
  it('should be a record of known tool display names', () => {
    expect(TOOL_DISPLAY_NAMES).toBeDefined();
    expect(typeof TOOL_DISPLAY_NAMES).toBe('object');
    expect(Object.keys(TOOL_DISPLAY_NAMES).length).toBeGreaterThan(0);
  });

  it('should contain all expected tool keys', () => {
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('qwen');
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('claude');
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('openclaw');
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('openai');
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('codex');
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('zcode');
    expect(TOOL_DISPLAY_NAMES).toHaveProperty('run_shell_command');
  });
});
