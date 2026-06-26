/**
 * UserSettingsModal - User personal settings dialog
 */

import React, { useState, useCallback } from 'react';
import { Modal, Button, TextInput } from '@/components/common';
import { AvatarUploader } from './AvatarUploader';
import { useLanguage, useAppStore, useUser } from '@/store';
import {
  useAutoFullscreenOnEnterChat,
  useEnableTabNotifications,
  useShowFileChangesPanel,
} from '@/store';
import { authApi } from '@/api/auth';
import { t } from '@/i18n';
import { useToast } from './Toast';
import { useAuth, useSecuritySettings } from '@/hooks';

interface UserSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const UserSettingsModal: React.FC<UserSettingsModalProps> = ({ isOpen, onClose }) => {
  const language = useLanguage();
  const user = useUser();
  const toast = useToast();
  const { changePassword, isChangingPassword, changePasswordError } = useAuth();
  const { data: securitySettings } = useSecuritySettings();
  const autoFullscreenOnEnterChat = useAutoFullscreenOnEnterChat();
  const enableTabNotifications = useEnableTabNotifications();
  const showFileChangesPanel = useShowFileChangesPanel();
  const {
    toggleAutoFullscreenOnEnterChat,
    toggleTabNotifications,
    toggleFileChangesPanel,
    setUser,
  } = useAppStore();
  const [uploading, setUploading] = useState(false);

  // Password change state
  const [showPasswordSection, setShowPasswordSection] = useState(false);
  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | null>(null);

