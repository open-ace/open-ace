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
    icon: 'bi-sign-merge-right',
    labelKey: 'autoStatusMerging',
    tone: 'info',
  },
  verification_pending: {
    variant: 'info',
    icon: 'bi-shield-check',
    labelKey: 'autoStatusVerificationPending',
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

// ── Pause reason categorization (#2634) ──────────────────────────────────────

export type PauseReasonCategory = 'acceptance_awaiting' | 'quota' | 'manual';

export interface PauseReasonInput {
  status: string;
  current_phase: string;
  error_message?: string | null;
}

export const PAUSE_REASON_CONFIG: Record<
  PauseReasonCategory,
  { variant: BadgeVariant; labelKey: string }
> = {
  // Needs explicit human action (accept / restart with feedback) — loudest tone.
  acceptance_awaiting: { variant: 'danger', labelKey: 'autoPauseReasonAcceptance' },
  quota: { variant: 'warning', labelKey: 'autoPauseReasonQuota' },
  manual: { variant: 'secondary', labelKey: 'autoPauseReasonManual' },
};

/**
 * Classify why a workflow is paused so the list/timeline can distinguish
 * "awaiting human acceptance review" (needs explicit action) from quota and
 * manual pauses. Returns null for non-paused workflows.
 */
export function getPauseReasonCategory(workflow: PauseReasonInput): PauseReasonCategory | null {
  if (workflow.status !== 'paused') {
    return null;
  }
  if (workflow.current_phase === 'acceptance_verification') {
    return 'acceptance_awaiting';
  }
  if ((workflow.error_message ?? '').startsWith('Quota exceeded')) {
    return 'quota';
  }
  return 'manual';
}

export const ACCEPTANCE_PAUSE_STALE_MS = 3 * 24 * 60 * 60 * 1000;

const BACKEND_DATETIME_RE = /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/;

/**
 * Parse a backend timestamp. The backend writes `"%Y-%m-%d %H:%M:%S"` in UTC
 * (space separator, no timezone), which `Date.parse` misreads: NaN in Safari
 * and LOCAL time in Chrome. Normalize that exact shape to ISO-8601 UTC before
 * parsing; anything else (already ISO) is parsed as-is.
 */
export function parseBackendTimestamp(value: string): number {
  if (BACKEND_DATETIME_RE.test(value)) {
    return Date.parse(`${value.replace(' ', 'T')}Z`);
  }
  return Date.parse(value);
}

export interface AcceptancePauseAgeInput {
  status: string;
  current_phase: string;
  paused_at?: string | null;
  updated_at?: string | null;
}

/** True when an acceptance-awaiting pause has sat unreviewed for over 3 days. */
export function isStaleAcceptancePause(
  workflow: AcceptancePauseAgeInput,
  now: number = Date.now()
): boolean {
  if (getPauseReasonCategory(workflow) !== 'acceptance_awaiting') {
    return false;
  }
  const anchor = workflow.paused_at ?? workflow.updated_at;
  if (!anchor) {
    return false;
  }
  const pausedMs = parseBackendTimestamp(anchor);
  if (Number.isNaN(pausedMs)) {
    return false;
  }
  return now - pausedMs > ACCEPTANCE_PAUSE_STALE_MS;
}
