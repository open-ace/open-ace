/**
 * RemoteMachineManagement Component - Remote machine management page
 *
 * Features:
 * - Machine list with status badges
 * - Generate registration token dialog (system admin only)
 * - Machine details dialog with assigned users
 * - Assign/revoke user access (system admin or machine admin)
 * - Deregister machine (system admin only)
 */

import React, { useState } from 'react';
import {
  useMachines,
  useMachineUsers,
  useGenerateToken,
  useDeregisterMachine,
  useAssignUser,
  useRevokeUser,
  useUsers,
} from '@/hooks';
import { useLanguage } from '@/store';
import type { Language } from '@/i18n';
import { t } from '@/i18n';
import {
  Button,
  Modal,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
} from '@/components/common';
import type { RemoteMachine } from '@/api';

export const RemoteMachineManagement: React.FC = () => {
  const language = useLanguage();
  const { data: machinesData, isLoading, isError, error, refetch } = useMachines();
  const generateToken = useGenerateToken();
  const deregisterMachine = useDeregisterMachine();
  const assignUser = useAssignUser();
  const revokeUser = useRevokeUser();
  const { data: allUsers } = useUsers();

  const machines = machinesData?.machines ?? [];

  // Derive isSystemAdmin: if no machine has current_user_permission, user is system admin
  // (backend only sets this field when querying with user_id for non-admin users)
  const isSystemAdmin = machines.length === 0
    || machines.every((m) => !m.current_user_permission);

  // Dialog states
  const [showTokenDialog, setShowTokenDialog] = useState(false);
  const [generatedToken, setGeneratedToken] = useState<string>('');
  const [copied, setCopied] = useState(false);
  const [copiedInstall, setCopiedInstall] = useState(false);

  const [selectedMachine, setSelectedMachine] = useState<RemoteMachine | null>(null);
  const [showDetailsDialog, setShowDetailsDialog] = useState(false);

  const [showAssignDialog, setShowAssignDialog] = useState(false);
  const [assignMachineId, setAssignMachineId] = useState<string>('');
  const [selectedUserId, setSelectedUserId] = useState<string>('');
  const [selectedPermission, setSelectedPermission] = useState<string>('user');

  const [showDeregisterDialog, setShowDeregisterDialog] = useState(false);
  const [deregisterTarget, setDeregisterTarget] = useState<RemoteMachine | null>(null);

  // Handlers
  const handleGenerateToken = async () => {
    try {
      const result = await generateToken.mutateAsync(undefined);
      setGeneratedToken(result.registration_token);
      setShowTokenDialog(true);
    } catch (err) {
      console.error('Failed to generate token:', err);
    }
  };

  const handleCopyToken = () => {
    navigator.clipboard.writeText(generatedToken);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const handleCopyInstallCommand = () => {
    const server = window.location.origin;
    const cmd = `curl -fsSL ${server}/api/remote/agent/install.sh | bash -s -- --server ${server} --token ${generatedToken}`;
    navigator.clipboard.writeText(cmd);
    setCopiedInstall(true);
    setTimeout(() => setCopiedInstall(false), 2000);
  };

  const handleViewDetails = (machine: RemoteMachine) => {
    setSelectedMachine(machine);
    setShowDetailsDialog(true);
  };

  const handleOpenAssign = (machineId: string) => {
    setAssignMachineId(machineId);
    setSelectedUserId('');
    setSelectedPermission('user');
    setShowAssignDialog(true);
  };

  const handleAssignUser = async () => {
    if (!selectedUserId) return;
    try {
      await assignUser.mutateAsync({
        machineId: assignMachineId,
        userId: Number(selectedUserId),
        permission: selectedPermission,
      });
      setShowAssignDialog(false);
    } catch (err) {
      console.error('Failed to assign user:', err);
    }
  };

  const handleRevokeUser = async (machineId: string, userId: number) => {
    try {
      await revokeUser.mutateAsync({ machineId, userId });
    } catch (err) {
      console.error('Failed to revoke user:', err);
    }
  };

  const handleOpenDeregister = (machine: RemoteMachine) => {
    setDeregisterTarget(machine);
    setShowDeregisterDialog(true);
  };

  const handleDeregister = async () => {
    if (!deregisterTarget) return;
    try {
      await deregisterMachine.mutateAsync(deregisterTarget.machine_id);
      setShowDeregisterDialog(false);
      setDeregisterTarget(null);
    } catch (err) {
      console.error('Failed to deregister:', err);
    }
  };

  // Stats
  const totalMachines = machines.length;
  const onlineCount = machines.filter((m) => m.status === 'online').length;
  const offlineCount = totalMachines - onlineCount;

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  // Permission options for assign dialog
  const permissionOptions = isSystemAdmin
    ? [{ value: 'user', label: 'User' }, { value: 'admin', label: 'Admin' }]
    : [{ value: 'user', label: 'User' }];

  return (
    <div className="remote-machine-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('remoteMachines', language)}</h2>
        {isSystemAdmin && (
          <Button variant="primary" size="sm" onClick={handleGenerateToken} loading={generateToken.isPending}>
            <i className="bi bi-plus-lg me-1" />
            {t('generateToken', language)}
          </Button>
        )}
      </div>

      {/* Stats Cards */}
      <div className="row g-3 mb-4">
        <div className="col-md-4">
          <div className="card">
            <div className="card-body text-center">
              <div className="text-muted small">{t('totalMachines', language)}</div>
              <div className="h3 mb-0">{totalMachines}</div>
            </div>
          </div>
        </div>
        <div className="col-md-4">
          <div className="card">
            <div className="card-body text-center">
              <div className="text-muted small">{t('onlineMachines', language)}</div>
              <div className="h3 mb-0 text-success">{onlineCount}</div>
            </div>
          </div>
        </div>
        <div className="col-md-4">
          <div className="card">
            <div className="card-body text-center">
              <div className="text-muted small">{t('offlineMachines', language)}</div>
              <div className="h3 mb-0 text-secondary">{offlineCount}</div>
            </div>
          </div>
        </div>
      </div>

      {/* Machine Table */}
      {machines.length === 0 ? (
        <EmptyState
          icon="bi-pc-display"
          title={t('noMachines', language)}
          description={t('noMachinesDescription', language)}
        />
      ) : (
        <div className="table-responsive">
          <table className="table table-hover">
            <thead>
              <tr>
                <th>{t('machineName', language)}</th>
                <th>{t('hostname', language)}</th>
                <th>OS</th>
                <th>{t('keyStatus', language)}</th>
                <th>{t('agentVersion', language)}</th>
                <th>{t('lastHeartbeat', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {machines.map((machine) => (
                <tr key={machine.machine_id}>
                  <td>
                    <strong>{machine.machine_name}</strong>
                    <div className="text-muted small">{machine.machine_id.substring(0, 8)}...</div>
                  </td>
                  <td>{machine.hostname || '-'}</td>
                  <td>
                    {machine.os_type || '-'}
                    {machine.os_version ? ` ${machine.os_version}` : ''}
                  </td>
                  <td>
                    <Badge variant={machine.status === 'online' ? 'success' : 'secondary'}>
                      {machine.status === 'online' ? t('online', language) : t('offline', language)}
                    </Badge>
                  </td>
                  <td>{machine.agent_version || '-'}</td>
                  <td>
                    {machine.last_heartbeat
                      ? new Date(machine.last_heartbeat).toLocaleString()
                      : '-'}
                  </td>
                  <td>
                    <div className="btn-group btn-group-sm">
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleViewDetails(machine)}
                      >
                        <i className="bi bi-eye" />
                      </Button>
                      {isSystemAdmin && (
                        <Button
                          variant="outline-danger"
                          size="sm"
                          onClick={() => handleOpenDeregister(machine)}
                        >
                          <i className="bi bi-x-lg" />
                        </Button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Registration Token Dialog */}
      <Modal
        isOpen={showTokenDialog}
        onClose={() => setShowTokenDialog(false)}
        title={t('registrationToken', language)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowTokenDialog(false)}>
              {t('close', language)}
            </Button>
            <Button variant="primary" onClick={handleCopyToken}>
              <i className="bi bi-clipboard me-1" />
              {copied ? t('copied', language) : t('copyToken', language)}
            </Button>
          </>
        }
      >
        <div className="mb-3">
          <p className="text-muted">{t('tokenGeneratedDesc', language)}</p>
          <div className="input-group">
            <input
              type="text"
              className="form-control font-monospace"
              value={generatedToken}
              readOnly
            />
            <Button variant="outline-secondary" onClick={handleCopyToken}>
              {copied ? <i className="bi bi-check" /> : <i className="bi bi-clipboard" />}
            </Button>
          </div>
        </div>

        {/* Install Command */}
        <div className="mt-3">
          <p className="text-muted mb-1">
            <strong>{t('installCommand', language)}</strong>
          </p>
          <div className="input-group">
            <input
              type="text"
              className="form-control font-monospace"
              value={`curl -fsSL ${window.location.origin}/api/remote/agent/install.sh | bash -s -- --server ${window.location.origin} --token ${generatedToken}`}
              readOnly
            />
            <Button variant="outline-secondary" onClick={handleCopyInstallCommand}>
              {copiedInstall ? <i className="bi bi-check" /> : <i className="bi bi-clipboard" />}
            </Button>
          </div>
          <small className="text-muted">{t('installCommandDesc', language)}</small>
        </div>
      </Modal>

      {/* Machine Details Dialog */}
      {selectedMachine && (
        <MachineDetailsDialog
          machine={selectedMachine}
          isOpen={showDetailsDialog}
          onClose={() => {
            setShowDetailsDialog(false);
            setSelectedMachine(null);
          }}
          onAssignUser={handleOpenAssign}
          onRevokeUser={handleRevokeUser}
          assignPending={assignUser.isPending}
          revokePending={revokeUser.isPending}
          canManageUsers={isSystemAdmin || selectedMachine.current_user_permission === 'admin'}
          language={language}
        />
      )}

      {/* Assign User Dialog */}
      <Modal
        isOpen={showAssignDialog}
        onClose={() => setShowAssignDialog(false)}
        title={t('assignUser', language)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowAssignDialog(false)}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleAssignUser}
              disabled={!selectedUserId}
              loading={assignUser.isPending}
            >
              {t('assignUser', language)}
            </Button>
          </>
        }
      >
        <div className="mb-3">
          <label className="form-label">{t('selectUser', language)}</label>
          <Select
            options={[
              { value: '', label: `-- ${t('selectUser', language)} --` },
              ...(allUsers ?? []).map((u) => ({ value: String(u.id), label: u.username })),
            ]}
            value={selectedUserId}
            onChange={(v) => setSelectedUserId(v)}
          />
        </div>
        <div>
          <label className="form-label">{t('permission', language)}</label>
          <Select
            options={permissionOptions}
            value={selectedPermission}
            onChange={(v) => setSelectedPermission(v)}
          />
        </div>
      </Modal>

      {/* Deregister Confirm Dialog */}
      <Modal
        isOpen={showDeregisterDialog}
        onClose={() => setShowDeregisterDialog(false)}
        title={t('deregisterMachine', language)}
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowDeregisterDialog(false)}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="danger"
              onClick={handleDeregister}
              loading={deregisterMachine.isPending}
            >
              {t('deregister', language)}
            </Button>
          </>
        }
      >
        <p>{t('deregisterConfirm', language)}</p>
        {deregisterTarget && (
          <p>
            <strong>{deregisterTarget.machine_name}</strong> ({deregisterTarget.hostname || deregisterTarget.machine_id.substring(0, 8)})
          </p>
        )}
      </Modal>
    </div>
  );
};

// ==================== Machine Details Sub-component ====================

interface MachineDetailsDialogProps {
  machine: RemoteMachine;
  isOpen: boolean;
  onClose: () => void;
  onAssignUser: (machineId: string) => void;
  onRevokeUser: (machineId: string, userId: number) => void;
  assignPending: boolean;
  revokePending: boolean;
  canManageUsers: boolean;
  language: Language;
}

const MachineDetailsDialog: React.FC<MachineDetailsDialogProps> = ({
  machine,
  isOpen,
  onClose,
  onAssignUser,
  onRevokeUser,
  revokePending,
  canManageUsers,
  language,
}) => {
  const { data: usersData, refetch } = useMachineUsers(machine.machine_id);
  const assignedUsers = usersData?.users ?? [];

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('machineDetails', language)}
      size="lg"
      footer={
        <>
          <Button variant="secondary" onClick={onClose}>
            {t('close', language)}
          </Button>
          {canManageUsers && (
            <Button variant="primary" onClick={() => onAssignUser(machine.machine_id)}>
              <i className="bi bi-person-plus me-1" />
              {t('assignUser', language)}
            </Button>
          )}
        </>
      }
    >
      {/* Machine Info */}
      <div className="row g-3 mb-4">
        <div className="col-md-6">
          <label className="text-muted small">{t('machineName', language)}</label>
          <div>{machine.machine_name}</div>
        </div>
        <div className="col-md-6">
          <label className="text-muted small">{t('hostname', language)}</label>
          <div>{machine.hostname || '-'}</div>
        </div>
        <div className="col-md-6">
          <label className="text-muted small">{t('operatingSystem', language)}</label>
          <div>
            {machine.os_type || '-'}
            {machine.os_version ? ` ${machine.os_version}` : ''}
          </div>
        </div>
        <div className="col-md-6">
          <label className="text-muted small">{t('agentVersion', language)}</label>
          <div>{machine.agent_version || '-'}</div>
        </div>
        <div className="col-md-6">
          <label className="text-muted small">{t('ipAddress', language)}</label>
          <div>{machine.ip_address || '-'}</div>
        </div>
        <div className="col-md-6">
          <label className="text-muted small">{t('keyStatus', language)}</label>
          <div>
            <Badge variant={machine.status === 'online' ? 'success' : 'secondary'}>
              {machine.status === 'online' ? t('online', language) : t('offline', language)}
            </Badge>
            {machine.connected && (
              <Badge variant="info" className="ms-2">
                {t('connected', language)}
              </Badge>
            )}
          </div>
        </div>
        <div className="col-md-6">
          <label className="text-muted small">{t('lastHeartbeat', language)}</label>
          <div>
            {machine.last_heartbeat
              ? new Date(machine.last_heartbeat).toLocaleString()
              : '-'}
          </div>
        </div>
      </div>

      {/* Capabilities */}
      {machine.capabilities && Object.keys(machine.capabilities).length > 0 && (
        <div className="mb-4">
          <label className="text-muted small">{t('capabilities', language)}</label>
          <pre className="bg-light p-2 rounded small mb-0" style={{ maxHeight: '200px', overflow: 'auto' }}>
            {JSON.stringify(machine.capabilities, null, 2)}
          </pre>
        </div>
      )}

      {/* Assigned Users - only visible to admins */}
      {canManageUsers && (
        <div>
          <h6 className="mb-2">{t('assignUsers', language)}</h6>
          {assignedUsers.length === 0 ? (
            <p className="text-muted">{t('noAssignedUsers', language)}</p>
          ) : (
            <table className="table table-sm table-hover">
              <thead>
                <tr>
                  <th>{t('tableUsername', language)}</th>
                  <th>{t('permission', language)}</th>
                  <th>{t('lastAccess', language)}</th>
                  <th>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {assignedUsers.map((u) => (
                  <tr key={u.user_id}>
                    <td>{u.username}</td>
                    <td>
                      <Badge variant={u.permission === 'admin' ? 'danger' : 'primary'}>
                        {u.permission}
                      </Badge>
                    </td>
                    <td>{u.granted_at ? new Date(u.granted_at).toLocaleDateString() : '-'}</td>
                    <td>
                      <Button
                        variant="outline-danger"
                        size="sm"
                        onClick={() => {
                          onRevokeUser(machine.machine_id, u.user_id);
                          setTimeout(() => refetch(), 500);
                        }}
                        loading={revokePending}
                      >
                        <i className="bi bi-person-x" />
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </Modal>
  );
};
