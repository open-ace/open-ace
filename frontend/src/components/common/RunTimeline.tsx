/**
 * RunTimeline Component - Persisted provenance timeline for a remote session
 *
 * Shows the durable run record (status, attribution, cumulative usage) plus the
 * append-only event stream and the durable permission approvals recorded by the
 * backend run_timeline module. Reuses the common Card / Badge / Loading /
 * EmptyState / Error primitives.
 *
 * The whole feature is gated by the backend config flag ``run_timeline.enabled``:
 * when it is off, the events API returns ``{ disabled: true }`` and this
 * component renders nothing, so callers can mount it unconditionally.
 */

import React, { useState } from 'react';
import { t, type Language } from '@/i18n';
import { Badge } from './Badge';
import type { BadgeVariant } from './Badge';
import { Loading } from './Loading';
import { Error, EmptyState } from './Error';
import { formatDateTime, formatTimestampWithSeconds, formatTokens } from '@/utils';
import { useRunEvents, useRunApprovals } from '@/hooks';
import type { AgentRun, RunEvent, AgentApproval } from '@/api';

interface RunTimelineProps {
  sessionId: string;
  language: Language;
}

/** Display config per recorded event_type (icon + tone + i18n label key). */
const EVENT_CONFIG: Record<string, { icon: string; variant: BadgeVariant; labelKey: string }> = {
  session_created: {
    icon: 'bi-play-circle',
    variant: 'primary',
    labelKey: 'runEventSessionCreated',
  },
  user_message: { icon: 'bi-person-fill', variant: 'primary', labelKey: 'runEventUserMessage' },
  assistant_output: { icon: 'bi-robot', variant: 'success', labelKey: 'runEventAssistantOutput' },
  tool_use: { icon: 'bi-tools', variant: 'info', labelKey: 'runEventToolUse' },
  permission_requested: {
    icon: 'bi-shield-lock',
    variant: 'warning',
    labelKey: 'runEventPermissionRequested',
  },
  permission_answered: {
    icon: 'bi-shield-check',
    variant: 'info',
    labelKey: 'runEventPermissionAnswered',
  },
  usage_reported: {
    icon: 'bi-bar-chart-line',
    variant: 'secondary',
    labelKey: 'runEventUsageReported',
  },
  stop: { icon: 'bi-stop-circle', variant: 'secondary', labelKey: 'runEventStop' },
  error: { icon: 'bi-x-circle', variant: 'danger', labelKey: 'runEventError' },
  pause: { icon: 'bi-pause-circle', variant: 'warning', labelKey: 'runEventPause' },
  resume: { icon: 'bi-arrow-clockwise', variant: 'success', labelKey: 'runEventResume' },
  request_aborted: {
    icon: 'bi-slash-circle',
    variant: 'warning',
    labelKey: 'runEventRequestAborted',
  },
};

const DEFAULT_EVENT_CONFIG = {
  icon: 'bi-circle',
  variant: 'secondary' as BadgeVariant,
  labelKey: 'runEventDefault',
};

const APPROVAL_VARIANT: Record<string, BadgeVariant> = {
  pending: 'warning',
  approved: 'success',
  denied: 'danger',
};

function getRunStatusVariant(status: string): BadgeVariant {
  switch (status) {
    case 'active':
      return 'success';
    case 'completed':
    case 'stopped':
      return 'secondary';
    case 'error':
      return 'danger';
    case 'paused':
      return 'warning';
    default:
      return 'info';
  }
}

