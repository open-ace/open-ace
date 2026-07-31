/**
 * RuntimeIsolationPanel — read-only display of the effective resource/isolation
 * policy for a workflow's sandbox (#2020 Phase B).
 *
 * Renders nothing unless the workflow carries a `sandbox_effective_policy`
 * snapshot (written by the orchestrator at sandbox-create time). The `enforced`
 * flags come straight from the provider's DECLARED capabilities — the panel
 * never decides on its own whether a limit is real, so it cannot misrepresent
 * an unsupported dimension as protected (the #2082 lesson).
 */

import { useId, useMemo, useState } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Badge } from '@/components/common';
import type { AutonomousWorkflow } from '@/api/autonomous';
import './RuntimeIsolationPanel.css';

export interface EffectivePolicySnapshot {
  schema_version?: number;
  provider?: string;
  capabilities?: string[];
  policy_configured?: boolean;
  limits?: {
    memory_max_bytes?: number;
    pids_max?: number;
    cpu_max?: string;
    wall_clock_limit?: number;
    ephemeral_storage_limit?: number;
    inode_limit?: number;
  };
  cgroup_enabled?: string;
  task_root?: string;
  enforced?: {
    memory?: boolean;
    pids?: boolean;
    cpu?: boolean;
    wall_clock?: boolean;
    ephemeral_storage?: boolean;
    inode?: boolean;
  };
}

interface RuntimeIsolationPanelProps {
  workflow: AutonomousWorkflow;
}

/** Parse the snapshot JSON; return null on absent/malformed data. */
export function parseEffectivePolicy(
  raw: string | null | undefined
): EffectivePolicySnapshot | null {
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as EffectivePolicySnapshot;
    return parsed && typeof parsed === 'object' ? parsed : null;
  } catch {
    return null;
  }
}

