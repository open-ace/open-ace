/**
 * NewSessionModal Component - Modal for creating new sessions (local, remote, or terminal)
 *
 * Features:
 * - Workspace type selection: Local / Remote / Terminal
 * - Machine selector for remote workspaces
 * - Project path input with auto-fill from machine work_dir
 * - Enhanced machine selector (Issue #317)
 * - Path history support (Issue #317)
 *
 * Supports two modes:
 * 1. Standalone (default): navigates to /work with URL params
 * 2. Embedded (with onCreateLocal/onCreateRemote): calls callbacks directly (used by Workspace "+")
 */

import React, { useState, useMemo, useEffect, useCallback } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAvailableMachines, useCreateRemoteSession } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button } from '@/components/common';
import { RemoteMachineSelector } from './RemoteMachineSelector';
import { DirectoryBrowserModal } from './DirectoryBrowserModal';
import type { RemoteMachine } from '@/api/remote';
import { remoteApi } from '@/api/remote';

interface NewSessionModalProps {
  isOpen?: boolean;
  show?: boolean;
  onClose: () => void;
  onCreateLocal?: () => void;
  onCreateRemote?: (params: {
    machineId: string;
    machineName: string;
    sessionId: string;
    projectPath: string;
  }) => void;
  onCreateTerminal?: (params: { machineId: string; machineName: string; workDir: string }) => void;
}

