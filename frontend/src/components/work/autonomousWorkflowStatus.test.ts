import { describe, expect, it } from 'vitest';

import { getAutonomousWorkflowStatusConfig } from './autonomousWorkflowStatus';

describe('autonomousWorkflowStatus', () => {
  it('does not fall back to Pending for verification_pending', () => {
    expect(getAutonomousWorkflowStatusConfig('verification_pending')).toMatchObject({
      labelKey: 'autoStatusVerificationPending',
      icon: 'bi-shield-check',
      tone: 'info',
    });
  });
});
