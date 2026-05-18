/**
 * RemoteMachineSelector Component - Enhanced machine selector for remote workspace
 *
 * Features:
 * - Display available machines with detailed info
 * - Machine status indicator
 * - Quick filter/search
 * - Save selected machine preference
 *
 * Issue #317: Remote workspace lacks project creation functionality
 */

import React, { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { remoteApi, type RemoteMachine } from '@/api/remote';
import { Loading, EmptyState, Badge } from '@/components/common';

interface RemoteMachineSelectorProps {
  onSelectMachine: (machineId: string, machine: RemoteMachine) => void;
  selectedMachineId?: string;
  machines?: RemoteMachine[]; // Optional: pass machines from parent
  isLoading?: boolean; // Optional: pass loading state from parent
}

// Local storage key for last selected machine
const LAST_MACHINE_KEY = 'last-selected-machine';

export const RemoteMachineSelector: React.FC<RemoteMachineSelectorProps> = ({
  onSelectMachine,
  selectedMachineId,
  machines: externalMachines,
  isLoading: externalLoading,
}) => {
  const language = useLanguage();
  const [machines, setMachines] = useState<RemoteMachine[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchFilter, setSearchFilter] = useState('');
  const [sortBy, setSortBy] = useState<'name' | 'status' | 'lastHeartbeat'>('name');

  // Use ref to avoid onSelectMachine dependency causing re-renders
  const onSelectMachineRef = useRef(onSelectMachine);
  onSelectMachineRef.current = onSelectMachine;

  // Track if we've already auto-selected (to prevent re-selection on re-render)
  const hasAutoSelectedRef = useRef(false);

  // Update machines when external data changes
  useEffect(() => {
    if (externalMachines !== undefined) {
      setMachines(externalMachines);
      return;
    }

    // Only load internally if no external machines provided
    const loadMachines = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const result = await remoteApi.getAvailableMachines();
        if (result.success) {
          setMachines(result.machines);

          // Auto-select last used machine if available (only once)
          if (!hasAutoSelectedRef.current && !selectedMachineId) {
            const lastMachineId = localStorage.getItem(LAST_MACHINE_KEY);
            if (lastMachineId) {
              const lastMachine = result.machines.find((m) => m.machine_id === lastMachineId);
              if (lastMachine) {
                hasAutoSelectedRef.current = true;
                onSelectMachineRef.current(lastMachineId, lastMachine);
              }
            }
          }
        }
      } catch (err) {
        setError((err as Error)?.message || 'Failed to load machines');
      } finally {
        setIsLoading(false);
      }
    };

    loadMachines();
  }, [externalMachines, selectedMachineId]); // Removed onSelectMachine from deps

  // Filter and sort machines
  const filteredMachines = useMemo(() => {
    let filtered = machines;

    // Apply search filter
    if (searchFilter) {
      const lowerFilter = searchFilter.toLowerCase();
      filtered = filtered.filter(
        (m) =>
          m.machine_name.toLowerCase().includes(lowerFilter) ||
          (m.hostname?.toLowerCase().includes(lowerFilter) ?? false) ||
          (m.ip_address?.toLowerCase().includes(lowerFilter) ?? false)
      );
    }

    // Apply sort
    filtered = [...filtered].sort((a, b) => {
      switch (sortBy) {
        case 'name':
          return a.machine_name.localeCompare(b.machine_name);
        case 'status':
          // Connected machines first
          return (b.connected ? 1 : 0) - (a.connected ? 1 : 0);
        case 'lastHeartbeat': {
          // Most recent heartbeat first
          const aTime = a.last_heartbeat ? new Date(a.last_heartbeat).getTime() : 0;
          const bTime = b.last_heartbeat ? new Date(b.last_heartbeat).getTime() : 0;
          return bTime - aTime;
        }
        default:
          return 0;
      }
    });

    return filtered;
  }, [machines, searchFilter, sortBy]);

  // Handle machine selection
  const handleSelect = (machine: RemoteMachine) => {
    localStorage.setItem(LAST_MACHINE_KEY, machine.machine_id);
    onSelectMachine(machine.machine_id, machine);
  };

  // Get status badge variant
  const getStatusBadge = (machine: RemoteMachine) => {
    if (machine.connected) {
      return <Badge variant="success">{t('online', language)}</Badge>;
    }
    if (machine.status === 'offline') {
      return <Badge variant="secondary">{t('offline', language)}</Badge>;
    }
    if (machine.status === 'error') {
      return <Badge variant="danger">{t('error', language)}</Badge>;
    }
    return <Badge variant="warning">{machine.status}</Badge>;
  };

  // Get OS icon
  const getOsIcon = (osType: string | null) => {
    if (!osType) return 'bi-desktop';
    const os = osType.toLowerCase();
    if (os.includes('windows')) return 'bi-windows';
    if (os.includes('darwin') || os.includes('mac')) return 'bi-apple';
    if (os.includes('linux')) return 'bi-ubuntu';
    return 'bi-desktop';
  };

  // Format last heartbeat time
  const formatLastHeartbeat = (heartbeat: string | null) => {
    if (!heartbeat) return t('never', language) || 'Never';
    const date = new Date(heartbeat);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);

    if (diffMins < 1) return t('justNow', language) || 'Just now';
    if (diffMins < 60) return `${diffMins} ${t('minutesAgo', language) || 'min ago'}`;
    if (diffMins < 1440)
      return `${Math.floor(diffMins / 60)} ${t('hoursAgo', language) || 'h ago'}`;
    return date.toLocaleDateString();
  };

  const loading = externalLoading ?? isLoading;

  return (
    <div className="remote-machine-selector">
      {/* Search and Sort */}
      <div className="mb-3">
        <div className="d-flex gap-2">
          <div className="input-group input-group-sm flex-grow-1">
            <span className="input-group-text">
              <i className="bi bi-search" />
            </span>
            <input
              type="text"
              className="form-control"
              value={searchFilter}
              onChange={(e) => setSearchFilter(e.target.value)}
              placeholder={t('searchMachines', language) || 'Search machines...'}
            />
          </div>
          <select
            className="form-select form-select-sm"
            value={sortBy}
            onChange={(e) => setSortBy(e.target.value as typeof sortBy)}
            style={{ width: 'auto' }}
          >
            <option value="name">{t('sortBy', language) || 'Sort by'} Name</option>
            <option value="status">{t('sortBy', language) || 'Sort by'} Status</option>
            <option value="lastHeartbeat">{t('sortBy', language) || 'Sort by'} Last Active</option>
          </select>
        </div>
      </div>

      {/* Error message */}
      {error && (
        <div className="alert alert-danger small mb-3">
          <i className="bi bi-exclamation-triangle me-1" />
          {error}
        </div>
      )}

      {/* Machine list */}
      {loading ? (
        <Loading size="sm" text={t('loadingMachines', language) || 'Loading machines...'} />
      ) : filteredMachines.length === 0 ? (
        searchFilter ? (
          <EmptyState
            icon="bi-search"
            title={t('noResults', language) || 'No Results'}
            description={t('noMatchingMachines', language) || 'No machines match your search'}
          />
        ) : (
          <EmptyState
            icon="bi-pc-display"
            title={t('noAvailableMachines', language) || 'No Available Machines'}
            description={t('noAvailableMachinesDesc', language) || 'No remote machines configured'}
          />
        )
      ) : (
        <div className="list-group" style={{ maxHeight: '300px', overflow: 'auto' }}>
          {filteredMachines.map((machine) => (
            <button
              key={machine.machine_id}
              className={`list-group-item list-group-item-action ${
                selectedMachineId === machine.machine_id ? 'active' : ''
              }`}
              onClick={() => handleSelect(machine)}
            >
              <div className="d-flex justify-content-between align-items-start">
                <div>
                  <div className="d-flex align-items-center gap-2">
                    <i className={`bi ${getOsIcon(machine.os_type)} fs-5`} />
                    <strong>{machine.machine_name}</strong>
                  </div>
                  <div className="text-muted small">
                    {machine.hostname ?? machine.machine_id.slice(0, 8)}
                    {machine.ip_address && ` | ${machine.ip_address}`}
                  </div>
                  {machine.work_dir && (
                    <div className="text-muted small mt-1">
                      <i className="bi bi-folder me-1" />
                      {machine.work_dir}
                    </div>
                  )}
                </div>
                <div className="text-end">
                  {getStatusBadge(machine)}
                  {machine.agent_version && (
                    <div className="text-muted small mt-1">v{machine.agent_version}</div>
                  )}
                </div>
              </div>

              {/* Last heartbeat */}
              <div className="mt-2 small text-muted">
                <i className="bi bi-clock me-1" />
                {t('lastActive', language) || 'Last active'}:{' '}
                {formatLastHeartbeat(machine.last_heartbeat)}
              </div>

              {/* Selected indicator */}
              {selectedMachineId === machine.machine_id && (
                <div className="mt-1">
                  <i className="bi bi-check-circle-fill text-success me-1" />
                  {t('selected', language) || 'Selected'}
                </div>
              )}
            </button>
          ))}
        </div>
      )}

      {/* Machine count */}
      {!loading && machines.length > 0 && (
        <div className="text-muted small mt-2">
          {filteredMachines.length} / {machines.length} {t('machines', language) || 'machines'}
        </div>
      )}
    </div>
  );
};