export const NewSessionModal: React.FC<NewSessionModalProps> = ({
  isOpen,
  show,
  onClose,
  onCreateLocal,
  onCreateRemote,
  onCreateTerminal,
}) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();

  const modalOpen = isOpen ?? show ?? false;

  const [workspaceType, setWorkspaceType] = useState<'local' | 'remote' | 'terminal'>('local');
  const [selectedMachineId, setSelectedMachineId] = useState<string>('');
  const [projectPath, setProjectPath] = useState('');
  const [isStartingTerminal, setIsStartingTerminal] = useState(false);
  const [showDirectoryBrowser, setShowDirectoryBrowser] = useState(false);
  const [pathHistory, setPathHistory] = useState<string[]>([]);

  const { data: machinesData, isLoading: machinesLoading } = useAvailableMachines();
  const createRemoteSession = useCreateRemoteSession();

  const machines = useMemo(() => machinesData?.machines ?? [], [machinesData]);

  // Get default workspace path based on OS type
  const getDefaultPath = (osType: string | null | undefined): string => {
    const os = (osType ?? '').toLowerCase();
    if (os.includes('windows')) {
      return 'C:\\workspace';
    }
    if (os.includes('darwin') || os.includes('mac')) {
      return '~/workspace';
    }
    return '/root/workspace';
  };

  // Handle machine selection from RemoteMachineSelector
  const handleMachineSelect = useCallback(
    (machineId: string, machine: RemoteMachine | undefined) => {
      setSelectedMachineId(machineId);
      if (machine) {
        setProjectPath(machine.work_dir ?? getDefaultPath(machine.os_type));
      }
    },
    []
  );

  // Auto-select the only available machine
  useEffect(() => {
    if (machines.length === 1 && !selectedMachineId) {
      const machine = machines[0];
      handleMachineSelect(machine.machine_id, machine);
    }
  }, [machines, selectedMachineId, handleMachineSelect]);

  // Load path history from localStorage when machine is selected
  useEffect(() => {
    if (selectedMachineId) {
      const savedHistory = localStorage.getItem(`remote-path-history-${selectedMachineId}`);
      if (savedHistory) {
        try {
          const parsed = JSON.parse(savedHistory);
          if (Array.isArray(parsed)) {
            setPathHistory(parsed.slice(0, 5));
          }
        } catch {
          // Ignore parse errors
        }
      } else {
        setPathHistory([]);
      }
    }
  }, [selectedMachineId]);

  // Save path to history
  const savePathToHistory = useCallback(
    (path: string) => {
      if (!path || !selectedMachineId) return;
      const newHistory = [path, ...pathHistory.filter((p) => p !== path)].slice(0, 5);
      setPathHistory(newHistory);
      localStorage.setItem(`remote-path-history-${selectedMachineId}`, JSON.stringify(newHistory));
    },
    [selectedMachineId, pathHistory]
  );

  // Select path from history
  const handleSelectFromHistory = useCallback((path: string) => {
    setProjectPath(path);
  }, []);

  const selectedMachine = useMemo(
    () => machines.find((m) => m.machine_id === selectedMachineId),
    [machines, selectedMachineId]
  );

  // Extract the last directory name from a path (cross-platform)
  const getLastPathPart = (path: string): string => {
    if (!path) return path;
    // Handle both Unix and Windows paths
    const parts = path.split(/[/\\]/).filter(Boolean);
    // For Windows drive like "C:", return it directly
    if (parts.length === 1 && parts[0].match(/^[A-Za-z]:$/)) {
      return parts[0];
    }
    return parts[parts.length - 1] || path;
  };

  const handleCreateLocal = () => {
    onClose();
    if (onCreateLocal) {
      onCreateLocal();
      return;
    }
    const isWorkspacePage = location.pathname === '/work' || location.pathname === '/work/';
    if (isWorkspacePage) {
      navigate('/work?newTab=true', { replace: true });
    } else {
      navigate('/work');
    }
  };

  const handleCreateRemote = async () => {
    if (!selectedMachineId || !projectPath) return;

    try {
      // Fetch ha_pool_token required by the backend for qwen-code sessions
      let haPoolToken: string | undefined;
      try {
        const modelsResp = await remoteApi.getSessionModels({
          workspace_type: 'remote',
          machine_id: selectedMachineId,
        });
        haPoolToken = modelsResp.ha_pool_token;
      } catch (err) {
        console.warn('Failed to fetch ha_pool_token:', err);
      }

      const result = await createRemoteSession.mutateAsync({
        machine_id: selectedMachineId,
        project_path: projectPath,
        ha_pool_token: haPoolToken,
      });

      onClose();

      const machineName = selectedMachine?.machine_name ?? selectedMachineId.slice(0, 8);
      const sessionId = result.session?.session_id || '';

      if (onCreateRemote) {
        onCreateRemote({ machineId: selectedMachineId, machineName, sessionId, projectPath });
      } else {
        const isWorkspacePage = location.pathname === '/work' || location.pathname === '/work/';
        if (isWorkspacePage) {
          const params = new URLSearchParams({
            newTab: 'true',
            workspaceType: 'remote',
            machineId: selectedMachineId,
            machineName,
          });
          if (sessionId) {
            params.set('sessionId', sessionId);
          }
          navigate(`/work?${params.toString()}`, { replace: true });
        } else {
          navigate('/work');
        }
      }

      // Reset state
      setSelectedMachineId('');
      setProjectPath('');
      setWorkspaceType('local');
    } catch (err) {
      console.error('Failed to create remote session:', err);
    }
  };

  const handleCreateTerminal = async () => {
    if (!selectedMachineId) return;

    setIsStartingTerminal(true);
    try {
      const machineName = selectedMachine?.machine_name ?? selectedMachineId.slice(0, 8);

      if (onCreateTerminal) {
        await onCreateTerminal({
          machineId: selectedMachineId,
          machineName,
          workDir: projectPath || getDefaultPath(selectedMachine?.os_type),
        });
      }
      setSelectedMachineId('');
      setProjectPath('');
      setWorkspaceType('local');
      onClose();
    } finally {
      setIsStartingTerminal(false);
    }
  };

  const handleCreate = () => {
    if (workspaceType === 'local') {
      handleCreateLocal();
    } else if (workspaceType === 'terminal') {
      handleCreateTerminal();
    } else {
      handleCreateRemote();
    }
  };

  const canCreate =
    workspaceType === 'local' ||
    (workspaceType === 'terminal' && selectedMachineId) ||
    (workspaceType === 'remote' && selectedMachineId && projectPath);

  const isLoading = createRemoteSession.isPending || isStartingTerminal;

  return (
    <>
      <Modal
        isOpen={modalOpen}
        onClose={onClose}
        title={t('newSession', language)}
        footer={
          <>
            <Button variant="secondary" onClick={onClose}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleCreate}
              disabled={!canCreate}
              loading={isLoading}
            >
              {t('create', language)}
            </Button>
          </>
        }
      >
        {/* Workspace Type Selection */}
        <div className="mb-3">
          <label className="form-label">{t('selectWorkspaceType', language)}</label>
          <div className="d-flex gap-2">
            <button
              className={`btn flex-fill ${workspaceType === 'local' ? 'btn-primary' : 'btn-outline-primary'}`}
              onClick={() => setWorkspaceType('local')}
            >
              <i className="bi bi-laptop me-1" />
              {t('localWorkspace', language)}
            </button>
            <button
              className={`btn flex-fill ${workspaceType === 'remote' ? 'btn-primary' : 'btn-outline-primary'}`}
              onClick={() => setWorkspaceType('remote')}
            >
              <i className="bi bi-cloud me-1" />
              {t('remoteWorkspace', language)}
            </button>
            <button
              className={`btn flex-fill ${workspaceType === 'terminal' ? 'btn-primary' : 'btn-outline-primary'}`}
              onClick={() => setWorkspaceType('terminal')}
            >
              <i className="bi bi-terminal me-1" />
              {t('terminalWorkspace', language) || 'Terminal'}
            </button>
          </div>
        </div>

        {/* Remote / Terminal Options */}
        {(workspaceType === 'remote' || workspaceType === 'terminal') && (
          <>
            {/* Machine Selector - using RemoteMachineSelector for enhanced functionality */}
            <div className="mb-3">
              <label className="form-label">{t('selectMachine', language)}</label>
              <RemoteMachineSelector
                onSelectMachine={handleMachineSelect}
                selectedMachineId={selectedMachineId}
                machines={machines}
                isLoading={machinesLoading}
              />
            </div>

            {/* Project Path */}
            {selectedMachineId && workspaceType === 'remote' && (
              <div className="mb-3">
                <label className="form-label">{t('projectPath', language)}</label>
                <div className="input-group">
                  <input
                    type="text"
                    className="form-control"
                    value={projectPath}
                    onChange={(e) => setProjectPath(e.target.value)}
                    placeholder={
                      selectedMachine ? getDefaultPath(selectedMachine.os_type) : '/root/workspace'
                    }
                  />
                  <Button variant="outline-secondary" onClick={() => setShowDirectoryBrowser(true)}>
                    <i className="bi bi-folder2-open me-1" />
                    {t('browse', language) || 'Browse'}
                  </Button>
                </div>
                <div className="form-text text-muted small">{t('projectPathHint', language)}</div>

                {/* Path History */}
                {pathHistory.length > 0 && (
                  <div className="mt-2">
                    <small className="text-muted">
                      {t('recentPaths', language) || 'Recent Paths'}
                    </small>
                    <div className="d-flex gap-1 flex-wrap mt-1">
                      {pathHistory.map((path) => (
                        <button
                          key={path}
                          className="btn btn-sm btn-outline-secondary"
                          onClick={() => handleSelectFromHistory(path)}
                          title={path}
                        >
                          <i className="bi bi-folder me-1" />
                          {getLastPathPart(path)}
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {/* Terminal options */}
            {workspaceType === 'terminal' && selectedMachineId && (
              <>
                {/* Working directory for terminal */}
                <div className="mb-3">
                  <label className="form-label">
                    {t('terminalWorkDir', language) || 'Working Directory'}
                  </label>
                  <div className="input-group">
                    <input
                      type="text"
                      className="form-control"
                      value={projectPath}
                      onChange={(e) => setProjectPath(e.target.value)}
                      placeholder={
                        selectedMachine
                          ? getDefaultPath(selectedMachine.os_type)
                          : '/root/workspace'
                      }
                    />
                    <Button
                      variant="outline-secondary"
                      onClick={() => setShowDirectoryBrowser(true)}
                    >
                      <i className="bi bi-folder2-open me-1" />
                      {t('browse', language) || 'Browse'}
                    </Button>
                  </div>
                  <div className="form-text text-muted small">
                    {t('terminalWorkDirHint', language) || 'Terminal will open in this directory'}
                  </div>

                  {/* Path History */}
                  {pathHistory.length > 0 && (
                    <div className="mt-2">
                      <small className="text-muted">
                        {t('recentPaths', language) || 'Recent Paths'}
                      </small>
                      <div className="d-flex gap-1 flex-wrap mt-1">
                        {pathHistory.map((path) => (
                          <button
                            key={path}
                            className="btn btn-sm btn-outline-secondary"
                            onClick={() => handleSelectFromHistory(path)}
                            title={path}
                          >
                            <i className="bi bi-folder me-1" />
                            {getLastPathPart(path)}
                          </button>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Info hint */}
                <div className="alert alert-info small">
                  <i className="bi bi-info-circle me-1" />
                  {t('terminalInfoHint', language) ||
                    'Opens a web terminal on the remote machine. Claude Code is pre-configured with proxy authentication.'}
                </div>
              </>
            )}

            {/* Error Display */}
            {createRemoteSession.isError && workspaceType === 'remote' && (
              <div className="alert alert-danger small">
                {t('error', language)}:{' '}
                {(createRemoteSession.error as Error)?.message || t('error', language)}
              </div>
            )}
          </>
        )}
      </Modal>

      {/* Directory Browser Modal */}
      {selectedMachineId && (
        <DirectoryBrowserModal
          isOpen={showDirectoryBrowser}
          onClose={() => setShowDirectoryBrowser(false)}
          machineId={selectedMachineId}
          initialPath={
            projectPath || (selectedMachine ? getDefaultPath(selectedMachine.os_type) : '')
          }
          osType={selectedMachine?.os_type ?? undefined}
          onSelectPath={(path) => {
            setProjectPath(path);
            savePathToHistory(path);
          }}
        />
      )}
    </>
  );
};
