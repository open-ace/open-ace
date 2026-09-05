/**
 * UserSelectModal Component - User selection modal for adding users to project
 *
 * Issue #3275: Project user management
 * Displays a list of tenant users, supports search and selection.
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, Loading, Error, EmptyState, Avatar } from '@/components/common';
import { useToast } from '@/components/common';
import { getUsers, type User } from '@/api/users';

interface UserSelectModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSelect: (userId: number) => void;
  projectId: number;
  existingUserIds: number[];
}

/**
 * Modal for selecting a user to add to a project.
 * Filters out users already in the project.
 */
export const UserSelectModal: React.FC<UserSelectModalProps> = ({
  isOpen,
  onClose,
  onSelect,
  projectId: _projectId,
  existingUserIds,
}) => {
  const language = useLanguage();
  const toast = useToast();

  const [users, setUsers] = useState<User[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [selectedUserId, setSelectedUserId] = useState<number | null>(null);

  // Fetch users when modal opens
  useEffect(() => {
    if (!isOpen) return;

    const fetchUsers = async () => {
      setIsLoading(true);
      setError(null);
      try {
        const response = await getUsers();
        setUsers(response.users || []);
      } catch (err) {
        const error = err as { message?: string };
        setError(error.message ?? 'Failed to load users');
      } finally {
        setIsLoading(false);
      }
    };

    fetchUsers();
  }, [isOpen]);

  // Filter users: exclude existing users and apply search
  const availableUsers = useMemo(() => {
    let filtered = users.filter((user) => !existingUserIds.includes(user.id));

    if (searchQuery) {
      const query = searchQuery.toLowerCase();
      filtered = filtered.filter(
        (user) =>
          user.username?.toLowerCase().includes(query) ?? user.email?.toLowerCase().includes(query)
      );
    }

    return filtered;
  }, [users, existingUserIds, searchQuery]);

  // Handle confirm
  const handleConfirm = () => {
    if (selectedUserId === null) {
      toast.error(t('pleaseSelectAUser', language));
      return;
    }

    onSelect(selectedUserId);
    setSelectedUserId(null);
    setSearchQuery('');
    onClose();
  };

  // Handle close
  const handleClose = () => {
    setSelectedUserId(null);
    setSearchQuery('');
    onClose();
  };

  if (!isOpen) return null;

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={t('selectUser', language)}
      size="md"
      footer={
        <>
          <Button variant="secondary" onClick={handleClose}>
            {t('cancel', language)}
          </Button>
          <Button variant="primary" onClick={handleConfirm} disabled={selectedUserId === null}>
            {t('confirm', language)}
          </Button>
        </>
      }
    >
      {isLoading && <Loading size="sm" text={t('loading', language)} />}

      {error && <Error message={error} onRetry={() => setUsers([])} />}

      {!isLoading && !error && (
        <>
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

          {/* User list */}
          {availableUsers.length === 0 ? (
            <EmptyState
              icon="bi-person-x"
              title={t('noAvailableUsers', language)}
              description={
                searchQuery
                  ? t('noUsersMatchSearch', language)
                  : t('allUsersAlreadyAdded', language)
              }
            />
          ) : (
            <div className="list-group" style={{ maxHeight: '300px', overflowY: 'auto' }}>
              {availableUsers.map((user) => (
                <button
                  key={user.id}
                  type="button"
                  className={`list-group-item list-group-item-action d-flex align-items-center ${
                    selectedUserId === user.id ? 'active' : ''
                  }`}
                  onClick={() => setSelectedUserId(user.id)}
                >
                  <Avatar name={user.username ?? 'User'} size="sm" className="me-2" />
                  <div className="flex-grow-1">
                    <div className="fw-medium">{user.username}</div>
                    <small className="text-muted">{user.email}</small>
                  </div>
                  {selectedUserId === user.id && <i className="bi bi-check-circle-fill" />}
                </button>
              ))}
            </div>
          )}

          {/* Selected count */}
          {selectedUserId !== null && (
            <div className="mt-2 text-muted small">
              {t('selectedUser', language, {
                username: availableUsers.find((u) => u.id === selectedUserId)?.username ?? 'User',
              })}
            </div>
          )}
        </>
      )}
    </Modal>
  );
};

export default UserSelectModal;
