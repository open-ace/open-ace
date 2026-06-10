import { useState } from 'react';
import { Button, Modal } from '@/components/common';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { useCancelMilestone } from '@/hooks/useAutonomous';

interface CancelRoundModalProps {
  isOpen: boolean;
  onClose: () => void;
  workflowId: string;
  milestoneId: string;
  milestoneTitle: string;
}

export default function CancelRoundModal({
  isOpen,
  onClose,
  workflowId,
  milestoneId,
  milestoneTitle,
}: CancelRoundModalProps) {
  const language = useLanguage();
  const [feedback, setFeedback] = useState('');

  const cancelMutation = useCancelMilestone();

  const handleSubmit = () => {
    if (!feedback.trim() || feedback.trim().length < 10) return;
    cancelMutation.mutate(
      { workflowId, milestoneId, feedback: feedback.trim() },
      {
        onSuccess: () => {
          setFeedback('');
          onClose();
        },
      }
    );
  };

  const handleClose = () => {
    if (!cancelMutation.isPending) {
      setFeedback('');
      onClose();
    }
  };

  const isValid = feedback.trim().length >= 10;

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title={t('autoCancelRound', language)}>
      <div className="mb-3">
        <p className="text-muted small">{t('autoCancelRoundDesc', language)}</p>
        <p className="fw-semibold small">{milestoneTitle}</p>
      </div>

      <div className="mb-3">
        <label className="form-label fw-semibold">{t('autoFeedbackLabel', language)}</label>
        <textarea
          className="form-control"
          rows={4}
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder={t('autoFeedbackPlaceholder', language)}
          disabled={cancelMutation.isPending}
        />
        {!isValid && feedback.length > 0 && (
          <div className="form-text text-danger">{t('autoFeedbackRequired', language)}</div>
        )}
      </div>

      <div className="d-flex justify-content-end gap-2">
        <Button variant="secondary" onClick={handleClose} disabled={cancelMutation.isPending}>
          {t('cancel', language)}
        </Button>
        <Button
          variant="danger"
          onClick={handleSubmit}
          disabled={!isValid || cancelMutation.isPending}
        >
          {cancelMutation.isPending ? t('autoCreating', language) : t('autoCancelRound', language)}
        </Button>
      </div>
    </Modal>
  );
}
