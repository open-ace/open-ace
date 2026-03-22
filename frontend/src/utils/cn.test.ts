/**
 * Tests for cn (className) utility
 */

import { describe, it, expect } from 'vitest';
import { cn } from './cn';

describe('cn', () => {
  it('should merge class names', () => {
    expect(cn('foo', 'bar')).toBe('foo bar');
  });

  it('should handle conditional classes', () => {
    expect(cn('foo', false && 'bar', 'baz')).toBe('foo baz');
    expect(cn('foo', true && 'bar', 'baz')).toBe('foo bar baz');
  });

  it('should handle undefined and null', () => {
    expect(cn('foo', undefined, null, 'bar')).toBe('foo bar');
  });

  it('should handle object syntax', () => {
    expect(cn({ foo: true, bar: false, baz: true })).toBe('foo baz');
  });

  it('should handle array syntax', () => {
    expect(cn(['foo', 'bar'], 'baz')).toBe('foo bar baz');
  });

  it('should merge tailwind classes correctly', () => {
    // clsx doesn't dedupe, but it handles the merging
    expect(cn('p-4', 'p-2')).toBe('p-4 p-2');
  });

  it('should handle empty input', () => {
    expect(cn()).toBe('');
    expect(cn('')).toBe('');
  });

  it('should handle complex combinations', () => {
    const result = cn(
      'base-class',
      { active: true, disabled: false },
      ['array-class'],
      undefined,
      'final-class'
    );
    expect(result).toBe('base-class active array-class final-class');
  });
});
