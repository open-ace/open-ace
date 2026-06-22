/**
 * UserManagement Component - User CRUD operations
 */

import React, { useState } from 'react';
import {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useUpdateUserPassword,
  usePageRefresh,
  useSecuritySettings,
} from '@/hooks';
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
  PageRefreshControl,
} from '@/components/common';
import { ToolAccountsEditor } from './ToolAccountsEditor';
import { MappingRulesEditor } from './MappingRulesEditor';
import { mappingRulesApi } from '@/api/mappingRules';
import { createMatcherConfig } from '@/utils';
import type { AdminUser, CreateUserRequest, UpdateUserRequest } from '@/api';

export const UserManagement: React.FC = () => {
  const language = useLanguage();
  const { data: users, isLoading, isError, error, refetch } = useUsers();
  const { data: securitySettings } = useSecuritySettings();
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const deleteUser = useDeleteUser();
  const updateUserPassword = useUpdateUserPassword();

  // Page refresh control - manual refresh for user management
  const pageRefresh = usePageRefresh({
    page: '/manage/users',
    refreshKey: createMatcherConfig([['users']], 'prefix'),
    interval: 0, // No auto refresh - manual only
    enabled: false,
  });

  const [showModal, setShowModal] = useState(false);
  const [editingUser, setEditingUser] = useState<AdminUser | null>(null);
  const [formError, setFormError] = useState<string | null>(null);
  const [formData, setFormData] = useState<
    CreateUserRequest & { confirm_password?: string; is_active: boolean }
  >({
    username: '',
    email: '',
    password: '',
    confirm_password: '',
    role: 'user',
    system_account: '',
    is_active: true,
  });

  const roleOptions = [
    { value: 'admin', label: 'Admin' },
    { value: 'user', label: 'User' },
    { value: 'viewer', label: 'Viewer' },
  ];

  const activeStatusOptions = [
    { value: 'true', label: t('active', language) },
    { value: 'false', label: t('inactive', language) },
  ];

  // Password policy validation
  const validatePasswordPolicy = (password: string): string | null => {
    if (!password) return t('passwordRequired', language) ?? 'Password is required';
    if (password.length < 8)
      return t('passwordTooShort', language) ?? 'Password must be at least 8 characters';

    const policy = securitySettings;
    if (policy) {
      const minLen = policy.password_min_length || 8;
      if (password.length < minLen) {
        return `${t('passwordMinLength', language)}: ${minLen}`;
      }
      if (policy.password_require_uppercase && !/[A-Z]/.test(password)) {
        return t('requireUppercase', language);
      }
      if (policy.password_require_lowercase && !/[a-z]/.test(password)) {
        return t('requireLowercase', language);
      }
      if (policy.password_require_number && !/[0-9]/.test(password)) {
        return t('requireNumber', language);
      }
      if (policy.password_require_special && !/[!@#$%^&*(),.?":{}|<>]/.test(password)) {
        return t('requireSpecial', language);
      }
    }
    return null;
  };

  // Password policy hint component
  const PasswordPolicyHint = () => {
    const policy = securitySettings;
    if (!policy) return null;

    const requirements: string[] = [];
    requirements.push(`${t('passwordMinLength', language)}: ${policy.password_min_length || 8}`);
    if (policy.password_require_uppercase) requirements.push(t('requireUppercase', language));
    if (policy.password_require_lowercase) requirements.push(t('requireLowercase', language));
    if (policy.password_require_number) requirements.push(t('requireNumber', language));
    if (policy.password_require_special) requirements.push(t('requireSpecial', language));

    return (
      <div className="password-policy-hint text-muted small mt-1">
        <div>{t('passwordRequirements', language)}:</div>
        <ul className="mb-0 ps-3" style={{ fontSize: '0.85em' }}>
          {requirements.map((req, idx) => (
            <li key={idx}>{req}</li>
          ))}
        </ul>
      </div>
    );
  };

  const handleOpenCreate = () => {
    setEditingUser(null);
    setFormError(null);
    setFormData({
      username: '',
      email: '',
      password: '',
      confirm_password: '',
      role: 'user',
      system_account: '',
      is_active: true,
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
      system_account: user.system_account ?? '',
      is_active: user.is_active,
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
      system_account: '',
      is_active: true,
    });
  };

  const handleSubmit = async () => {
    setFormError(null);

    // Client-side validation
    if (!formData.username || formData.username.trim() === '') {
      setFormError(t('usernameRequired', language) ?? 'Username is required');
      return;
    }

    if (!formData.email || formData.email.trim() === '') {
      setFormError(t('emailRequired', language) ?? 'Email is required');
      return;
    }

    if (!editingUser) {
      // Password required for new users - validate with policy
      const passwordError = validatePasswordPolicy(formData.password);
      if (passwordError) {
        setFormError(passwordError);
        return;
      }
    } else if (formData.password && formData.password.trim() !== '') {
      // Optional password update for existing user - validate with policy
      const passwordError = validatePasswordPolicy(formData.password);
      if (passwordError) {
        setFormError(passwordError);
        return;
      }
    }

    if (formData.password && formData.password !== formData.confirm_password) {
      setFormError(t('passwordMismatch', language) ?? 'Passwords do not match');
      return;
    }

    try {
      if (editingUser) {
        // Update existing user
        const updateData: UpdateUserRequest = {
          username: formData.username,
          email: formData.email,
          role: formData.role,
          system_account: formData.system_account,
          is_active: formData.is_active,
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
    } catch (err: unknown) {
      console.error('Failed to save user:', err);
      // Display error message to user
      const errorMessage =
        (err as Error)?.message ??
        (err as Record<string, string>)?.error ??
        t('failedToSaveUser', language) ??
        'Failed to save user';
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
        <h2>{t('userList', language)}</h2>
        <div className="d-flex gap-2">
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showAutoRefreshToggle={false}
            showIntervalSelector={false}
            showLastRefreshTime={true}
          />
          <Button variant="primary" size="sm" onClick={handleOpenCreate}>
            <i className="bi bi-plus-lg me-1" />
            {t('addUser', language)}
          </Button>
        </div>
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
                <th>{t('toolAccounts', language)}</th>
                <th>{language === 'zh' ? '映射规则' : 'Mapping Rules'}</th>
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
                  <td>{user.system_account ?? '-'}</td>
                  <td>
                    <ToolAccountsEditor userId={user.id} onChange={() => refetch()} />
                  </td>
                  <td>
                    <MappingRulesEditor
                      userId={user.id}
                      username={user.username}
                      onChange={() => refetch()}
                    />
                  </td>
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
                value={formData.system_account ?? ''}
                onChange={(value: string) => setFormData({ ...formData, system_account: value })}
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
            {editingUser && (
              <div className="col-md-6">
                <label className="form-label">{t('activationStatus', language)}</label>
                <Select
                  options={activeStatusOptions}
                  value={formData.is_active ? 'true' : 'false'}
                  onChange={(value) => setFormData({ ...formData, is_active: value === 'true' })}
                />
              </div>
            )}
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
                  <PasswordPolicyHint />
                </div>
                <div className="col-md-6">
                  <label className="form-label">{t('confirmPassword', language)}</label>
                  <TextInput
                    type="password"
                    value={formData.confirm_password ?? ''}
                    onChange={(value: string) =>
                      setFormData({ ...formData, confirm_password: value })
                    }
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
                  <PasswordPolicyHint />
                </div>
                <div className="col-md-6">
                  <label className="form-label">{t('confirmPassword', language)}</label>
                  <TextInput
                    type="password"
                    value={formData.confirm_password ?? ''}
                    onChange={(value: string) =>
                      setFormData({ ...formData, confirm_password: value })
                    }
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
