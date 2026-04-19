/**
 * NewSessionModal Component - Modal for creating new sessions (local or remote)
 *
 * Features:
 * - Workspace type selection: Local / Remote
 * - Machine selector for remote workspaces
 * - Project path input with auto-fill from machine work_dir
 */

import React, { useState, useMemo } from 'react';
import { useNavigate, useLocation } from 'react-router-dom';
import { useAvailableMachines, useCreateRemoteSession } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, Badge, EmptyState, Loading } from '@/components/common';

interface NewSessionModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const NewSessionModal: React.FC<NewSessionModalProps> = ({ isOpen, onClose }) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const location = useLocation();

  const [workspaceType, setWorkspaceType] = useState<'local' | 'remote'>('local');
  const [selectedMachineId, setSelectedMachineId] = useState<string>('');
  const [projectPath, setProjectPath] = useState('');

  const { data: machinesData, isLoading: machinesLoading } = useAvailableMachines();
  const createRemoteSession = useCreateRemoteSession();

  const machines = machinesData?.machines ?? [];

  const selectedMachine = useMemo(
    () => machines.find((m) => m.machine_id === selectedMachineId),
    [machines, selectedMachineId]
  );

  const handleMachineSelect = (machineId: string) => {
    setSelectedMachineId(machineId);
    const machine = machines.find((m) => m.machine_id === machineId);
    if (machine) {
      setProjectPath(machine.work_dir || '/root/workspace');
    }
  };

  const handleCreateLocal = () => {
    onClose();
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
      const result = await createRemoteSession.mutateAsync({
        machine_id: selectedMachineId,
        project_path: projectPath,
      });

      onClose();

      const machineName = selectedMachine?.machine_name || selectedMachineId.slice(0, 8);
      const sessionId = result.session?.session_id;
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

      // Reset state
      setSelectedMachineId('');
      setProjectPath('');
      setWorkspaceType('local');
    } catch (err) {
      console.error('Failed to create remote session:', err);
    }
  };

  const handleCreate = () => {
    if (workspaceType === 'local') {
      handleCreateLocal();
    } else {
      handleCreateRemote();
    }
  };

  const canCreate = workspaceType === 'local' || (selectedMachineId && projectPath);

  return (
    <Modal
      isOpen={isOpen}
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
            loading={createRemoteSession.isPending}
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
        </div>
      </div>

      {/* Remote Options */}
      {workspaceType === 'remote' && (
        <>
          {/* Machine List */}
          <div className="mb-3">
            <label className="form-label">{t('selectMachine', language)}</label>
            {machinesLoading ? (
              <Loading size="sm" text={t('loading', language)} />
            ) : machines.length === 0 ? (
              <EmptyState
                icon="bi-pc-display"
                title={t('noAvailableMachines', language)}
                description={t('noAvailableMachinesDesc', language)}
              />
            ) : (
              <div className="list-group" style={{ maxHeight: '240px', overflow: 'auto' }}>
                {machines.map((machine) => (
                  <button
                    key={machine.machine_id}
                    className={`list-group-item list-group-item-action d-flex justify-content-between align-items-center ${
                      selectedMachineId === machine.machine_id ? 'active' : ''
                    }`}
                    onClick={() => handleMachineSelect(machine.machine_id)}
                  >
                    <div>
                      <strong>{machine.machine_name}</strong>
                      <div className="text-muted small">
                        {machine.hostname || machine.machine_id.slice(0, 8)}
                        {machine.os_type && ` | ${machine.os_type}`}
                      </div>
                    </div>
                    <Badge variant="success">
                      {t('online', language)}
                    </Badge>
                  </button>
                ))}
              </div>
            )}
          </div>

          {/* Project Path */}
          {selectedMachineId && (
            <div className="mb-3">
              <label className="form-label">{t('projectPath', language)}</label>
              <input
                type="text"
                className="form-control"
                value={projectPath}
                onChange={(e) => setProjectPath(e.target.value)}
                placeholder="/root/workspace"
              />
              <div className="form-text text-muted small">
                {t('projectPathHint', language)}
              </div>
            </div>
          )}

          {/* Error Display */}
          {createRemoteSession.isError && (
            <div className="alert alert-danger small">
              {t('error', language)}: {(createRemoteSession.error as Error)?.message || t('error', language)}
            </div>
          )}
        </>
      )}
    </Modal>
  );
};
