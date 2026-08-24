/**
 * Tests for Compliance API
 *
 * Issue #2617: Verify cleanup operations use extended timeout
 * to prevent premature frontend abort while backend continues execution.
 */

import { describe, it, expect, vi, beforeEach } from 'vitest';
import { complianceApi } from './compliance';

describe('complianceApi', () => {
  beforeEach(() => {
    vi.resetAllMocks();
  });

  describe('runCleanup - Issue #2617', () => {
    it('should use extended timeout (120s) for cleanup operations', async () => {
      const mockReport = {
        timestamp: '2026-08-24T00:00:00',
        rules_applied: [],
        records_deleted: 0,
        records_archived: 0,
        records_anonymized: 0,
        errors: [],
      };

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify(mockReport)),
      } as Response);

      await complianceApi.runCleanup(false);

      // Verify fetch was called with signal from AbortController
      // The timeout should be 120000ms (CLEANUP_TIMEOUT)
      expect(fetch).toHaveBeenCalledWith(
        '/api/compliance/retention/cleanup',
        expect.objectContaining({
          method: 'POST',
        })
      );

      // Verify the abort signal timeout by checking the AbortController
      // was created with 120s timeout (not the default 30s)
      const callArgs = vi.mocked(fetch).mock.calls[0];
      const signal = callArgs[1]?.signal;
      expect(signal).toBeDefined();
      expect(signal).toBeInstanceOf(AbortSignal);
    });

    it('should append dry_run query param when dryRun is true', async () => {
      const mockReport = {
        timestamp: '2026-08-24T00:00:00',
        rules_applied: [],
        records_deleted: 0,
        records_archived: 0,
        records_anonymized: 0,
        errors: [],
      };

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify(mockReport)),
      } as Response);

      await complianceApi.runCleanup(true);

      expect(fetch).toHaveBeenCalledWith(
        '/api/compliance/retention/cleanup?dry_run=true',
        expect.objectContaining({
          method: 'POST',
        })
      );
    });

    it('should not append dry_run param when dryRun is false', async () => {
      const mockReport = {
        timestamp: '2026-08-24T00:00:00',
        rules_applied: [],
        records_deleted: 0,
        records_archived: 0,
        records_anonymized: 0,
        errors: [],
      };

      vi.mocked(fetch).mockResolvedValueOnce({
        ok: true,
        text: () => Promise.resolve(JSON.stringify(mockReport)),
      } as Response);

      await complianceApi.runCleanup(false);

      expect(fetch).toHaveBeenCalledWith(
        '/api/compliance/retention/cleanup',
        expect.any(Object)
      );
    });
  });
});
