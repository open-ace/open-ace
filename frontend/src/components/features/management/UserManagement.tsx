/**
 * UserManagement Component - User CRUD operations
 */

import React, { useState } from 'react';
import {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useResetUserPassword,
  usePageRefresh,
  useSecuritySettings,
  useTenants,
} from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { copyToClipboard } from '@/utils';
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
import { useConfirm, useToast } from '@/components/common';
import { ToolAccountsEditor } from './ToolAccountsEditor';
import { MappingRulesEditor } from './MappingRulesEditor';
import { createMatcherConfig } from '@/utils';
import type { AdminUser, CreateUserRequest, UpdateUserRequest } from '@/api';

export const UserManagement: React.FC = () => {
  const language = useLanguage();
  const [selectedTenantId, setSelectedTenantId] = useState<number | undefined>(undefined);
  const { data: users, isLoading, isError, error, refetch } = useUsers(selectedTenantId);
  const { data: securitySettings } = useSecuritySettings();
  const { data: tenantsData } = useTenants();
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const deleteUser = useDeleteUser();
  const resetUserPassword = useResetUserPassword();

  // Temporary password modal state
  const [showTempPasswordModal, setShowTempPasswordModal] = useState(false);
  const [tempPassword, setTempPassword] = useState<string>('');

  // Page refresh control - manual refresh for user management
  const pageRefresh = usePageRefresh({
    page: '/manage/users',
    refreshKey: createMatcherConfig([['admin', 'users']], 'prefix'),
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
    tenant_id: undefined,
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

  const tenantFormOptions = tenantsData?.tenants
    ? tenantsData.tenants.map((t) => ({ value: String(t.id), label: t.name }))
    : [];

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
      tenant_id: selectedTenantId,
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
      tenant_id: user.tenant_id,
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
      tenant_id: undefined,
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

      if (formData.password !== formData.confirm_password) {
        setFormError(t('passwordMismatch', language) ?? 'Passwords do not match');
        return;
      }
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
          tenant_id: formData.tenant_id,
        };
        await updateUser.mutateAsync({ userId: editingUser.id, data: updateData });
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

  const confirm = useConfirm();
  const toast = useToast();
  const handleDelete = async (userId: number) => {
    if (await confirm({ message: t('confirmDeleteUser', language), variant: 'danger' })) {
      try {
        await deleteUser.mutateAsync(userId);
      } catch (err) {
        console.error('Failed to delete user:', err);
      }
    }
  };

  const handleResetPassword = async (userId: number) => {
    try {
      const result = await resetUserPassword.mutateAsync(userId);
      if (result.temporary_password) {
        setTempPassword(result.temporary_password);
        setShowTempPasswordModal(true);
      }
    } catch (err) {
      console.error('Failed to reset password:', err);
    }
  };

  const handleCloseTempPasswordModal = () => {
    setShowTempPasswordModal(false);
    setTempPassword('');
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

  // Tenant filter options
  const tenantFilterOptions = [
    { value: '', label: t('allTenants', language) ?? 'All Tenants' },
    ...(tenantsData?.tenants?.map((t) => ({ value: String(t.id), label: t.name })) ?? []),
  ];

  return (
    <div className="user-management">
      {/* Header - 顶部操作栏优化 */}
      <div className="d-flex align-items-center gap-3 mb-4">
        {/* 左侧：标题 */}
        <h2 className="mb-0 fs-5 fw-semibold">{t('userList', language)}</h2>

        {/* 中间：筛选控件 */}
        <div className="d-flex align-items-center">
          <Select
            options={tenantFilterOptions}
            value={selectedTenantId ? String(selectedTenantId) : ''}
            onChange={(value) => setSelectedTenantId(value ? Number(value) : undefined)}
            placeholder={t('selectTenant', language) ?? 'Select Tenant'}
            size="sm"
            style={{ minWidth: '140px', maxWidth: '200px' }}
          />
        </div>

        {/* 右侧：操作按钮 */}
        <div className="d-flex align-items-center gap-2 ms-auto">
          {/* 刷新按钮 */}
          <PageRefreshControl
            refresh={pageRefresh}
            compact={true}
            showAutoRefreshToggle={false}
            showIntervalSelector={false}
            showLastRefreshTime={true}
          />

          {/* 添加用户按钮：柔和圆角主色按钮 */}
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
                <th>{t('tenant', language) ?? 'Tenant'}</th>
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
                  <td>{user.tenant_name ?? '-'}</td>
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
                        variant="outline-warning"
                        size="sm"
                        onClick={() => handleResetPassword(user.id)}
                        disabled={resetUserPassword.isPending}
                        title={t('resetPassword', language) ?? 'Reset Password'}
                      >
                        <i className="bi bi-key" />
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
              <label className="form-label">
                {t('tableUsername', language)}
                <span className="text-danger ms-1">*</span>
              </label>
              <TextInput
                value={formData.username}
                onChange={(value: string) => setFormData({ ...formData, username: value })}
                placeholder={t('enterUsername', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">
                {t('tableEmail', language)}
                <span className="text-danger ms-1">*</span>
              </label>
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
            <div className="col-md-6">
              <label className="form-label">{t('tenant', language) ?? 'Tenant'}</label>
              <Select
                options={tenantFormOptions}
                value={formData.tenant_id ? String(formData.tenant_id) : ''}
                onChange={(value) =>
                  setFormData({ ...formData, tenant_id: value ? Number(value) : undefined })
                }
                placeholder={t('selectTenant', language) ?? 'Select Tenant'}
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
                  <label className="form-label">
                    {t('password', language)}
                    <span className="text-danger ms-1">*</span>
                  </label>
                  <TextInput
                    type="password"
                    value={formData.password}
                    onChange={(value: string) => setFormData({ ...formData, password: value })}
                    placeholder={t('enterPassword', language)}
                  />
                  <PasswordPolicyHint />
                </div>
                <div className="col-md-6">
                  <label className="form-label">
                    {t('confirmPassword', language)}
                    <span className="text-danger ms-1">*</span>
                  </label>
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

      {/* Temporary Password Modal */}
      <Modal
        isOpen={showTempPasswordModal}
        onClose={handleCloseTempPasswordModal}
        title={t('temporaryPassword', language) ?? 'Temporary Password'}
        size="md"
        footer={
          <Button variant="primary" onClick={handleCloseTempPasswordModal}>
            {t('close', language)}
          </Button>
        }
      >
        <div className="alert alert-warning mb-3">
          <i className="bi bi-exclamation-triangle-fill me-2" />
          {t('tempPasswordWarning', language) ??
            'Please share this password securely with the user. They will be required to change it on first login.'}
        </div>
        <div className="mb-3">
          <label className="form-label">
            {t('temporaryPasswordLabel', language) ?? 'Temporary Password'}
          </label>
          <div className="input-group">
            <input
              type="text"
              className="form-control"
              value={tempPassword}
              readOnly
              style={{ fontWeight: 'bold', fontSize: '1.2em' }}
            />
            <Button
              variant="outline-secondary"
              onClick={async () => {
                const success = await copyToClipboard(tempPassword);
                if (!success) {
                  toast.error(t('copyFailed', language) || 'Copy failed');
                }
              }}
              title={t('copyToClipboard', language) ?? 'Copy to clipboard'}
            >
              <i className="bi bi-clipboard" />
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
