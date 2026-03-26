/**
 * UserManagement Component - User CRUD operations
 */

import React, { useState } from 'react';
import { useUsers, useCreateUser, useUpdateUser, useDeleteUser, useUpdateUserPassword } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Button,
  Modal,
  TextInput,
  Select,
  Loading,
  Error,
  EmptyState,
  Badge,
} from '@/components/common';
import type { AdminUser, CreateUserRequest, UpdateUserRequest } from '@/api';

export const UserManagement: React.FC = () => {
  const language = useLanguage();
  const { data: users, isLoading, isError, error, refetch } = useUsers();
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const deleteUser = useDeleteUser();
  const updateUserPassword = useUpdateUserPassword();

  const [showModal, setShowModal] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formData, setFormData] = useState<CreateUserRequest & { confirm_password?: string }>({
    username: '',
    email: '',
    password: '',
    confirm_password: '',
    role: 'user',
    linux_account: '',
  });

  const roleOptions = [
    { value: 'admin', label: 'Admin' },
    { value: 'user', label: 'User' },
    { value: 'viewer', label: 'Viewer' },
  ];

  const handleOpenCreate = () => {
    setEditingUser(null);
    setFormError(null);
    setFormData({
      username: '',
      email: '',
      password: '',
      confirm_password: '',
      role: 'user',
      linux_account: '',
    });
    setShowModal(true);
  };

  const handleOpenEdit = (user: AdminUser) => {
    setEditingUser(user);
    setFormError(null);
    setFormData({
      username: user.username,
      email: user.email,
      password: '',
      confirm_password: '',
      role: user.role,
      linux_account: user.linux_account || '',
    });
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingUser(null);
    setFormError(null);
    setFormData({
      username: '',
      email: '',
      password: '',
      confirm_password: '',
      role: 'user',
      linux_account: '',
    });
  };

  const handleSubmit = async () => {
    setFormError(null);

    // Client-side validation
    if (!formData.username || formData.username.trim() === '') {
      setFormError(t('usernameRequired', language) || 'Username is required');
      return;
    }

    if (!formData.email || formData.email.trim() === '') {
      setFormError(t('emailRequired', language) || 'Email is required');
      return;
    }

    if (!editingUser) {
      // Password required for new users
      if (!formData.password || formData.password.trim() === '') {
        setFormError(t('passwordRequired', language) || 'Password is required');
        return;
      }
      if (formData.password.length < 8) {
        setFormError(t('passwordTooShort', language) || 'Password must be at least 8 characters');
        return;
      }
    }

    if (formData.password && formData.password !== formData.confirm_password) {
      setFormError(t('passwordMismatch', language) || 'Passwords do not match');
      return;
    }

    try {
      if (editingUser) {
        // Update existing user
        const updateData: UpdateUserRequest = {
          username: formData.username,
          email: formData.email,
          role: formData.role,
          linux_account: formData.linux_account,
        };
        await updateUser.mutateAsync({ userId: editingUser.id, data: updateData });

        // Update password if provided
        if (formData.password && formData.password.trim() !== '') {
          await updateUserPassword.mutateAsync({
            userId: editingUser.id,
            password: formData.password,
          });
        }
      } else {
        // Create new user
        await createUser.mutateAsync(formData);
      }
      handleCloseModal();
    } catch (err: any) {
      console.error('Failed to save user:', err);
      // Display error message to user
      const errorMessage = err?.message || err?.error || t('failedToSaveUser', language) || 'Failed to save user';
      setFormError(errorMessage);
    }
  };

  const handleDelete = async (userId: number) => {
    if (window.confirm(t('confirmDeleteUser', language))) {
      try {
        await deleteUser.mutateAsync(userId);
      } catch (err) {
        console.error('Failed to delete user:', err);
      }
    }
  };

  const getRoleBadgeVariant = (role: string) => {
    switch (role) {
      case 'admin':
        return 'danger';
      case 'user':
        return 'primary';
      default:
        return 'secondary';
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="user-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h5>{t('userList', language)}</h5>
        <Button variant="primary" size="sm" onClick={handleOpenCreate}>
          <i className="bi bi-plus-lg me-1" />
          {t('addUser', language)}
        </Button>
      </div>

      {/* User Table */}
      {!users || users.length === 0 ? (
        <EmptyState icon="bi-people" title={t('noUsers', language)} />
      ) : (
        <div className="table-responsive">
          <table className="table table-hover">
            <thead>
              <tr>
                <th>{t('tableUsername', language)}</th>
                <th>{t('tableEmail', language)}</th>
                <th>{t('linuxAccount', language)}</th>
                <th>{t('tableRole', language)}</th>
                <th>{t('tableStatus', language)}</th>
                <th>{t('tableCreatedAt', language)}</th>
                <th>{t('tableActions', language)}</th>
              </tr>
            </thead>
            <tbody>
              {users.map((user) => (
                <tr key={user.id}>
                  <td>
                    <strong>{user.username}</strong>
                  </td>
                  <td>{user.email}</td>
                  <td>{user.linux_account || '-'}</td>
                  <td>
                    <Badge variant={getRoleBadgeVariant(user.role)}>{user.role}</Badge>
                  </td>
                  <td>
                    <Badge variant={user.is_active ? 'success' : 'secondary'}>
                      {user.is_active ? t('active', language) : t('inactive', language)}
                    </Badge>
                  </td>
                  <td>{new Date(user.created_at).toLocaleDateString()}</td>
                  <td>
                    <div className="btn-group btn-group-sm">
                      <Button
                        variant="outline-primary"
                        size="sm"
                        onClick={() => handleOpenEdit(user)}
                      >
                        <i className="bi bi-pencil" />
                      </Button>
                      <Button
                        variant="outline-danger"
                        size="sm"
                        onClick={() => handleDelete(user.id)}
                        disabled={deleteUser.isPending}
                      >
                        <i className="bi bi-trash" />
                      </Button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Create/Edit Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModal}
        title={editingUser ? t('editUser', language) : t('addUser', language)}
        size="lg"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleSubmit}
              loading={createUser.isPending || updateUser.isPending}
            >
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
        >
          {/* Hidden submit button to enable Enter key submission */}
          <button type="submit" style={{ display: 'none' }} />

          {/* Error Message */}
          {formError && (
            <div className="alert alert-danger mb-3" role="alert">
              <i className="bi bi-exclamation-triangle-fill me-2" />
              {formError}
            </div>
          )}

          <div className="row g-3">
          <div className="col-md-6">
            <label className="form-label">{t('tableUsername', language)}</label>
            <TextInput
              value={formData.username}
              onChange={(value: string) => setFormData({ ...formData, username: value })}
              placeholder={t('enterUsername', language)}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('tableEmail', language)}</label>
            <TextInput
              type="email"
              value={formData.email}
              onChange={(value: string) => setFormData({ ...formData, email: value })}
              placeholder={t('enterEmail', language)}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('linuxAccount', language)}</label>
            <TextInput
              value={formData.linux_account || ''}
              onChange={(value: string) => setFormData({ ...formData, linux_account: value })}
              placeholder={t('enterLinuxAccount', language)}
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('tableRole', language)}</label>
            <Select
              options={roleOptions}
              value={formData.role}
              onChange={(value) =>
                setFormData({ ...formData, role: value as CreateUserRequest['role'] })
              }
            />
          </div>
          {!editingUser && (
            <>
              <div className="col-md-6">
                <label className="form-label">{t('password', language)}</label>
                <TextInput
                  type="password"
                  value={formData.password}
                  onChange={(value: string) => setFormData({ ...formData, password: value })}
                  placeholder={t('enterPassword', language)}
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('confirmPassword', language)}</label>
                <TextInput
                  type="password"
                  value={formData.confirm_password || ''}
                  onChange={(value: string) => setFormData({ ...formData, confirm_password: value })}
                  placeholder={t('confirmPassword', language)}
                />
              </div>
            </>
          )}
          {editingUser && (
            <>
              <div className="col-12">
                <hr className="my-2" />
                <small className="text-muted d-block mb-2">{t('passwordHint', language)}</small>
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('newPassword', language)}</label>
                <TextInput
                  type="password"
                  value={formData.password}
                  onChange={(value: string) => setFormData({ ...formData, password: value })}
                  placeholder={t('enterPassword', language)}
                />
              </div>
              <div className="col-md-6">
                <label className="form-label">{t('confirmPassword', language)}</label>
                <TextInput
                  type="password"
                  value={formData.confirm_password || ''}
                  onChange={(value: string) => setFormData({ ...formData, confirm_password: value })}
                  placeholder={t('confirmPassword', language)}
                />
              </div>
            </>
          )}
        </div>
        </form>
      </Modal>
    </div>
  );
};