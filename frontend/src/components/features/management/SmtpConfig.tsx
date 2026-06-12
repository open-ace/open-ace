/**
 * SmtpConfig Component - SMTP Configuration Management
 *
 * Features:
 * - SMTP server configuration form
 * - Test connection functionality
 * - Email sending statistics
 * - Send test email
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  TextInput,
  Loading,
  Error,
  Badge,
  useToast,
} from '@/components/common';
import { smtpConfigApi, type SMTPConfig, type EmailStatistics } from '@/api/smtpConfig';

export const SmtpConfig: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();

  // State
  const [config, setConfig] = useState<SMTPConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [testing, setTesting] = useState(false);
  const [statistics, setStatistics] = useState<EmailStatistics | null>(null);

  // Form state
  const [formData, setFormData] = useState({
    smtp_host: '',
    smtp_port: '587',
    smtp_user: '',
    smtp_password: '',
    from_address: '',
    use_tls: true,
  });

  // Fetch config on mount
  useEffect(() => {
    fetchConfig();
    fetchStatistics();
  }, []);

  const fetchConfig = async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await smtpConfigApi.getConfig();
      setConfig(result);
      if (result) {
        setFormData({
          smtp_host: result.smtp_host,
          smtp_port: String(result.smtp_port),
          smtp_user: result.smtp_user || '',
          smtp_password: '', // Don't populate password
          from_address: result.from_address,
          use_tls: result.use_tls,
        });
      }
    } catch (err) {
      const errorMessage = (err as Error).message || 'Failed to fetch SMTP config';
      setError(errorMessage);
    } finally {
      setLoading(false);
    }
  };

  const fetchStatistics = async () => {
    try {
      const stats = await smtpConfigApi.getStatistics(7);
      setStatistics(stats);
    } catch (err: unknown) {
      console.error('Failed to fetch statistics:', err);
    }
  };

  const handleSave = async () => {
    // Validate required fields
    if (!formData.smtp_host || !formData.smtp_port || !formData.from_address) {
      toast.error(t('validationError', language), t('smtpRequiredFields', language));
      return;
    }

    // Validate email format
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    if (!emailRegex.test(formData.from_address)) {
      toast.error(t('validationError', language), t('invalidEmailFormat', language));
      return;
    }

    setSaving(true);
    try {
      const port = parseInt(formData.smtp_port, 10);
      if (isNaN(port) || port < 1 || port > 65535) {
        toast.error(t('validationError', language), t('invalidPortNumber', language));
        return;
      }

      const saved = await smtpConfigApi.saveConfig({
        smtp_host: formData.smtp_host,
        smtp_port: port,
        smtp_user: formData.smtp_user || undefined,
        smtp_password: formData.smtp_password || undefined,
        from_address: formData.from_address,
        use_tls: formData.use_tls,
      });

      setConfig(saved);
      toast.success(t('smtpConfigSaved', language), t('smtpConfigSavedDesc', language));

      // Clear password field after save
      setFormData({ ...formData, smtp_password: '' });
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to save SMTP config';
      toast.error(t('error', language), errorMessage);
    } finally {
      setSaving(false);
    }
  };

  const handleTestConnection = async () => {
    if (!formData.smtp_host || !formData.smtp_port || !formData.from_address) {
      toast.error(t('validationError', language), t('smtpRequiredFields', language));
      return;
    }

    setTesting(true);
    try {
      const port = parseInt(formData.smtp_port, 10);
      if (isNaN(port) || port < 1 || port > 65535) {
        toast.error(t('validationError', language), t('invalidPortNumber', language));
        return;
      }

      const result = await smtpConfigApi.testConnection({
        smtp_host: formData.smtp_host,
        smtp_port: port,
        smtp_user: formData.smtp_user || undefined,
        smtp_password: formData.smtp_password || undefined,
        from_address: formData.from_address,
        use_tls: formData.use_tls,
      });

      if (result.success) {
        toast.success(t('smtpTestSuccess', language), result.message);
        // Refresh config to get updated verification status
        fetchConfig();
      } else {
        toast.error(t('smtpTestFailed', language), result.message);
      }
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to test SMTP connection';
      toast.error(t('error', language), errorMessage);
    } finally {
      setTesting(false);
    }
  };

  const handleDelete = async () => {
    if (!window.confirm(t('confirmDeleteSmtpConfig', language))) return;

    try {
      await smtpConfigApi.deleteConfig();
      setConfig(null);
      setFormData({
        smtp_host: '',
        smtp_port: '587',
        smtp_user: '',
        smtp_password: '',
        from_address: '',
        use_tls: true,
      });
      toast.success(t('smtpConfigDeleted', language), t('smtpConfigDeletedDesc', language));
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to delete SMTP config';
      toast.error(t('error', language), errorMessage);
    }
  };

  const handleSendTestEmail = async () => {
    if (!config?.is_verified) {
      toast.error(t('error', language), t('smtpNotVerified', language));
      return;
    }

    if (!formData.from_address) {
      toast.error(t('error', language), t('enterSenderEmail', language));
      return;
    }

    try {
      const result = await smtpConfigApi.sendTestEmail(formData.from_address, language);
      if (result.success) {
        toast.success(t('testEmailSent', language), result.message);
      } else {
        toast.error(t('error', language), result.message);
      }
    } catch (err: unknown) {
      const errorMessage = (err as Error).message || 'Failed to send test email';
      toast.error(t('error', language), errorMessage);
    }
  };

  if (loading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  return (
    <div className="smtp-config">
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('smtpConfiguration', language)}</h2>
        {config && (
          <Badge variant={config.is_verified ? 'success' : 'warning'}>
            {config.is_verified ? t('verified', language) : t('notVerified', language)}
          </Badge>
        )}
      </div>

      {error && <Error message={error} onRetry={fetchConfig} />}

      {/* Configuration Form */}
      <Card className="mb-4">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSave();
          }}
        >
          <div className="row g-3">
            <div className="col-md-6">
              <label className="form-label">
                {t('smtpHost', language)} *
              </label>
              <TextInput
                value={formData.smtp_host}
                onChange={(value) => setFormData({ ...formData, smtp_host: value })}
                placeholder="smtp.example.com"
              />
            </div>

            <div className="col-md-6">
              <label className="form-label">
                {t('smtpPort', language)} *
              </label>
              <TextInput
                type="number"
                value={formData.smtp_port}
                onChange={(value) => setFormData({ ...formData, smtp_port: value })}
                placeholder="587"
              />
            </div>

            <div className="col-md-6">
              <label className="form-label">{t('smtpUser', language)}</label>
              <TextInput
                value={formData.smtp_user}
                onChange={(value) => setFormData({ ...formData, smtp_user: value })}
                placeholder="user@example.com"
              />
            </div>

            <div className="col-md-6">
              <label className="form-label">{t('smtpPassword', language)}</label>
              <input
                type="password"
                className="form-control"
                value={formData.smtp_password}
                onChange={(e) => setFormData({ ...formData, smtp_password: e.target.value })}
                placeholder={config?.smtp_password_masked || t('enterPassword', language)}
              />
              {config?.smtp_password_masked && !formData.smtp_password && (
                <small className="text-muted">
                  {t('currentPassword', language)}: {config.smtp_password_masked}
                </small>
              )}
            </div>

            <div className="col-md-6">
              <label className="form-label">
                {t('senderEmail', language)} *
              </label>
              <TextInput
                type="email"
                value={formData.from_address}
                onChange={(value) => setFormData({ ...formData, from_address: value })}
                placeholder="noreply@example.com"
              />
            </div>

            <div className="col-md-6">
              <div className="form-check form-switch mt-4">
                <input
                  className="form-check-input"
                  type="checkbox"
                  id="useTLS"
                  checked={formData.use_tls}
                  onChange={(e) => setFormData({ ...formData, use_tls: e.target.checked })}
                />
                <label className="form-check-label" htmlFor="useTLS">
                  {t('useTLS', language)}
                </label>
              </div>
            </div>

            <div className="col-12">
              <div className="d-flex gap-2">
                <Button variant="primary" onClick={handleSave} loading={saving}>
                  <i className="bi bi-save me-1" />
                  {t('save', language)}
                </Button>
                <Button variant="outline-secondary" onClick={handleTestConnection} loading={testing}>
                  <i className="bi bi-plug me-1" />
                  {t('testConnection', language)}
                </Button>
                {config?.is_verified && (
                  <Button variant="outline-info" onClick={handleSendTestEmail}>
                    <i className="bi bi-envelope me-1" />
                    {t('sendTestEmail', language)}
                  </Button>
                )}
                {config && (
                  <Button variant="outline-danger" onClick={handleDelete}>
                    <i className="bi bi-trash me-1" />
                    {t('delete', language)}
                  </Button>
                )}
              </div>
            </div>
          </div>
        </form>
      </Card>

      {/* Statistics */}
      {statistics && (
        <Card title={t('emailStatistics', language)}>
          <div className="row g-3">
            <div className="col-md-3">
              <div className="border rounded p-3 text-center">
                <h5 className="mb-1">{statistics.total_sent}</h5>
                <small className="text-muted">{t('totalEmails', language)}</small>
              </div>
            </div>
            <div className="col-md-3">
              <div className="border rounded p-3 text-center">
                <h5 className="mb-1 text-success">{statistics.successful}</h5>
                <small className="text-muted">{t('successful', language)}</small>
              </div>
            </div>
            <div className="col-md-3">
              <div className="border rounded p-3 text-center">
                <h5 className="mb-1 text-danger">{statistics.failed}</h5>
                <small className="text-muted">{t('failed', language)}</small>
              </div>
            </div>
            <div className="col-md-3">
              <div className="border rounded p-3 text-center">
                <h5 className="mb-1">{statistics.success_rate}%</h5>
                <small className="text-muted">{t('successRate', language)}</small>
              </div>
            </div>
          </div>
          <div className="mt-3">
            <small className="text-muted">
              {t('statisticsPeriod', language)}: {statistics.period_days} {t('days', language)}
            </small>
          </div>
        </Card>
      )}

      {/* Help */}
      <Card title={t('help', language)} className="mt-4">
        <div className="alert alert-info">
          <h6 className="alert-heading">{t('smtpSetupGuide', language)}</h6>
          <ul className="mb-0">
            <li>{t('smtpSetupGuide1', language)}</li>
            <li>{t('smtpSetupGuide2', language)}</li>
            <li>{t('smtpSetupGuide3', language)}</li>
            <li>{t('smtpSetupGuide4', language)}</li>
          </ul>
        </div>
      </Card>
    </div>
  );
};