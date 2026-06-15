/**
 * ForkConnector Component - Visual connector between shared and parallel branches
 *
 * Renders a horizontal divider with a centered fork badge and
 * feedback summary. Branch columns attach via their own top connectors.
 */

import React from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Badge } from '@/components/common';
import { getAutonomousWorkflowStatusConfig } from './autonomousWorkflowStatus';

interface ForkConnectorProps {
  feedback?: string;
  branchCount?: number;
}

export const ForkConnector: React.FC<ForkConnectorProps> = ({ feedback, branchCount = 2 }) => {
  const language = useLanguage();

  return (
    <div className="timeline-fork-connector position-relative my-3">
      <hr className="my-0" />

      <div className="position-absolute top-0 start-50 translate-middle px-2 timeline-fork-connector__badge">
        <Badge variant="info">
          <i className="bi bi-diagram-3 me-1"></i>
          {t('autoForkPoint', language)}
        </Badge>
      </div>

      {feedback && (
        <div className="timeline-fork-connector__feedback text-center mt-2">
          <small className="fst-italic">
            {feedback.length > 120 ? feedback.slice(0, 120) + '...' : feedback}
          </small>
        </div>
      )}

      <div
        className="d-flex mt-2 timeline-fork-connector__lines"
        style={{ height: '14px', gap: '0' }}
      >
        {Array.from({ length: branchCount }, (_, i) => (
          <div key={i} className="timeline-fork-connector__line-slot">
            <div
              className="timeline-fork-connector__line"
              style={{
                backgroundColor: `var(--bs-${i === 0 ? 'primary' : 'success'})`,
              }}
            />
          </div>
        ))}
      </div>
    </div>
  );
};

/**
 * BranchColumn - Wrapper for a single branch in the parallel view
 */
interface BranchColumnProps {
  title: string;
  status: string;
  branchName?: string;
  colorIndex: number;
  children: React.ReactNode;
}

const BRANCH_COLORS = ['primary', 'success', 'warning', 'info'];

export const BranchColumn: React.FC<BranchColumnProps> = ({
  title,
  status,
  branchName,
  colorIndex = 0,
  children,
}) => {
  const language = useLanguage();
  const color = BRANCH_COLORS[colorIndex % BRANCH_COLORS.length];
  const statusConfig = getAutonomousWorkflowStatusConfig(status);

  return (
    <div
      className="fork-branch-column d-flex flex-column"
      style={{
        borderTop: `3px solid var(--bs-${color})`,
        minHeight: '100px',
      }}
    >
      <div
        className="fork-branch-column__header p-2 rounded-top"
        style={{ backgroundColor: `var(--bs-${color}-bg-subtle, var(--bs-${color}-rgb))` }}
      >
        <div className="fork-branch-column__title fw-semibold">{title}</div>
        <div className="fork-branch-column__badges d-flex justify-content-center gap-1 mt-1">
          <Badge variant={statusConfig.variant}>{t(statusConfig.labelKey, language)}</Badge>
          {branchName && (
            <Badge variant="light">
              <i className="bi bi-git me-1"></i>
              {branchName.length > 20 ? branchName.slice(0, 20) + '...' : branchName}
            </Badge>
          )}
        </div>
      </div>

      <div className="fork-branch-column__body flex-grow-1 p-2">{children}</div>
    </div>
  );
};
