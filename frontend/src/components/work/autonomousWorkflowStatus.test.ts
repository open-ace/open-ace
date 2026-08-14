import { describe, expect, it } from 'vitest';

import {
  getAutonomousWorkflowStatusConfig,
  getPauseReasonCategory,
  isStaleAcceptancePause,
} from './autonomousWorkflowStatus';

describe('autonomousWorkflowStatus', () => {
  it('does not fall back to Pending for verification_pending', () => {
    expect(getAutonomousWorkflowStatusConfig('verification_pending')).toMatchObject({
      labelKey: 'autoStatusVerificationPending',
      icon: 'bi-shield-check',
      tone: 'info',
    });
  });
});

describe('getPauseReasonCategory (#2634)', () => {
  const base = { status: 'paused', current_phase: 'developing', error_message: '' };

  it('categorizes paused @ acceptance_verification as acceptance_awaiting', () => {
    expect(
      getPauseReasonCategory({
        ...base,
        current_phase: 'acceptance_verification',
        error_message: 'Acceptance verification rejected; awaiting review',
      })
    ).toBe('acceptance_awaiting');
  });

  it('categorizes Quota exceeded pauses as quota', () => {
    expect(
      getPauseReasonCategory({
        ...base,
        error_message: 'Quota exceeded: daily usage at 100% (1000/1000)',
      })
    ).toBe('quota');
  });

  it('categorizes everything else paused as manual', () => {
    expect(getPauseReasonCategory(base)).toBe('manual');
  });

  it('returns null for non-paused workflows', () => {
    expect(
      getPauseReasonCategory({
        status: 'developing',
        current_phase: 'acceptance_verification',
        error_message: '',
      })
    ).toBeNull();
  });
});

describe('isStaleAcceptancePause (#2634)', () => {
  const DAY_MS = 24 * 60 * 60 * 1000;
  const now = Date.parse('2026-08-14T00:00:00Z');
  const iso = (daysAgo: number) => new Date(now - daysAgo * DAY_MS).toISOString();
  const base = {
    status: 'paused',
    current_phase: 'acceptance_verification',
    error_message: 'rejected',
  };

  it('flags acceptance pauses older than 3 days', () => {
    expect(isStaleAcceptancePause({ ...base, paused_at: iso(4) }, now)).toBe(true);
  });

  it('does not flag fresh acceptance pauses', () => {
    expect(isStaleAcceptancePause({ ...base, paused_at: iso(2) }, now)).toBe(false);
  });

  it('falls back to updated_at when paused_at is missing', () => {
    expect(isStaleAcceptancePause({ ...base, paused_at: null, updated_at: iso(5) }, now)).toBe(
      true
    );
  });

  it('never flags non-acceptance pauses', () => {
    expect(
      isStaleAcceptancePause({ ...base, current_phase: 'developing', paused_at: iso(10) }, now)
    ).toBe(false);
  });

  // The backend writes paused_at as "%Y-%m-%d %H:%M:%S" in UTC (no "T", no
  // timezone). Date.parse of that shape returns NaN in Safari (badge never
  // fires) and LOCAL time in Chrome (timezone skew). The math must hold
  // regardless of the runner's local timezone.
  describe('non-ISO backend timestamps', () => {
    const backendTs = (daysAgo: number) =>
      new Date(now - daysAgo * DAY_MS).toISOString().slice(0, 19).replace('T', ' ');

    it('flags acceptance pauses older than 3 days written in backend format', () => {
      expect(isStaleAcceptancePause({ ...base, paused_at: backendTs(4) }, now)).toBe(true);
    });

    it('does not flag fresh acceptance pauses written in backend format', () => {
      expect(isStaleAcceptancePause({ ...base, paused_at: backendTs(1) }, now)).toBe(false);
    });
  });
});