export const RunTimeline: React.FC<RunTimelineProps> = ({ sessionId, language }) => {
  const { data, isLoading, isError, error, refetch } = useRunEvents(sessionId);
  // Approvals are only meaningful once the run timeline is enabled.
  const disabled = !!data?.disabled;
  const approvalsQuery = useRunApprovals(disabled ? null : sessionId);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});

  // Feature disabled on the backend → render nothing (the UI hides itself).
  if (disabled) return null;

  if (isLoading) {
    return (
      <div className="mt-3">
        <h6 className="mb-2">
          <i className="bi bi-clock-history me-1" />
          {t('runTimeline', language)}
        </h6>
        <Loading size="sm" text={t('runTimelineLoading', language)} />
      </div>
    );
  }

  if (isError) {
    return (
      <div className="mt-3">
        <h6 className="mb-2">
          <i className="bi bi-clock-history me-1" />
          {t('runTimeline', language)}
        </h6>
        <Error
          message={
            error && typeof error === 'object' && 'message' in error
              ? `${t('runTimelineError', language)}: ${(error as { message?: string }).message}`
              : t('runTimelineError', language)
          }
          onRetry={() => refetch()}
        />
      </div>
    );
  }

  const run = data?.run ?? null;
  const events = data?.events ?? [];
  const approvals = approvalsQuery.data?.approvals ?? [];

  return (
    <div className="run-timeline mt-3" data-testid="run-timeline">
      <h6 className="mb-2 d-flex align-items-center">
        <i className="bi bi-clock-history me-1" />
        {t('runTimeline', language)}
        {run && (
          <Badge variant={getRunStatusVariant(run.status)} className="ms-2">
            {run.status}
          </Badge>
        )}
      </h6>

      {/* Run summary: attribution + cumulative usage */}
      {run && <RunSummary run={run} language={language} />}

      {/* Event stream */}
      <div className="run-timeline-events" style={{ maxHeight: '420px', overflowY: 'auto' }}>
        {events.length === 0 ? (
          <EmptyState icon="bi-clock-history" title={t('runTimelineEmpty', language)} />
        ) : (
          events.map((event, idx) => (
            <TimelineEventRow
              key={event.id ?? `${event.event_type}-${idx}`}
              event={event}
              language={language}
              expanded={!!expanded[event.id ?? `${event.event_type}-${idx}`]}
              onToggle={() =>
                setExpanded((cur) => {
                  const key = event.id ?? `${event.event_type}-${idx}`;
                  return { ...cur, [key]: !cur[key] };
                })
              }
            />
          ))
        )}
      </div>

      {/* Approvals */}
      {approvals.length > 0 && (
        <div className="mt-3">
          <h6 className="mb-2">
            <i className="bi bi-shield-check me-1" />
            {t('runTimelineApprovals', language)}
          </h6>
          <div className="run-timeline-approvals">
            {approvals.map((approval, idx) => (
              <ApprovalRow
                key={approval.request_id ?? idx}
                approval={approval}
                language={language}
              />
            ))}
          </div>
        </div>
      )}
    </div>
  );
};

const RunSummary: React.FC<{ run: AgentRun; language: Language }> = ({ run, language }) => {
  return (
    <div className="run-timeline-summary p-2 bg-light rounded mb-2 small">
      <div className="row g-2">
        <div className="col-md-3">
          <small className="text-muted d-block">{t('model', language) ?? 'Model'}</small>
          <span>{run.model ?? '-'}</span>
        </div>
        <div className="col-md-3">
          <small className="text-muted d-block">{t('provider', language) ?? 'Provider'}</small>
          <span>{run.provider ?? '-'}</span>
        </div>
        <div className="col-md-3">
          <small className="text-muted d-block">{t('totalTokens', language)}</small>
          <span>{formatTokens(run.total_tokens)}</span>
        </div>
        <div className="col-md-3">
          <small className="text-muted d-block">{t('totalRequests', language) ?? 'Requests'}</small>
          <span>{run.total_requests}</span>
        </div>
        {run.tool_name && (
          <div className="col-md-3">
            <small className="text-muted d-block">{t('toolName', language) ?? 'Tool'}</small>
            <span>{run.tool_name}</span>
          </div>
        )}
        {run.cli_tool && (
          <div className="col-md-3">
            <small className="text-muted d-block">{t('cliTool', language) ?? 'CLI Tool'}</small>
            <span>{run.cli_tool}</span>
          </div>
        )}
        {run.machine_id && (
          <div className="col-md-3">
            <small className="text-muted d-block">{t('machine', language) ?? 'Machine'}</small>
            <span className="text-break">{run.machine_id}</span>
          </div>
        )}
        {run.started_at && (
          <div className="col-md-3">
            <small className="text-muted d-block">{t('started', language) ?? 'Started'}</small>
            <span>{formatDateTime(run.started_at)}</span>
          </div>
        )}
      </div>
    </div>
  );
};