  // Password policy hint component
  const PasswordPolicyHint = () => {
    const policy = securitySettings;
    if (!policy) return null;

    const requirements: string[] = [];
    requirements.push(`${t('passwordMinLength', language)}: ${policy.password_min_length ?? 8}`);
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

  const handlePasswordChange = useCallback(async () => {
    setPasswordError(null);

    if (!currentPassword) {
      setPasswordError(t('currentPasswordRequired', language) ?? 'Current password is required');
      return;
    }

    if (!newPassword) {
      setPasswordError(t('newPasswordRequired', language) ?? 'New password is required');
      return;
    }

    if (newPassword.length < (securitySettings?.password_min_length ?? 8)) {
      setPasswordError(
        t('passwordTooShort', language) ?? `Password must be at least ${securitySettings?.password_min_length ?? 8} characters`
      );
      return;
    }

    if (newPassword !== confirmPassword) {
      setPasswordError(t('passwordMismatch', language) ?? 'Passwords do not match');
      return;
    }

    try {
      await changePassword(currentPassword, newPassword);
      toast.success(t('passwordChangeSuccess', language) ?? 'Password changed successfully');
      // Clear form and hide section
      setCurrentPassword('');
      setNewPassword('');
      setConfirmPassword('');
      setShowPasswordSection(false);
    } catch (err) {
      const errorMessage =
        (err as Error)?.message ??
        (err as Record<string, string>)?.error ??
        t('failedToChangePassword', language) ??
        'Failed to change password';
      setPasswordError(errorMessage);
    }
  }, [currentPassword, newPassword, confirmPassword, changePassword, securitySettings, toast, language]);

  const handleUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      try {
        const result = await authApi.uploadAvatar(file);
        if (result.success && result.avatar_url) {
          // Update user in store
          if (user) {
            setUser({ ...user, avatar_url: result.avatar_url });
          }
          toast.success(t('avatarUploadSuccess', language));
        } else {
          toast.error(t('avatarUploadFailed', language));
        }
      } catch {
        toast.error(t('avatarUploadFailed', language));
      } finally {
        setUploading(false);
      }
    },
    [user, setUser, toast, language]
  );

  const handleDelete = useCallback(async () => {
    setUploading(true);
    try {
      const result = await authApi.deleteAvatar();
      if (result.success) {
        // Update user in store
        if (user) {
          setUser({ ...user, avatar_url: undefined });
        }
        toast.success(t('avatarDeleteSuccess', language));
      } else {
        toast.error(t('avatarDeleteFailed', language));
      }
    } catch {
      toast.error(t('avatarDeleteFailed', language));
    } finally {
      setUploading(false);
    }
  }, [user, setUser, toast, language]);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('personalSettings', language)} size="md">
      <div className="user-settings">
        {/* Avatar Section */}
        <div className="mb-4">
          <h6 className="text-muted mb-3">
            <i className="bi bi-person-circle me-2" />
            {t('avatar', language)}
          </h6>

          <AvatarUploader
            currentAvatarUrl={user?.avatar_url}
            username={user?.username}
            onUpload={handleUpload}
            onDelete={handleDelete}
            uploading={uploading}
          />
        </div>

        {/* Divider */}
        <hr className="my-4" />

        {/* Workspace Settings Section */}
        <div className="mb-4">
          <h6 className="text-muted mb-3">
            <i className="bi bi-window-desktop me-2" />
            {t('workspaceSettings', language)}
          </h6>

          {/* Auto fullscreen on enter chat */}
          <div className="form-check form-switch mb-3 d-flex align-items-start">
            <input
              className="form-check-input mt-1"
              type="checkbox"
              id="autoFullscreenOnEnterChat"
              checked={autoFullscreenOnEnterChat}
              onChange={toggleAutoFullscreenOnEnterChat}
            />
            <div className="ms-2">
              <label className="form-check-label fw-medium" htmlFor="autoFullscreenOnEnterChat">
                {t('autoFullscreenOnEnterChat', language)}
              </label>
              <p className="text-muted small mb-0 mt-1">
                {t('autoFullscreenOnEnterChatDesc', language)}
              </p>
            </div>
          </div>

          {/* Tab notifications */}
          <div className="form-check form-switch mb-3 d-flex align-items-start">
            <input
              className="form-check-input mt-1"
              type="checkbox"
              id="enableTabNotifications"
              checked={enableTabNotifications}
              onChange={toggleTabNotifications}
            />
            <div className="ms-2">
              <label className="form-check-label fw-medium" htmlFor="enableTabNotifications">
                {t('tabNotifications', language)}
              </label>
              <p className="text-muted small mb-0 mt-1">{t('tabNotificationsDesc', language)}</p>
            </div>
          </div>

          {/* File changes panel (Issue #144) */}
          <div className="form-check form-switch mb-3 d-flex align-items-start">
            <input
              className="form-check-input mt-1"
              type="checkbox"
              id="showFileChangesPanel"
              checked={showFileChangesPanel}
              onChange={toggleFileChangesPanel}
            />
            <div className="ms-2">
              <label className="form-check-label fw-medium" htmlFor="showFileChangesPanel">
                {t('showFileChangesPanel', language)}
              </label>
              <p className="text-muted small mb-0 mt-1">
                {t('showFileChangesPanelDesc', language)}
              </p>
            </div>
          </div>
        </div>

        {/* Divider */}
        <hr className="my-4" />

        {/* Password Section */}
        <div className="mb-4">
          <h6 className="text-muted mb-3">
            <i className="bi bi-key me-2" />
            {t('password', language) ?? 'Password'}
          </h6>

          {!showPasswordSection ? (
            <Button
              variant="outline-secondary"
              size="sm"
              onClick={() => setShowPasswordSection(true)}
            >
              <i className="bi bi-pencil me-1" />
              {t('changePassword', language) ?? 'Change Password'}
            </Button>
          ) : (
            <div className="password-change-form">
              {(passwordError ?? changePasswordError) && (
                <div className="alert alert-danger mb-3" role="alert">
                  <i className="bi bi-exclamation-triangle-fill me-2" />
                  {passwordError ?? (changePasswordError as Error)?.message}
                </div>
              )}

              <div className="mb-3">
                <label className="form-label">
                  {t('currentPassword', language) ?? 'Current Password'}
                </label>
                <TextInput
                  type="password"
                  value={currentPassword}
                  onChange={(value: string) => setCurrentPassword(value)}
                  placeholder={t('enterCurrentPassword', language) ?? 'Enter current password'}
                />
              </div>

              <div className="mb-3">
                <label className="form-label">
                  {t('newPassword', language) ?? 'New Password'}
                </label>
                <TextInput
                  type="password"
                  value={newPassword}
                  onChange={(value: string) => setNewPassword(value)}
                  placeholder={t('enterNewPassword', language) ?? 'Enter new password'}
                />
                <PasswordPolicyHint />
              </div>

              <div className="mb-3">
                <label className="form-label">
                  {t('confirmPassword', language) ?? 'Confirm Password'}
                </label>
                <TextInput
                  type="password"
                  value={confirmPassword}
                  onChange={(value: string) => setConfirmPassword(value)}
                  placeholder={t('confirmPassword', language) ?? 'Confirm password'}
                />
              </div>

              <div className="d-flex gap-2">
                <Button
                  variant="outline-secondary"
                  size="sm"
                  onClick={() => {
                    setShowPasswordSection(false);
                    setCurrentPassword('');
                    setNewPassword('');
                    setConfirmPassword('');
                    setPasswordError(null);
                  }}
                >
                  {t('cancel', language) ?? 'Cancel'}
                </Button>
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handlePasswordChange}
                  loading={isChangingPassword}
                >
                  {t('changePassword', language) ?? 'Change Password'}
                </Button>
              </div>
            </div>
          )}
        </div>
      </div>
    </Modal>
  );
};
