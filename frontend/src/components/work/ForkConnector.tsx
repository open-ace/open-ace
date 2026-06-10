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

interface ForkConnectorProps {
  feedback?: string;
  branchCount?: number;
}

export const ForkConnector: React.FC<ForkConnectorProps> = ({ feedback, branchCount = 2 }) => {
  const language = useLanguage();

  return (
    <div className="position-relative my-3">
      {/* Horizontal divider */}
      <hr className="my-0" />

      {/* Centered fork badge */}
      <div className="position-absolute top-0 start-50 translate-middle bg-body px-2">
        <Badge variant="info">
          <i className="bi bi-diagram-3 me-1"></i>
          {t('autoForkPoint', language)}
        </Badge>
      </div>

      {/* Feedback summary */}
      {feedback && (
        <div className="text-center mt-2">
          <small className="text-muted fst-italic">
            {feedback.length > 120 ? feedback.slice(0, 120) + '...' : feedback}
          </small>
        </div>
      )}

      {/* Vertical drop lines to each branch column */}
      <div className="d-flex mt-2" style={{ height: '14px', gap: '0' }}>
        {Array.from({ length: branchCount }, (_, i) => (
          <div
            key={i}
            style={{
              flex: 1,
              display: 'flex',
              justifyContent: 'center',
            }}
          >
            <div
              style={{
                width: '2px',
                height: '14px',
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
  const color = BRANCH_COLORS[colorIndex % BRANCH_COLORS.length];

  return (
    <div
      className="d-flex flex-column"
      style={{
        borderTop: `3px solid var(--bs-${color})`,
        minHeight: '100px',
      }}
    >
      {/* Branch header */}
      <div
        className="p-2 rounded-top text-center"
        style={{ backgroundColor: `var(--bs-${color}-bg-subtle, var(--bs-${color}-rgb))` }}
      >
        <div className="fw-semibold" style={{ fontSize: '0.85rem' }}>
          {title}
        </div>
        <div className="d-flex justify-content-center gap-1 mt-1">
          <Badge variant={color as 'primary' | 'success' | 'warning' | 'info'}>{status}</Badge>
          {branchName && (
            <Badge variant="light">
              <i className="bi bi-git me-1"></i>
              {branchName.length > 20 ? branchName.slice(0, 20) + '...' : branchName}
            </Badge>
          )}
        </div>
      </div>

      {/* Branch milestones */}
      <div className="flex-grow-1 p-2">{children}</div>
    </div>
  );
};
