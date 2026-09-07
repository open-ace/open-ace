/**
 * ProjectUserManagement Component - Manage project visible users
 *
 * Issue #3275: Project user management functionality
 * Allows project creator or admin to add/remove visible users for shared projects.
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Modal,
  Button,
  Loading,
  Error,
  EmptyState,
  Badge,
  Avatar,
  Divider,
} from '@/components/common';
import { useToast } from '@/components/common';
import {
  getProjectUsers,
  addProjectUser,
  removeProjectUser,
  type UserProject,
} from '@/api/projects';
import { UserSelectModal } from './UserSelectModal';

interface ProjectUserManagementProps {
  isOpen: boolean;
  onClose: () => void;
  projectId: number;
  projectName: string;
  onSuccess: () => void;
}

/**
 * Modal for managing project visible users.
 * Supports adding and removing users, with session conflict warnings.
 */
export const ProjectUserManagement: React.FC<ProjectUserManagementProps> = ({
  isOpen,
  onClose,
  projectId,
  projectName,
  onSuccess,
}) => {
  const language = useLanguage();
  const toast = useToast();

  const [users, setUsers] = useState<UserProject[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showAddModal, setShowAddModal] = useState(false);
  const [removeTarget, setRemoveTarget] = useState<UserProject | null>(null);
  const [isRemoving, setIsRemoving] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');

  // Fetch users when modal opens
  useEffect(() => {
    if (!isOpen) return;

    const fetchUsers = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const response = await getProjectUsers(projectId);
        setUsers(response.users || []);
      } catch (err) {
        const error = err as { message?: string };
        setError(error.message ?? 'Failed to load users');
      } finally {
        setIsLoading(false);
      }
    };

    fetchUsers();
  }, [isOpen, projectId]);

  // Filter users by search query
  const filteredUsers = useMemo(() => {
    if (!searchQuery) return users;
    const query = searchQuery.toLowerCase();
    return users.filter(
      (user) =>
        user.username?.toLowerCase().includes(query) ??
        `user ${user.user_id}`.toLowerCase().includes(query)
    );
  }, [users, searchQuery]);

  // Handle add user
  const handleAddUser = async (userId: number) => {
    try {
      await addProjectUser(projectId, userId);
      toast.success(t('userAddedSuccessfully', language));
      // Refresh user list
      const response = await getProjectUsers(projectId);
      setUsers(response.users || []);
      onSuccess();
    } catch (err) {
      const error = err as { message?: string };
      toast.error(error.message ?? t('failedToAddUser', language));
    }
  };

  // Handle remove user
  const handleRemoveUser = async () => {
    if (!removeTarget) return;

    setIsRemoving(true);
    try {
      const response = await removeProjectUser(projectId, removeTarget.user_id);

      if (response.warning) {
        toast.warning(response.warning);
      } else {
        toast.success(t('userRemovedSuccessfully', language));
      }

      // Refresh user list
      const userResponse = await getProjectUsers(projectId);
      setUsers(userResponse.users || []);
      setRemoveTarget(null);
      onSuccess();
    } catch (err) {
      const error = err as { message?: string };
      toast.error(error.message ?? t('failedToRemoveUser', language));
    } finally {
      setIsRemoving(false);
    }
  };

  // Format duration
  const formatDuration = (seconds: number): string => {
    if (seconds < 60) return `${seconds}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${minutes}m`;
  };

  // Format datetime
  const formatDateTime = (dateStr: string | null): string => {
    if (!dateStr) return t('never', language);
    const date = new Date(dateStr);
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 60) return t('minutesAgo', language, { count: diffMins });
    if (diffHours < 24) return t('hoursAgo', language, { count: diffHours });
    if (diffDays < 7) return t('daysAgo', language, { count: diffDays });

    return date.toLocaleDateString();
  };

  if (!isOpen) return null;

  return (
    <>
      <Modal
        isOpen={isOpen}
        onClose={onClose}
        title={`${t('manageProjectUsers', language)}: ${projectName}`}
        size="lg"
      >
        {isLoading && <Loading size="sm" text={t('loading', language)} />}

        {error && <Error message={error} onRetry={() => setUsers([])} />}

        {!isLoading && !error && (
          <>
            {/* Header with add button */}
            <div className="d-flex justify-content-between align-items-center mb-3">
              <div>
                <h6 className="mb-0 d-flex align-items-center">
                  <i className="bi bi-people me-2" />
                  {t('currentVisibleUsers', language)}
                  <Badge variant="secondary" pill className="ms-2">
                    {users.length}
                  </Badge>
                </h6>
              </div>
              <Button variant="primary" size="sm" onClick={() => setShowAddModal(true)}>
                <i className="bi bi-plus-lg me-1" />
                {t('addUser', language)}
              </Button>
            </div>

            {/* Search box */}
            <div className="mb-3">
              <input
                type="text"
                className="form-control"
                placeholder={t('searchUsers', language)}
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
              />
            </div>

            <Divider />

            {/* User list */}
            {filteredUsers.length === 0 ? (
              <EmptyState
                icon="bi-person-x"
                title={t('noUsersFound', language)}
                description={
                  searchQuery ? t('noUsersMatchSearch', language) : t('noVisibleUsers', language)
                }
              />
            ) : (
              <div className="table-responsive" style={{ maxHeight: '400px', overflowY: 'auto' }}>
                <table className="table table-sm table-hover align-middle">
                  <thead>
                    <tr>
                      <th>{t('tableUser', language)}</th>
                      <th className="text-center">{t('sessions', language)}</th>
                      <th className="text-center">{t('tokens', language)}</th>
                      <th className="text-center">{t('workTime', language)}</th>
                      <th>{t('lastAccess', language)}</th>
                      <th>{t('tableActions', language)}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filteredUsers.map((user) => (
                      <tr key={user.id}>
                        <td>
                          <div className="d-flex align-items-center">
                            <Avatar name={user.username ?? 'User'} size="sm" className="me-2" />
                            <strong>{user.username ?? `User ${user.user_id}`}</strong>
                          </div>
                        </td>
                        <td className="text-center">{user.total_sessions}</td>
                        <td className="text-center">
                          {Number(user.total_tokens).toLocaleString()}
                        </td>
                        <td className="text-center">
                          {formatDuration(user.total_duration_seconds)}
                        </td>
                        <td>
                          <small className="text-muted">
                            {formatDateTime(user.last_access_at)}
                          </small>
                        </td>
                        <td>
                          <Button
                            variant="outline-danger"
                            size="sm"
                            onClick={() => {
                              setRemoveTarget(user);
                            }}
                          >
                            <i className="bi bi-trash me-1" />
                            {t('remove', language)}
                          </Button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {/* Footer */}
            <div className="modal-footer border-0 px-0 pb-0 mt-3">
              <Button variant="secondary" onClick={onClose}>
                {t('close', language)}
              </Button>
            </div>
          </>
        )}
      </Modal>

      {/* Add User Modal */}
      <UserSelectModal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        onSelect={handleAddUser}
        projectId={projectId}
        existingUserIds={users.map((u) => u.user_id)}
      />

      {/* Remove Confirmation Modal */}
      <Modal
        isOpen={removeTarget !== null}
        onClose={() => setRemoveTarget(null)}
        title={t('confirmRemoveUser', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setRemoveTarget(null)}>
              {t('cancel', language)}
            </Button>
            <Button variant="danger" onClick={handleRemoveUser} loading={isRemoving}>
              {t('remove', language)}
            </Button>
          </>
        }
      >
        {removeTarget && (
          <div className="alert alert-warning d-flex align-items-center">
            <i className="bi bi-exclamation-triangle me-2" />
            <div>
              {t('confirmRemoveUserMessage', language, {
                username: removeTarget.username ?? `User ${removeTarget.user_id}`,
              })}
            </div>
          </div>
        )}
      </Modal>
    </>
  );
};

export default ProjectUserManagement;
