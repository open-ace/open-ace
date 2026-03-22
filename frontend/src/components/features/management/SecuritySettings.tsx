/**
 * SecuritySettings Component - Security settings management
 */

import React, { useState } from 'react';
import { useSecuritySettings, useUpdateSecuritySettings } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, TextInput, Loading, Error } from '@/components/common';
import { useToast } from '@/components/common';
import type { SecuritySettings as SecuritySettingsType } from '@/api';

export const SecuritySettings: React.FC = () => {
  const language = useLanguage();
  const { data: settings, isLoading, isError, error, refetch } = useSecuritySettings();
  const updateSettings = useUpdateSecuritySettings();
  const toast = useToast();

  const [formData, setFormData] = useState<Partial<SecuritySettingsType>>({});

  const handleInputChange = (key: keyof SecuritySettingsType, value: unknown) => {
    setFormData((prev) => ({ ...prev, [key]: value }));
  };

  const handleSave = async () => {
    try {
      await updateSettings.mutateAsync(formData);
      toast.success(t('settingsSaved', language));
      setFormData({});
    } catch (err) {
      console.error('Failed to save settings:', err);
      toast.error(t('error', language));
    }
  };

  const handleReset = () => {
    setFormData({});
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  // Merge current settings with form changes
  const currentSettings: SecuritySettingsType = {
    session_timeout: 30,
    max_login_attempts: 5,
    password_min_length: 8,
    password_require_uppercase: true,
    password_require_lowercase: true,
    password_require_number: true,
    password_require_special: false,
    two_factor_enabled: false,
    ip_whitelist: [],
    ...settings,
    ...formData,
  };

  return (
    <div className="security-settings">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h5>{t('securitySettings', language)}</h5>
        <div className="d-flex gap-2">
          <Button variant="secondary" size="sm" onClick={handleReset}>
            {t('reset', language)}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={handleSave}
            loading={updateSettings.isPending}
          >
            {t('save', language)}
          </Button>
        </div>
      </div>

      {/* Session Settings */}
      <Card title={t('sessionSettings', language)} className="mb-4">
        <div className="row g-3">
          <div className="col-md-6">
            <label className="form-label">
              {t('sessionTimeout', language)} ({t('minutes', language)})
            </label>
            <TextInput
              type="number"
              value={currentSettings.session_timeout.toString()}
              onChange={(value: string) =>
                handleInputChange('session_timeout', parseInt(value) || 30)
              }
            />
            <small className="text-muted">{t('sessionTimeoutHelp', language)}</small>
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('maxLoginAttempts', language)}</label>
            <TextInput
              type="number"
              value={currentSettings.max_login_attempts.toString()}
              onChange={(value: string) =>
                handleInputChange('max_login_attempts', parseInt(value) || 5)
              }
            />
            <small className="text-muted">{t('maxLoginAttemptsHelp', language)}</small>
          </div>
        </div>
      </Card>

      {/* Password Policy */}
      <Card title={t('passwordPolicy', language)} className="mb-4">
        <div className="row g-3">
          <div className="col-md-6">
            <label className="form-label">{t('passwordMinLength', language)}</label>
            <TextInput
              type="number"
              value={currentSettings.password_min_length.toString()}
              onChange={(value: string) =>
                handleInputChange('password_min_length', parseInt(value) || 8)
              }
            />
          </div>
          <div className="col-md-6">
            <label className="form-label">{t('passwordRequirements', language)}</label>
            <div className="d-flex flex-column gap-2 mt-2">
              <div className="form-check">
                <input
                  className="form-check-input"
                  type="checkbox"
                  checked={currentSettings.password_require_uppercase}
                  onChange={(e) =>
                    handleInputChange('password_require_uppercase', e.target.checked)
                  }
                  id="requireUppercase"
                />
                <label className="form-check-label" htmlFor="requireUppercase">
                  {t('requireUppercase', language)}
                </label>
              </div>
              <div className="form-check">
                <input
                  className="form-check-input"
                  type="checkbox"
                  checked={currentSettings.password_require_lowercase}
                  onChange={(e) =>
                    handleInputChange('password_require_lowercase', e.target.checked)
                  }
                  id="requireLowercase"
                />
                <label className="form-check-label" htmlFor="requireLowercase">
                  {t('requireLowercase', language)}
                </label>
              </div>
              <div className="form-check">
                <input
                  className="form-check-input"
                  type="checkbox"
                  checked={currentSettings.password_require_number}
                  onChange={(e) => handleInputChange('password_require_number', e.target.checked)}
                  id="requireNumber"
                />
                <label className="form-check-label" htmlFor="requireNumber">
                  {t('requireNumber', language)}
                </label>
              </div>
              <div className="form-check">
                <input
                  className="form-check-input"
                  type="checkbox"
                  checked={currentSettings.password_require_special}
                  onChange={(e) => handleInputChange('password_require_special', e.target.checked)}
                  id="requireSpecial"
                />
                <label className="form-check-label" htmlFor="requireSpecial">
                  {t('requireSpecial', language)}
                </label>
              </div>
            </div>
          </div>
        </div>
      </Card>

      {/* Two-Factor Authentication */}
      <Card title={t('twoFactorAuth', language)} className="mb-4">
        <div className="form-check form-switch">
          <input
            className="form-check-input"
            type="checkbox"
            checked={currentSettings.two_factor_enabled}
            onChange={(e) => handleInputChange('two_factor_enabled', e.target.checked)}
            id="twoFactorEnabled"
          />
          <label className="form-check-label" htmlFor="twoFactorEnabled">
            {t('enableTwoFactor', language)}
          </label>
        </div>
        <small className="text-muted">{t('twoFactorHelp', language)}</small>
      </Card>

      {/* IP Whitelist */}
      <Card title={t('ipWhitelist', language)} className="mb-4">
        <div className="mb-3">
          <label className="form-label">{t('allowedIpAddresses', language)}</label>
          <textarea
            className="form-control"
            rows={4}
            value={(currentSettings.ip_whitelist || []).join('\n')}
            onChange={(e) =>
              handleInputChange(
                'ip_whitelist',
                e.target.value.split('\n').filter((ip) => ip.trim())
              )
            }
            placeholder={'192.168.1.1\n10.0.0.0/24'}
          />
          <small className="text-muted">{t('ipWhitelistHelp', language)}</small>
        </div>
      </Card>
    </div>
  );
};
