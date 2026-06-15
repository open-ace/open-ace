import type { BadgeVariant } from '@/components/common';

export interface AutonomousWorkflowStatusConfig {
  variant: BadgeVariant;
  icon: string;
  labelKey: string;
  tone: 'info' | 'warning' | 'success' | 'danger';
}

export const AUTONOMOUS_WORKFLOW_STATUS_CONFIG: Record<string, AutonomousWorkflowStatusConfig> = {
  queued: {
    variant: 'secondary',
    icon: 'bi-hourglass-split',
    labelKey: 'autoStatusQueued',
    tone: 'info',
  },
  pending: {
    variant: 'secondary',
    icon: 'bi-hourglass',
    labelKey: 'autoStatusPending',
    tone: 'info',
  },
  preparing: {
    variant: 'info',
    icon: 'bi-gear',
    labelKey: 'autoStatusPreparing',
    tone: 'info',
  },
  planning: {
    variant: 'info',
    icon: 'bi-lightbulb',
    labelKey: 'autoStatusPlanning',
    tone: 'info',
  },
  developing: {
    variant: 'primary',
    icon: 'bi-code-slash',
    labelKey: 'autoStatusDeveloping',
    tone: 'info',
  },
  pr_review: {
    variant: 'warning',
    icon: 'bi-eye',
    labelKey: 'autoStatusPRReview',
    tone: 'warning',
  },
  reporting: {
    variant: 'info',
    icon: 'bi-file-text',
    labelKey: 'autoStatusReporting',
    tone: 'info',
  },
  waiting: {
    variant: 'secondary',
    icon: 'bi-clock',
    labelKey: 'autoStatusWaiting',
    tone: 'warning',
  },
  merging: {
    variant: 'info',
    icon: 'bi-git-merge',
    labelKey: 'autoStatusMerging',
    tone: 'info',
  },
  completed: {
    variant: 'success',
    icon: 'bi-check-circle',
    labelKey: 'autoStatusCompleted',
    tone: 'success',
  },
  failed: {
    variant: 'danger',
    icon: 'bi-x-circle',
    labelKey: 'autoStatusFailed',
    tone: 'danger',
  },
  cancelled: {
    variant: 'secondary',
    icon: 'bi-slash-circle',
    labelKey: 'autoStatusCancelled',
    tone: 'warning',
  },
  paused: {
    variant: 'warning',
    icon: 'bi-pause-circle',
    labelKey: 'autoStatusPaused',
    tone: 'warning',
  },
  planning_timeout: {
    variant: 'warning',
    icon: 'bi-clock-history',
    labelKey: 'autoStatusPlanningTimeout',
    tone: 'warning',
  },
};

export function getAutonomousWorkflowStatusConfig(status: string): AutonomousWorkflowStatusConfig {
  return AUTONOMOUS_WORKFLOW_STATUS_CONFIG[status] ?? AUTONOMOUS_WORKFLOW_STATUS_CONFIG.pending;
}

