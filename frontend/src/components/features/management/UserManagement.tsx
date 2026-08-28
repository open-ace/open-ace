/**
 * UserManagement Component - User CRUD operations
 */

import React, { useState, useRef, useMemo } from 'react';
import {
  useUsers,
  useCreateUser,
  useUpdateUser,
  useDeleteUser,
  useRestoreUser,
  useResetUserPassword,
  useSyncFeishuOrg,
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
import { AutoMappingPanel } from './AutoMappingPanel';
import { createMatcherConfig } from '@/utils';
import type {
  AdminUser,
  CreateUserRequest,
  UpdateUserRequest,
  SoftDeletedUserConflict,
  RestoreUserRequest,
} from '@/api';

export const UserManagement: React.FC = () => {
  const language = useLanguage();
  const [selectedTenantId, setSelectedTenantId] = useState<number | undefined>(undefined);
  const { data: users, isLoading, isError, error, refetch } = useUsers(selectedTenantId);
  const { data: securitySettings } = useSecuritySettings();
  const { data: tenantsData } = useTenants();
  const createUser = useCreateUser();
  const updateUser = useUpdateUser();
  const deleteUser = useDeleteUser();
  const restoreUser = useRestoreUser();
  const resetUserPassword = useResetUserPassword();
  const syncFeishuOrg = useSyncFeishuOrg();

  // Soft-deleted user conflict state (Issue #2755)
  const [showRestoreModal, setShowRestoreModal] = useState(false);
  const [softDeletedConflict, setSoftDeletedConflict] = useState<SoftDeletedUserConflict | null>(
    null
  );
  const [pendingCreateData, setPendingCreateData] = useState<CreateUserRequest | null>(null);
  const [restorePassword, setRestorePassword] = useState('');
  const [restorePasswordConfirm, setRestorePasswordConfirm] = useState('');

  // Reset password modal state (three-step)
  const [showResetPasswordModal, setShowResetPasswordModal] = useState(false);
  const [resetPasswordStep, setResetPasswordStep] = useState<
    'confirm' | 'setPassword' | 'complete'
  >('confirm');
  const [resetPasswordUser, setResetPasswordUser] = useState<AdminUser | null>(null);
  const [editingPassword, setEditingPassword] = useState('');
  const [resetPasswordError, setResetPasswordError] = useState<string | null>(null);
  const [resetPasswordResult, setResetPasswordResult] = useState('');
  const [copiedPassword, setCopiedPassword] = useState(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

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
    { value: 'platform_admin', label: t('rolePlatformAdmin', language) },
    { value: 'tenant_admin', label: t('roleTenantAdmin', language) },
    { value: 'manager', label: t('roleManager', language) },
    { value: 'user', label: t('roleUser', language) },
    { value: 'readonly', label: t('roleReadonly', language) },
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

    // Enforce maximum length to match backend validation (128 chars)
    if (password.length > 128) {
      return t('passwordTooLong', language) ?? 'Password must be less than 128 characters';
    }

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
      if (policy.password_require_special && !/[^\w\s]/.test(password)) {
        return t('requireSpecial', language);
      }
    } else {
      // Fallback when security settings not loaded - use default min length of 8
      if (password.length < 8) {
        return t('passwordTooShort', language) ?? 'Password must be at least 8 characters';
      }
    }
    return null;
  };

  // Generate a secure random password using Web Crypto API
  const generateSecurePassword = (): string => {
    const policy = securitySettings;
    const minLen = Math.max(policy?.password_min_length ?? 8, 12);
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789!@#$%^&*';

    // Use crypto.getRandomValues for cryptographically secure randomness
    const randomArray = new Uint32Array(minLen);
    crypto.getRandomValues(randomArray);

    let password = '';
    for (let i = 0; i < minLen; i++) {
      password += chars[randomArray[i] % chars.length];
    }

    // Ensure the password meets all policy requirements
    if (policy) {
      const checks: { flag: boolean | undefined; regex: RegExp; fallback: string }[] = [
        { flag: policy.password_require_uppercase, regex: /[A-Z]/, fallback: 'A' },
        { flag: policy.password_require_lowercase, regex: /[a-z]/, fallback: 'a' },
        { flag: policy.password_require_number, regex: /[0-9]/, fallback: '1' },
        { flag: policy.password_require_special, regex: /[^\w\s]/, fallback: '!' },
      ];

      checks.forEach((check, idx) => {
        if (check.flag && !check.regex.test(password)) {
          // Replace character at position idx with a guaranteed compliant one
          const randomIndex = new Uint32Array(1);
          crypto.getRandomValues(randomIndex);
          const replacementChars = check.fallback;
          password = password.substring(0, idx) + replacementChars + password.substring(idx + 1);
        }
      });
    }

    return password;
  };

  // Cache password validation result to avoid recomputing on every render

  const passwordValidationError = useMemo(
    () => validatePasswordPolicy(editingPassword),
    [editingPassword, securitySettings, language]
  );

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
      role: user.role as CreateUserRequest['role'],
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

      // Check for soft-deleted user conflict (Issue #2755)
      type ErrorResponse = { response?: { data?: Record<string, unknown> } };
      const errorObj = err as ErrorResponse;
      const errorData = errorObj?.response?.data;

      if (errorData?.error === 'USER_SOFT_DELETED' && errorData.soft_deleted_user) {
        // Soft-deleted user conflict - show restore modal
        const deletedUser = errorData.soft_deleted_user as Record<string, unknown>;
        const conflictInfo: SoftDeletedUserConflict = {
          user_id: deletedUser.user_id as number,
          username: deletedUser.username as string,
          email: deletedUser.email as string,
          deleted_at: deletedUser.deleted_at as string,
          tenant_id: deletedUser.tenant_id as number | undefined,
          conflicts: deletedUser.conflicts as ('username' | 'email')[],
        };

        setSoftDeletedConflict(conflictInfo);
        setPendingCreateData(formData);
        setShowModal(false);
        setShowRestoreModal(true);
        return;
      }

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

  const handleResetPassword = (user: AdminUser) => {
    // Open the three-step reset password modal instead of directly calling API
    setResetPasswordUser(user);
    setResetPasswordStep('confirm');
    setEditingPassword(generateSecurePassword());
    setResetPasswordError(null);
    setResetPasswordResult('');
    setCopiedPassword(false);
    setShowResetPasswordModal(true);
  };

  const handleConfirmResetPassword = () => {
    // Move from Step 1 (confirm) to Step 2 (set password)
    setResetPasswordError(null);
    setResetPasswordStep('setPassword');
  };

  const handleBackToConfirm = () => {
    setResetPasswordStep('confirm');
  };

  const handleRegeneratePassword = () => {
    setEditingPassword(generateSecurePassword());
    setResetPasswordError(null);
  };

  const handleConfirmSetPassword = async () => {
    if (!resetPasswordUser) return;

    // Validate password before submitting
    const error = validatePasswordPolicy(editingPassword);
    if (error) {
      setResetPasswordError(error);
      return;
    }

    try {
      const result = await resetUserPassword.mutateAsync({
        userId: resetPasswordUser.id,
        password: editingPassword,
      });
      if (result.temporary_password) {
        setResetPasswordResult(result.temporary_password);
        setResetPasswordStep('complete');
      }
    } catch (err) {
      console.error('Failed to reset password:', err);
      const errorMessage =
        (err as Error)?.message ??
        (err as Record<string, string>)?.error ??
        t('failedToSaveUser', language) ??
        'Failed to reset password';
      setResetPasswordError(errorMessage);
    }
  };

  const handleCloseResetPasswordModal = () => {
    if (copyTimeoutRef.current) {
      clearTimeout(copyTimeoutRef.current);
      copyTimeoutRef.current = null;
    }
    setShowResetPasswordModal(false);
    setResetPasswordUser(null);
    setResetPasswordStep('confirm');
    setEditingPassword('');
    setResetPasswordError(null);
    setResetPasswordResult('');
    setCopiedPassword(false);
  };

  const handleCopyPassword = async () => {
    const success = await copyToClipboard(resetPasswordResult);
    if (success) {
      setCopiedPassword(true);
      if (copyTimeoutRef.current) {
        clearTimeout(copyTimeoutRef.current);
      }
      copyTimeoutRef.current = setTimeout(() => {
        setCopiedPassword(false);
        copyTimeoutRef.current = null;
      }, 2000);
    } else {
      toast.error(t('copyFailed', language) || 'Copy failed');
    }
  };

  const handleSyncFeishu = async () => {
    const buttonLabel = language === 'zh' ? '同步飞书' : 'Sync Feishu';
    try {
      const response = await syncFeishuOrg.mutateAsync(selectedTenantId);
      const summary = response.result;
      const message =
        language === 'zh'
          ? `部门 ${summary.departments_seen}，用户 ${summary.users_seen}，新增团队 ${summary.teams_created}，新增成员关系 ${summary.memberships_added}`
          : `Departments ${summary.departments_seen}, users ${summary.users_seen}, teams created ${summary.teams_created}, memberships added ${summary.memberships_added}`;
      toast.success(buttonLabel, message);
      await refetch();
    } catch (err) {
      console.error('Failed to sync Feishu org:', err);
      toast.error(
        buttonLabel,
        (err as Error)?.message || (language === 'zh' ? '飞书同步失败' : 'Feishu sync failed')
      );
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

          <Button
            variant="outline-primary"
            size="sm"
            onClick={handleSyncFeishu}
            disabled={syncFeishuOrg.isPending}
          >
            <i className="bi bi-cloud-arrow-down me-1" aria-hidden="true" />
            {language === 'zh' ? '同步飞书' : 'Sync Feishu'}
          </Button>

          {/* 添加用户按钮：柔和圆角主色按钮 */}
          <Button variant="primary" size="sm" onClick={handleOpenCreate}>
            <i className="bi bi-plus-lg me-1" aria-hidden="true" />
            {t('addUser', language)}
          </Button>
        </div>
      </div>

      {/* Auto Mapping Panel - Issue #2374 */}
      <AutoMappingPanel users={users ?? undefined} onChange={() => refetch()} />

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
                        title={t('edit', language) ?? 'Edit'}
                        ariaLabel={t('edit', language) ?? 'Edit'}
                      >
                        <i className="bi bi-pencil" aria-hidden="true" />
                      </Button>
                      <Button
                        variant="outline-warning"
                        size="sm"
                        onClick={() => handleResetPassword(user)}
                        disabled={resetUserPassword.isPending}
                        title={t('resetPassword', language) ?? 'Reset Password'}
                        ariaLabel={t('resetPassword', language) ?? 'Reset Password'}
                      >
                        <i className="bi bi-key" aria-hidden="true" />
                      </Button>
                      <Button
                        variant="outline-danger"
                        size="sm"
                        onClick={() => handleDelete(user.id)}
                        disabled={deleteUser.isPending}
                        title={t('delete', language) ?? 'Delete'}
                        ariaLabel={t('delete', language) ?? 'Delete'}
                      >
                        <i className="bi bi-trash" aria-hidden="true" />
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
              <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
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

      {/* Reset Password Modal (Three-step) */}
      <Modal
        isOpen={showResetPasswordModal}
        onClose={handleCloseResetPasswordModal}
        title={t('resetUserPasswordTitle', language) ?? 'Reset User Password'}
        size="md"
        footer={
          resetPasswordStep === 'confirm' ? (
            <>
              <Button variant="secondary" onClick={handleCloseResetPasswordModal}>
                {t('cancel', language)}
              </Button>
              <Button variant="primary" onClick={handleConfirmResetPassword}>
                {t('continueToSetPassword', language) ?? 'Confirm, Continue to Set Password'}
              </Button>
            </>
          ) : resetPasswordStep === 'setPassword' ? (
            <>
              <Button variant="secondary" onClick={handleBackToConfirm}>
                {t('back', language) ?? 'Back'}
              </Button>
              <Button
                variant="primary"
                onClick={handleConfirmSetPassword}
                loading={resetUserPassword.isPending}
                disabled={!!passwordValidationError}
              >
                {t('confirmResetPassword', language) ?? 'Confirm Reset Password'}
              </Button>
            </>
          ) : (
            <Button variant="primary" onClick={handleCloseResetPasswordModal}>
              {t('close', language)}
            </Button>
          )
        }
      >
        {/* Step indicator */}
        <div className="d-flex align-items-center justify-content-center mb-3 gap-2">
          <span className={resetPasswordStep === 'confirm' ? 'fw-bold' : 'text-muted'}>
            {resetPasswordStep === 'confirm' ? '\u25CF' : '\u25CB'}{' '}
            {t('confirmOperation', language) ?? 'Confirm Operation'}
          </span>
          <span className="text-muted">{'\u2192'}</span>
          <span className={resetPasswordStep === 'setPassword' ? 'fw-bold' : 'text-muted'}>
            {resetPasswordStep === 'setPassword' ? '\u25CF' : '\u25CB'}{' '}
            {t('setPassword', language) ?? 'Set Password'}
          </span>
          <span className="text-muted">{'\u2192'}</span>
          <span className={resetPasswordStep === 'complete' ? 'fw-bold' : 'text-muted'}>
            {resetPasswordStep === 'complete' ? '\u25CF' : '\u25CB'} {t('complete', language)}
          </span>
        </div>

        {/* Step 1: Confirm */}
        {resetPasswordStep === 'confirm' && resetPasswordUser && (
          <div>
            <div className="alert alert-warning mb-3">
              <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
              <strong>
                {t('confirmResetUserPassword', language) ??
                  'Are you sure you want to reset this user\u2019s password?'}
              </strong>
              <br />
              <span className="small">
                {t('afterResetPasswordWillExpire', language) ??
                  'The current password will be invalid immediately after reset, and the user must log in with the new password.'}
              </span>
            </div>
            <div className="mb-3">
              <div className="row mb-1">
                <div className="col-4 text-muted">{t('tableUsername', language)}</div>
                <div className="col-8">
                  <strong>{resetPasswordUser.username}</strong>
                </div>
              </div>
              <div className="row">
                <div className="col-4 text-muted">{t('tableEmail', language)}</div>
                <div className="col-8">{resetPasswordUser.email}</div>
              </div>
            </div>
          </div>
        )}

        {/* Step 2: Set Password */}
        {resetPasswordStep === 'setPassword' && (
          <div>
            {resetPasswordError && (
              <div className="alert alert-danger mb-3" role="alert">
                <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
                {resetPasswordError}
              </div>
            )}
            <div className="mb-3">
              <label className="form-label">{t('newPassword', language)}</label>
              <div className="input-group">
                <input
                  type="text"
                  className="form-control"
                  value={editingPassword}
                  onChange={(e) => {
                    setEditingPassword(e.target.value);
                    setResetPasswordError(null);
                  }}
                  style={{ fontFamily: 'monospace', fontSize: '1.1em' }}
                />
                <Button
                  variant="outline-secondary"
                  onClick={handleRegeneratePassword}
                  title={t('generateRandomPassword', language) ?? 'Generate Random Password'}
                  ariaLabel={t('generateRandomPassword', language) ?? 'Generate Random Password'}
                >
                  <i className="bi bi-arrow-clockwise" aria-hidden="true" />
                </Button>
              </div>
              {/* Real-time validation result */}
              {passwordValidationError ? (
                <div className="text-danger small mt-1">
                  {'\u2717'}{' '}
                  {t('passwordDoesNotMeetRequirements', language) ??
                    'Password does not meet requirements'}
                  : {passwordValidationError}
                </div>
              ) : (
                <div className="text-success small mt-1">
                  {'\u2713'}{' '}
                  {t('passwordMeetsAllRequirements', language) ?? 'Password meets all requirements'}
                </div>
              )}
              {/* Password requirements list */}
              <PasswordPolicyHint />
            </div>
          </div>
        )}

        {/* Step 3: Complete */}
        {resetPasswordStep === 'complete' && (
          <div>
            <div className="alert alert-success mb-3">
              <i className="bi bi-check-circle-fill me-2" aria-hidden="true" />
              <strong>
                {t('passwordResetSuccessfully', language) ?? 'Password reset successfully'}
              </strong>
              <br />
              <span className="small">
                {t('userMustChangePasswordOnNextLogin', language) ??
                  'The user must change the password on next login.'}
              </span>
            </div>
            <div className="mb-3">
              <label className="form-label">{t('newPassword', language)}</label>
              <div className="input-group">
                <input
                  type="text"
                  className="form-control"
                  value={resetPasswordResult}
                  readOnly
                  style={{ fontFamily: 'monospace', fontSize: '1.2em', fontWeight: 'bold' }}
                />
                <Button
                  variant="outline-secondary"
                  onClick={handleCopyPassword}
                  title={t('copy', language) ?? 'Copy'}
                  ariaLabel={t('copy', language) ?? 'Copy'}
                >
                  <i
                    className={copiedPassword ? 'bi bi-check-lg' : 'bi bi-clipboard'}
                    aria-hidden="true"
                  />
                  {copiedPassword
                    ? (t('copied', language) ?? 'Copied')
                    : (t('copy', language) ?? 'Copy')}
                </Button>
              </div>
            </div>
          </div>
        )}
      </Modal>

      {/* Restore User Modal (Issue #2755) */}
      <Modal
        isOpen={showRestoreModal}
        onClose={() => {
          setShowRestoreModal(false);
          setSoftDeletedConflict(null);
          setPendingCreateData(null);
          setRestorePassword('');
          setRestorePasswordConfirm('');
        }}
        title={language === 'zh' ? '恢复已删除用户' : 'Restore Deleted User'}
        size="lg"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setShowRestoreModal(false);
                setSoftDeletedConflict(null);
                setPendingCreateData(null);
                setRestorePassword('');
                setRestorePasswordConfirm('');
              }}
            >
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={async () => {
                if (!softDeletedConflict) return;

                // Validate password
                if (restorePassword !== restorePasswordConfirm) {
                  toast.error(language === 'zh' ? '密码不匹配' : 'Passwords do not match');
                  return;
                }

                const passwordError = validatePasswordPolicy(restorePassword);
                if (passwordError) {
                  toast.error(passwordError);
                  return;
                }

                try {
                  const restoreData: RestoreUserRequest = {
                    password: restorePassword,
                    username: pendingCreateData?.username,
                    email: pendingCreateData?.email,
                  };

                  await restoreUser.mutateAsync({
                    userId: softDeletedConflict.user_id,
                    data: restoreData,
                  });

                  toast.success(language === 'zh' ? '用户已恢复' : 'User restored successfully');
                  setShowRestoreModal(false);
                  setSoftDeletedConflict(null);
                  setPendingCreateData(null);
                  setRestorePassword('');
                  setRestorePasswordConfirm('');
                  await refetch();
                } catch (err) {
                  console.error('Failed to restore user:', err);
                  toast.error(
                    (err as Error)?.message ??
                      (language === 'zh' ? '恢复用户失败' : 'Failed to restore user')
                  );
                }
              }}
              loading={restoreUser.isPending}
              disabled={!restorePassword || restorePassword !== restorePasswordConfirm}
            >
              {language === 'zh' ? '确认恢复' : 'Confirm Restore'}
            </Button>
          </>
        }
      >
        {softDeletedConflict && (
          <div>
            {/* Warning about historical associations */}
            <div className="alert alert-warning mb-3">
              <i className="bi bi-exclamation-triangle-fill me-2" aria-hidden="true" />
              <strong>
                {language === 'zh'
                  ? '警告：恢复用户将重新激活历史关联'
                  : 'Warning: Restoring this user will reactivate historical associations'}
              </strong>
              <ul className="mb-0 mt-2 small">
                <li>
                  {language === 'zh'
                    ? '用户将重新获得之前的项目所有权和权限'
                    : 'The user will regain previous project ownerships and permissions'}
                </li>
                <li>
                  {language === 'zh'
                    ? '工作区访问权限将被恢复'
                    : 'Workspace access will be restored'}
                </li>
                <li>
                  {language === 'zh'
                    ? 'SSO 映射关联将被重新激活'
                    : 'SSO mapping associations will be reactivated'}
                </li>
                <li>
                  {language === 'zh'
                    ? '所有历史会话将被撤销，用户需要重新登录'
                    : 'All historical sessions will be revoked; the user must log in again'}
                </li>
              </ul>
            </div>

            {/* User info */}
            <div className="mb-3">
              <div className="row mb-1">
                <div className="col-4 text-muted">{t('tableUsername', language)}</div>
                <div className="col-8">
                  <strong>{softDeletedConflict.username}</strong>
                </div>
              </div>
              <div className="row mb-1">
                <div className="col-4 text-muted">{t('tableEmail', language)}</div>
                <div className="col-8">{softDeletedConflict.email}</div>
              </div>
              <div className="row">
                <div className="col-4 text-muted">
                  {language === 'zh' ? '删除时间' : 'Deleted at'}
                </div>
                <div className="col-8">
                  {new Date(softDeletedConflict.deleted_at).toLocaleString()}
                </div>
              </div>
            </div>

            {/* New password for restored user */}
            <div className="row g-3">
              <div className="col-md-6">
                <label className="form-label">
                  {language === 'zh' ? '新密码' : 'New Password'}
                  <span className="text-danger ms-1">*</span>
                </label>
                <TextInput
                  type="password"
                  value={restorePassword}
                  onChange={(value: string) => setRestorePassword(value)}
                  placeholder={language === 'zh' ? '输入新密码' : 'Enter new password'}
                />
                <PasswordPolicyHint />
              </div>
              <div className="col-md-6">
                <label className="form-label">
                  {language === 'zh' ? '确认密码' : 'Confirm Password'}
                  <span className="text-danger ms-1">*</span>
                </label>
                <TextInput
                  type="password"
                  value={restorePasswordConfirm}
                  onChange={(value: string) => setRestorePasswordConfirm(value)}
                  placeholder={language === 'zh' ? '确认新密码' : 'Confirm new password'}
                />
              </div>
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};
