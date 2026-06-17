/**
 * Unit tests for anomaly description helpers.
 *
 * These helpers are shared by the dedicated anomaly page (backend-driven) and
 * the Analysis overview table (client-driven), so their contract — direction
 * derivation, baseline fallback, optional top_contributor — is covered here.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import {
  getAnomalyDescription,
  getAnomalySuggestion,
  getAnomalyTopContributor,
} from './AnomalyDetection';

describe('getAnomalyDescription', () => {
  it('describes a spike as above the daily average', () => {
    const desc = getAnomalyDescription(
      { date: '2026-05-11', tokens: 5000, expected: 500, deviation: 900, type: 'spike', severity: 'high' },
      'en'
    );
    // Direction comes from type (spike => "above"), never from the deviation sign
    expect(desc).toContain('above the daily average');
    expect(desc).not.toContain('below');
  });

  it('describes a drop as below the daily average', () => {
    const desc = getAnomalyDescription(
      { date: '2026-05-11', tokens: 100, expected: 500, deviation: 80, type: 'drop', severity: 'low' },
      'en'
    );
    expect(desc).toContain('below the daily average');
    expect(desc).not.toContain('above');
  });

  it('localizes to Chinese', () => {
    const desc = getAnomalyDescription(
      { date: '2026-05-11', tokens: 5000, expected: 500, deviation: 900, type: 'spike', severity: 'high' },
      'zh'
    );
    expect(desc).toContain('高出');
  });

  it('degrades to the raw token count when baseline fields are missing', () => {
    const desc = getAnomalyDescription(
      { date: '2026-05-11', tokens: 5000, type: 'spike', severity: 'high' },
      'en'
    );
    expect(desc).toMatch(/5/); // formatTokens(5000)
    expect(desc).not.toContain('average');
  });

  it('degrades when expected is zero (division guard)', () => {
    const desc = getAnomalyDescription(
      { date: '2026-05-11', tokens: 5000, expected: 0, deviation: 0, type: 'spike', severity: 'high' },
      'en'
    );
    expect(desc).not.toContain('NaN');
  });
});

describe('getAnomalySuggestion', () => {
  it('returns a spike suggestion for spikes', () => {
    const s = getAnomalySuggestion('spike', 'en');
    expect(s.length).toBeGreaterThan(0);
    expect(getAnomalySuggestion('spike', 'zh')).toContain('建议');
  });

  it('returns a drop suggestion for non-spikes', () => {
    const s = getAnomalySuggestion('drop', 'en');
    expect(s.length).toBeGreaterThan(0);
    expect(getAnomalySuggestion('drop', 'zh')).toContain('建议');
  });
});

describe('getAnomalyTopContributor', () => {
  it('formats the tool and its share', () => {
    const line = getAnomalyTopContributor(
      { date: '2026-05-11', tokens: 5000, type: 'spike', severity: 'high', top_contributor: { tool: 'qwen', share_pct: 80 } },
      'en'
    );
    expect(line).toContain('qwen');
    expect(line).toContain('80');
  });

  it('returns empty string when top_contributor is absent (forward-compatible)', () => {
    const line = getAnomalyTopContributor(
      { date: '2026-05-11', tokens: 5000, type: 'spike', severity: 'high' },
      'en'
    );
    expect(line).toBe('');
  });

  it('returns empty string when share_pct is missing', () => {
    const line = getAnomalyTopContributor(
      { date: '2026-05-11', tokens: 5000, type: 'spike', severity: 'high', top_contributor: { tool: 'qwen' } },
      'en'
    );
    expect(line).toBe('');
  });
});