const TimelineEventRow: React.FC<{
  event: RunEvent;
  language: Language;
  expanded: boolean;
  onToggle: () => void;
}> = ({ event, language, expanded, onToggle }) => {
  const config = EVENT_CONFIG[event.event_type] ?? DEFAULT_EVENT_CONFIG;
  const content = event.content?.trim() ?? '';
  const hasMeta = !!event.metadata && Object.keys(event.metadata as object).length > 0;
  const expandable = content.length > 120 || hasMeta;

  const contentPreview = content
    ? expanded || content.length <= 120
      ? content
      : `${content.slice(0, 120)}...`
    : null;

  return (
    <div className="run-timeline-event d-flex align-items-start mb-2">
      <div className="run-timeline-event-icon me-2">
        <i
          className={`bi ${config.icon} text-${config.variant === 'secondary' ? 'muted' : config.variant}`}
        />
      </div>
      <div className="run-timeline-event-body flex-grow-1">
        <div className="d-flex justify-content-between align-items-center flex-wrap gap-1">
          <div className="d-flex align-items-center gap-1 flex-wrap">
            <Badge variant={config.variant}>
              {t(config.labelKey, language) ?? event.event_type}
            </Badge>
            {event.event_subtype && <Badge variant="light">{event.event_subtype}</Badge>}
            {event.tool_name && <Badge variant="light">{event.tool_name}</Badge>}
          </div>
          <small className="text-muted">
            {event.event_ts ? formatTimestampWithSeconds(event.event_ts) : ''}
          </small>
        </div>
        {contentPreview && (
          <div
            className="run-timeline-event-content mt-1 small"
            style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}
          >
            {contentPreview}
          </div>
        )}
        {expandable && (
          <button
            type="button"
            className="btn btn-link btn-sm p-0 run-timeline-event-toggle"
            onClick={onToggle}
          >
            {expanded ? t('showLess', language) : t('showMore', language)}
          </button>
        )}
      </div>
    </div>
  );
};

const ApprovalRow: React.FC<{ approval: AgentApproval; language: Language }> = ({
  approval,
  language,
}) => {
  const variant = APPROVAL_VARIANT[approval.status] ?? 'secondary';
  return (
    <div className="run-timeline-approval border rounded p-2 mb-2 small">
      <div className="d-flex justify-content-between align-items-center flex-wrap gap-1">
        <div className="d-flex align-items-center gap-1 flex-wrap">
          <Badge variant={variant}>
            {t(`runApproval${capitalize(approval.status)}`, language) ?? approval.status}
          </Badge>
          {approval.tool_name && <Badge variant="light">{approval.tool_name}</Badge>}
        </div>
        <small className="text-muted">
          {approval.decided_at
            ? formatDateTime(approval.decided_at)
            : approval.requested_at
              ? formatDateTime(approval.requested_at)
              : ''}
        </small>
      </div>
      <div className="text-muted mt-1 text-break">
        <code>{approval.request_id}</code>
        {approval.decided_by_name && (
          <span className="ms-2">
            {t('decidedBy', language) ?? 'by'} {approval.decided_by_name}
          </span>
        )}
      </div>
    </div>
  );
};

function capitalize(value: string): string {
  return value ? value.charAt(0).toUpperCase() + value.slice(1) : value;
}

export default RunTimeline;
