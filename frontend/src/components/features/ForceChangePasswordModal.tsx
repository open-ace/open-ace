/**
 * ForceChangePasswordModal - Modal for forced password change
 *
 * Displays when user's must_change_password flag is true,
 * requiring them to change password before continuing.
 */

import React, { useState } from 'react';
import { Modal, Button, TextInput } from '@/components/common';
import { useAuth, useLanguage, useMustChangePassword, useSecuritySettings } from '@/hooks';
import { t } from '@/i18n';

export const ForceChangePasswordModal: React.FC = () => {
  const language = useLanguage();
  const mustChangePassword = useMustChangePassword();
  const { changePassword, isChangingPassword, changePasswordError } = useAuth();
  const { data: securitySettings } = useSecuritySettings();

  const [currentPassword, setCurrentPassword] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [error, setError] = useState<string | null>(null);

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

  const handleSubmit = async () => {
    setError(null);

    if (!currentPassword) {
      setError(t('currentPasswordRequired', language) ?? 'Current password is required');
      return;
    }

    if (!newPassword) {
      setError(t('newPasswordRequired', language) ?? 'New password is required');
      return;
    }

    if (newPassword.length < 8) {
      setError(t('passwordTooShort', language) ?? 'Password must be at least 8 characters');
      return;
    }

    if (newPassword !== confirmPassword) {
      setError(t('passwordMismatch', language) ?? 'Passwords do not match');
      return;
    }

    try {
      await changePassword(currentPassword, newPassword);
      // After successful change, the auth hook will refetch and update must_change_password
    } catch (err) {
      const errorMessage =
        (err as Error)?.message ??
        (err as Record<string, string>)?.error ??
        t('failedToChangePassword', language) ??
        'Failed to change password';
      setError(errorMessage);
    }
  };

  // Don't render if not required
  if (!mustChangePassword) {
    return null;
  }

  return (
    <Modal
      isOpen={true}
      onClose={() => {}}
      title={t('changePasswordRequired', language) ?? 'Change Password Required'}
      size="md"
      footer={
        <Button variant="primary" onClick={handleSubmit} loading={isChangingPassword}>
          {t('changePassword', language) ?? 'Change Password'}
        </Button>
      }
    >
      <div className="alert alert-warning mb-3">
        <i className="bi bi-exclamation-triangle-fill me-2" />
        {t('mustChangePasswordHint', language) ??
          'Your password was reset by an administrator. You must change it before continuing.'}
      </div>

      {(error ?? changePasswordError) && (
        <div className="alert alert-danger mb-3" role="alert">
          <i className="bi bi-exclamation-triangle-fill me-2" />
          {error ?? (changePasswordError as Error)?.message}
        </div>
      )}

      <div className="mb-3">
        <label className="form-label">{t('currentPassword', language) ?? 'Current Password'}</label>
        <TextInput
          type="password"
          value={currentPassword}
          onChange={(value: string) => setCurrentPassword(value)}
          placeholder={t('enterCurrentPassword', language) ?? 'Enter current password'}
        />
      </div>

      <div className="mb-3">
        <label className="form-label">{t('password', language) ?? 'New Password'}</label>
        <TextInput
          type="password"
          value={newPassword}
          onChange={(value: string) => setNewPassword(value)}
          placeholder={t('enterNewPassword', language) ?? 'Enter new password'}
        />
        <PasswordPolicyHint />
      </div>

      <div className="mb-3">
        <label className="form-label">{t('confirmPassword', language) ?? 'Confirm Password'}</label>
        <TextInput
          type="password"
          value={confirmPassword}
          onChange={(value: string) => setConfirmPassword(value)}
          placeholder={t('confirmPassword', language) ?? 'Confirm password'}
        />
      </div>
    </Modal>
  );
};