function formatBytes(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return '—';
  const units = ['B', 'KiB', 'MiB', 'GiB', 'TiB'];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value % 1 === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

function formatSeconds(seconds: number | undefined): string {
  if (!seconds || seconds <= 0) return '—';
  if (seconds >= 3600) {
    const h = seconds / 3600;
    return `${h % 1 === 0 ? h : h.toFixed(1)}h`;
  }
  if (seconds >= 60) return `${Math.round(seconds / 60)}m`;
  return `${seconds}s`;
}

interface LimitRow {
  labelKey: string;
  value: string;
  enforced?: boolean;
}

const SUMMARY_LIMIT_KEYS = [
  'autoPolicyMemory',
  'autoPolicyPids',
  'autoPolicyCpu',
  'autoPolicyWallClock',
  'autoPolicyStorage',
] as const;

export function RuntimeIsolationPanel({ workflow }: RuntimeIsolationPanelProps) {
  const language = useLanguage();
  const [expanded, setExpanded] = useState(false);
  const bodyId = useId();
  const snapshot = useMemo(
    () => parseEffectivePolicy(workflow.sandbox_effective_policy),
    [workflow.sandbox_effective_policy]
  );

  if (!snapshot) return null;

  const policyConfigured = snapshot.policy_configured !== false;
  const limits = snapshot.limits ?? {};
  const enforced = snapshot.enforced ?? {};
  const rows: LimitRow[] = [
    {
      labelKey: 'autoPolicyMemory',
      value: limits.memory_max_bytes ? formatBytes(limits.memory_max_bytes) : '—',
      enforced: enforced.memory,
    },
    {
      labelKey: 'autoPolicyPids',
      value: limits.pids_max ? String(limits.pids_max) : '—',
      enforced: enforced.pids,
    },
    {
      labelKey: 'autoPolicyCpu',
      // cpu_max uses '' as the unset sentinel (build_effective_policy), so an
      // empty string must render as '—'; nullish coalescing (??) would show ''.
      // eslint-disable-next-line @typescript-eslint/prefer-nullish-coalescing
      value: limits.cpu_max || '—',
      enforced: enforced.cpu,
    },
    {
      labelKey: 'autoPolicyWallClock',
      value: formatSeconds(limits.wall_clock_limit),
      enforced: enforced.wall_clock,
    },
    {
      labelKey: 'autoPolicyStorage',
      value: limits.ephemeral_storage_limit ? formatBytes(limits.ephemeral_storage_limit) : '—',
      enforced: enforced.ephemeral_storage,
    },
    {
      labelKey: 'autoPolicyInode',
      value: limits.inode_limit ? String(limits.inode_limit) : '—',
      enforced: enforced.inode,
    },
  ];
  const visibleSummaryRows: LimitRow[] | null = policyConfigured
    ? (() => {
        const summaryRows: LimitRow[] = SUMMARY_LIMIT_KEYS.flatMap((labelKey) => {
          const row = rows.find((limitRow) => limitRow.labelKey === labelKey);
          return row && row.value !== '—' ? [row] : [];
        });
        return summaryRows.length > 0 ? summaryRows : rows.slice(0, 4);
      })()
    : null;

  return (
    <div className="runtime-isolation-panel" data-testid="runtime-isolation-panel">
      <button
        type="button"
        className="runtime-isolation-panel__toggle"
        aria-expanded={expanded}
        aria-controls={expanded ? bodyId : undefined}
        aria-label={expanded ? t('collapse', language) : t('expand', language)}
        onClick={() => setExpanded((open) => !open)}
      >
        <div className="runtime-isolation-panel__header">
          <div className="runtime-isolation-panel__title-group">
            <span className="runtime-isolation-panel__title">
              <i className="bi bi-shield-lock"></i>
              {t('autoPolicyPanelTitle', language)}
            </span>
            {snapshot.provider ? (
              <span className="runtime-isolation-panel__provider">{snapshot.provider}</span>
            ) : null}
          </div>
          <span className="runtime-isolation-panel__chevron" aria-hidden="true">
            <i className={`bi ${expanded ? 'bi-chevron-up' : 'bi-chevron-down'}`}></i>
          </span>
        </div>
        <div className="runtime-isolation-panel__summary-pills">
          {visibleSummaryRows ? (
            visibleSummaryRows.map((row) => (
              <span key={row.labelKey} className="runtime-isolation-panel__summary-pill">
                <span className="runtime-isolation-panel__summary-label">
                  {t(row.labelKey, language)}
                </span>
                <span className="runtime-isolation-panel__summary-value">{row.value}</span>
              </span>
            ))
          ) : (
            <span className="runtime-isolation-panel__summary-pill runtime-isolation-panel__summary-pill--warning">
              <i className="bi bi-exclamation-circle"></i>
              <span>{t('autoPolicyNotConfigured', language)}</span>
            </span>
          )}
        </div>
      </button>

      {expanded && (
        <div id={bodyId} className="runtime-isolation-panel__body">
          {!policyConfigured ? (
            <div className="runtime-isolation-panel__notice runtime-isolation-panel__notice--warning">
              <i className="bi bi-exclamation-triangle me-1"></i>
              {t('autoPolicyConfigMissing', language)}
            </div>
          ) : null}

          {snapshot.capabilities && snapshot.capabilities.length > 0 ? (
            <div className="runtime-isolation-panel__section">
              <div className="runtime-isolation-panel__section-label">
                {t('autoPolicyCapabilities', language)}
              </div>
              <div className="runtime-isolation-panel__capabilities">
                {snapshot.capabilities.map((cap) => (
                  <Badge key={cap} variant="info">
                    {cap}
                  </Badge>
                ))}
              </div>
            </div>
          ) : (
            <div className="runtime-isolation-panel__notice">
              <i className="bi bi-exclamation-triangle me-1"></i>
              {t('autoPolicyNoCapabilities', language)}
            </div>
          )}

          {policyConfigured ? (
            <div className="runtime-isolation-panel__section">
              <div className="runtime-isolation-panel__section-label">
                {t('autoPolicyLimit', language)}
              </div>
              <div className="runtime-isolation-panel__limit-grid">
                {rows.map((row) => (
                  <div key={row.labelKey} className="runtime-isolation-panel__limit-card">
                    <div className="runtime-isolation-panel__limit-header">
                      <span className="runtime-isolation-panel__limit-label">
                        {t(row.labelKey, language)}
                      </span>
                      <Badge variant={row.enforced ? 'success' : 'warning'}>
                        {row.enforced
                          ? t('autoPolicyEnforcedYes', language)
                          : t('autoPolicyEnforcedNo', language)}
                      </Badge>
                    </div>
                    <div className="runtime-isolation-panel__limit-value">{row.value}</div>
                  </div>
                ))}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
}
