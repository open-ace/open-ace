/**
 * QuotaCheckResultModal Component - Modal for displaying tenant quota check results
 *
 * Issue #3132: Tenant management page quota check functionality
 *
 * Features:
 * - Display allowed/exceeded status with badge
 * - Show reason when quota is exceeded
 * - Show check timestamp with snapshot note
 * - "Edit Quota" button when quota is exceeded
 */

import React from 'react';
import { t, type Language } from '@/i18n';
import { Modal, Button, Badge } from '@/components/common';
import { formatDateTime } from '@/utils';
import type { Tenant } from '@/api';
import type { CheckResult } from '@/hooks/useTenantQuotaCheck';

/**
 * Props for QuotaCheckResultModal
 */
export interface QuotaCheckResultModalProps {
  /** Whether the modal is open */
  isOpen: boolean;
  /** Check result (null if no result) */
  result: CheckResult | null;
  /** Callback when modal is closed */
  onClose: () => void;
  /** Callback when user clicks "Edit Quota" */
  onEditQuota: (tenant: Tenant) => void;
  /** Current language for i18n */
  language: Language;
}

/**
 * Modal component for displaying quota check results
 *
 * Always renders the Modal component to ensure proper state management.
 * Content is only displayed when result is available.
 */
export const QuotaCheckResultModal: React.FC<QuotaCheckResultModalProps> = ({
  isOpen,
  result,
  onClose,
  onEditQuota,
  language,
}) => {
  // Get display reason (fallback if reason is null/undefined)
  // Use nullish coalescing to preserve empty strings if that's intentional
  const displayReason = result?.reason ?? t('noDetails', language);

  // Build title based on result availability
  const title = result
    ? `${result.tenant.name} ${t('quotaCheckResult', language)}`
    : t('quotaCheckResult', language);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={title}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t('close', language)}
          </Button>
          {result && !result.allowed && (
            <Button variant="primary" onClick={() => onEditQuota(result.tenant)}>
              {t('editQuotaNow', language)}
            </Button>
          )}
        </>
      }
    >
      {result && (
        <div className="quota-check-result">
          {/* Status Badge */}
          <div className="mb-3">
            <div className="d-flex align-items-center gap-2">
              <span className="fw-medium">{t('status', language)}:</span>
              {result.allowed ? (
                <Badge variant="success">{t('quotaAvailable', language)}</Badge>
              ) : (
                <Badge variant="danger">{t('quotaExceeded', language)}</Badge>
              )}
            </div>
          </div>

          {/* Reason (only shown when quota exceeded) */}
          {!result.allowed && (
            <div className="mb-3">
              <div className="fw-medium mb-1">{t('reason', language)}:</div>
              <div className="text-muted p-2 bg-light rounded">{displayReason}</div>
            </div>
          )}

          {/* Check Time */}
          <div className="mb-3">
            <div className="d-flex align-items-center gap-2">
              <span className="fw-medium">{t('quotaCheckTime', language)}:</span>
              <span className="text-muted">{formatDateTime(result.checkedAt.toISOString())}</span>
            </div>
          </div>

          {/* Snapshot Note */}
          <div className="mt-4 pt-3 border-top">
            <small className="text-muted">
              <i className="bi bi-info-circle me-1" />
              {t('quotaCheckSnapshotNote', language)}
            </small>
          </div>
        </div>
      )}
    </Modal>
  );
};
