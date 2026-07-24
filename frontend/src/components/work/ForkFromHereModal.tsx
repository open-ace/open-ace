import { useState } from 'react';
import { Button, Modal } from '@/components/common';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { useForkMilestone } from '@/hooks/useAutonomous';

interface ForkFromHereModalProps {
  isOpen: boolean;
  onClose: () => void;
  workflowId: string;
  milestoneId: string;
  milestoneTitle: string;
}

export default function ForkFromHereModal({
  isOpen,
  onClose,
  workflowId,
  milestoneId,
  milestoneTitle,
}: ForkFromHereModalProps) {
  const language = useLanguage();
  const [feedback, setFeedback] = useState('');
  const [branchName, setBranchName] = useState('');
  const [pauseOriginal, setPauseOriginal] = useState(true);

  const forkMutation = useForkMilestone();

  const handleSubmit = () => {
    if (!feedback.trim() || feedback.trim().length < 10) return;
    forkMutation.mutate(
      {
        workflowId,
        milestoneId,
        feedback: feedback.trim(),
        pauseOriginal,
        branchName: branchName.trim() || undefined,
      },
      {
        onSuccess: () => {
          setFeedback('');
          setBranchName('');
          onClose();
        },
      }
    );
  };

  const handleClose = () => {
    if (!forkMutation.isPending) {
      setFeedback('');
      setBranchName('');
      onClose();
    }
  };

  const isValid = feedback.trim().length >= 10;

  return (
    <Modal isOpen={isOpen} onClose={handleClose} title={t('autoForkFromHere', language)}>
      <div className="mb-3">
        <p className="text-muted small">{t('autoForkDesc', language)}</p>
        <p className="fw-semibold small">{milestoneTitle}</p>
      </div>

      <div className="mb-3">
        <label className="form-label fw-semibold">{t('autoForkInstructionsLabel', language)}</label>
        <textarea
          className="form-control"
          rows={4}
          value={feedback}
          onChange={(e) => setFeedback(e.target.value)}
          placeholder={t('autoFeedbackPlaceholder', language)}
          disabled={forkMutation.isPending}
        />
        {!isValid && feedback.length > 0 && (
          <div className="form-text text-danger">{t('autoForkFeedbackRequired', language)}</div>
        )}
      </div>

      <div className="mb-3">
        <label className="form-label fw-semibold">{t('autoForkBranchLabel', language)}</label>
        <input
          type="text"
          className="form-control"
          value={branchName}
          onChange={(e) => setBranchName(e.target.value)}
          placeholder={`fork/from-${milestoneId.slice(0, 8)}`}
          disabled={forkMutation.isPending}
        />
      </div>

      <div className="mb-3">
        <label className="form-label fw-semibold">{t('autoForkOriginalBehavior', language)}</label>
        <div className="form-check">
          <input
            className="form-check-input"
            type="radio"
            name="forkBehavior"
            id="forkPause"
            checked={pauseOriginal}
            onChange={() => setPauseOriginal(true)}
            disabled={forkMutation.isPending}
          />
          <label className="form-check-label" htmlFor="forkPause">
            {t('autoForkPauseOriginal', language)}
          </label>
        </div>
        <div className="form-check">
          <input
            className="form-check-input"
            type="radio"
            name="forkBehavior"
            id="forkContinue"
            checked={!pauseOriginal}
            onChange={() => setPauseOriginal(false)}
            disabled={forkMutation.isPending}
          />
          <label className="form-check-label" htmlFor="forkContinue">
            {t('autoForkContinueOriginal', language)}
          </label>
        </div>
      </div>

      <div className="d-flex justify-content-end gap-2">
        <Button variant="secondary" onClick={handleClose} disabled={forkMutation.isPending}>
          {t('cancel', language)}
        </Button>
        <Button variant="info" onClick={handleSubmit} disabled={!isValid || forkMutation.isPending}>
          {forkMutation.isPending ? t('autoCreating', language) : t('autoForkFromHere', language)}
        </Button>
      </div>
    </Modal>
  );
}
