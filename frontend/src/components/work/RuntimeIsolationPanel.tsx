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

import { useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Badge } from '@/components/common';
import type { AutonomousWorkflow } from '@/api/autonomous';

export interface EffectivePolicySnapshot {
  schema_version?: number;
  provider?: string;
  capabilities?: string[];
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
  raw: string | null | undefined,
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

export function RuntimeIsolationPanel({ workflow }: RuntimeIsolationPanelProps) {
  const language = useLanguage();
  const snapshot = useMemo(
    () => parseEffectivePolicy(workflow.sandbox_effective_policy),
    [workflow.sandbox_effective_policy],
  );

  if (!snapshot) return null;

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
      value: limits.ephemeral_storage_limit
        ? formatBytes(limits.ephemeral_storage_limit)
        : '—',
      enforced: enforced.ephemeral_storage,
    },
    {
      labelKey: 'autoPolicyInode',
      value: limits.inode_limit ? String(limits.inode_limit) : '—',
      enforced: enforced.inode,
    },
  ];

  return (
    <details className="runtime-isolation-panel mb-2" data-testid="runtime-isolation-panel">
      <summary className="runtime-isolation-panel__summary">
        <i className="bi bi-shield-lock me-1"></i>
        {t('autoPolicyPanelTitle', language)}
        {snapshot.provider ? (
          <Badge variant="secondary" className="ms-2">
            {snapshot.provider}
          </Badge>
        ) : null}
      </summary>
      <div className="runtime-isolation-panel__body p-2">
        {snapshot.capabilities && snapshot.capabilities.length > 0 ? (
          <div className="mb-2">
            <div className="text-muted small mb-1">{t('autoPolicyCapabilities', language)}</div>
            <div className="d-flex flex-wrap gap-1">
              {snapshot.capabilities.map((cap) => (
                <Badge key={cap} variant="info">
                  {cap}
                </Badge>
              ))}
            </div>
          </div>
        ) : (
          <div className="text-muted small mb-2">
            <i className="bi bi-exclamation-triangle me-1"></i>
            {t('autoPolicyNoCapabilities', language)}
          </div>
        )}
        <table className="table table-sm mb-0 runtime-isolation-panel__table">
          <thead>
            <tr>
              <th>{t('autoPolicyLimit', language)}</th>
              <th>{t('autoPolicyValue', language)}</th>
              <th>{t('autoPolicyEnforced', language)}</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => (
              <tr key={row.labelKey}>
                <td>{t(row.labelKey, language)}</td>
                <td className="font-monospace">{row.value}</td>
                <td>
                  {row.enforced ? (
                    <Badge variant="success">{t('autoPolicyEnforcedYes', language)}</Badge>
                  ) : (
                    <Badge variant="warning">{t('autoPolicyEnforcedNo', language)}</Badge>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}
