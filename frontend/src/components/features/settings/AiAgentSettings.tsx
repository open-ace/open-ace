/**
 * AiAgentSettings Component - AI Agent settings page
 *
 * Features:
 * - Configure the GitHub account used by autonomous workflows
 * - Token input with show/hide toggle
 * - Test connection to validate the token
 * - Author name and email for git commits
 */

import React, { useState, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  TextInput,
  Loading,
  Error as ErrorDisplay,
} from '@/components/common';
import { useToast } from '@/components/common';
import {
  useAiAgentSettings,
  useUpdateAiAgentSettings,
  useValidateGithubToken,
} from '@/hooks';
import type { AiAgentSettings as AiAgentSettingsType } from '@/api/aiAgentSettings';

export const AiAgentSettings: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();

  // Data
  const {
    data: settings,
    isLoading,
    isError,
    error: fetchError,
    refetch,
  } = useAiAgentSettings();
  const updateSettings = useUpdateAiAgentSettings();
  const validateToken = useValidateGithubToken();

  // Form state
  const [formData, setFormData] = useState<Partial<AiAgentSettingsType>>({});
  const [showToken, setShowToken] = useState(false);
  const [validationResult, setValidationResult] = useState<{
    valid: boolean;
    username?: string;
    error?: string;
  } | null>(null);
  const [isValidating, setIsValidating] = useState(false);

  // Initialize form when settings load
  useEffect(() => {
    if (settings) {
      // Don't pre-fill token if it's masked
      const tokenValue = settings.ai_github_token || '';
      setFormData({
        ai_github_author_name: settings.ai_github_author_name || 'Open ACE AI',
        ai_github_author_email: settings.ai_github_author_email || 'bot@open-ace.com',
        // Only set token if it's empty (not masked)
        ...(tokenValue === '' ? { ai_github_token: '' } : {}),
      });
    }
  }, [settings]);

  const handleSave = async () => {
    try {
      await updateSettings.mutateAsync(formData);
      toast.success(t('settingsSaved', language));
      setFormData((prev) => ({
        ...prev,
        // Clear token from form after save (it will be masked on reload)
        ai_github_token: undefined,
      }));
      refetch();
    } catch (err) {
      console.error('Failed to save AI agent settings:', err);
      toast.error(t('error', language));
    }
  };

  const handleTestConnection = async () => {
    const tokenToTest = formData.ai_github_token;
    if (!tokenToTest || tokenToTest.includes('****')) {
      toast.warning(
        language === 'zh'
          ? '请先输入新的令牌再测试'
          : 'Please enter a new token before testing'
      );
      return;
    }

    setIsValidating(true);
    setValidationResult(null);
    try {
      const result = await validateToken.mutateAsync(tokenToTest);
      setValidationResult(result);
    } catch (err) {
      setValidationResult({
        valid: false,
        error: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      setIsValidating(false);
    }
  };

  const hasMaskedToken = settings?.ai_github_token?.includes('****');
  const hasNewToken = formData.ai_github_token && !formData.ai_github_token.includes('****');

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return (
      <ErrorDisplay
        message={fetchError?.message || t('error', language)}
        onRetry={() => refetch()}
      />
    );
  }

  return (
    <div className="ai-agent-settings">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h2>{t('aiAgentSettings', language)}</h2>
      </div>

      {/* Status Banner */}
      {hasMaskedToken && !hasNewToken && (
        <div className="alert alert-info d-flex align-items-center mb-3" role="alert">
          <i className="bi bi-info-circle-fill me-2" />
          <span>
            <i className="bi bi-github me-1" />
            {language === 'zh'
              ? '已配置 AI GitHub 账户'
              : 'AI GitHub account configured'}
            {' — '}
            <code>{settings?.ai_github_token}</code>
          </span>
        </div>
      )}
      {!hasMaskedToken && !hasNewToken && (
        <div className="alert alert-warning d-flex align-items-center mb-3" role="alert">
          <i className="bi bi-exclamation-triangle-fill me-2" />
          {t('aiGithubTokenNotConfigured', language)}
        </div>
      )}

      {/* GitHub Account Card */}
      <Card title={t('aiGithubToken', language)} className="mb-4">
        <div className="row g-3">
          {/* Token */}
          <div className="col-12">
            <label className="form-label fw-semibold">{t('aiGithubToken', language)}</label>
            <div className="input-group">
              <input
                type={showToken ? 'text' : 'password'}
                className="form-control"
                placeholder={t('aiGithubTokenPlaceholder', language)}
                value={
                  formData.ai_github_token !== undefined
                    ? formData.ai_github_token
                    : settings?.ai_github_token || ''
                }
                onChange={(e) =>
                  setFormData((prev) => ({ ...prev, ai_github_token: e.target.value }))
                }
              />
              <Button
                variant="outline-secondary"
                onClick={() => setShowToken(!showToken)}
              >
                <i className={`bi ${showToken ? 'bi-eye-slash' : 'bi-eye'}`} />
              </Button>
            </div>
            <small className="text-muted">{t('aiGithubTokenHelp', language)}</small>
          </div>

          {/* Test Connection */}
          <div className="col-12">
            <Button
              variant="outline-primary"
              onClick={handleTestConnection}
              loading={isValidating}
              disabled={!hasNewToken}
            >
              <i className="bi bi-plug me-1" />
              {t('aiGithubTestConnection', language)}
            </Button>
            {validationResult && (
              <div
                className={`alert ${validationResult.valid ? 'alert-success' : 'alert-danger'} mt-2 mb-0 py-2`}
              >
                <i
                  className={`bi ${validationResult.valid ? 'bi-check-circle-fill' : 'bi-x-circle-fill'} me-1`}
                />
                {validationResult.valid
                  ? t('aiGithubTestSuccess', language).replace(
                      '{username}',
                      validationResult.username || ''
                    )
                  : t('aiGithubTestFailed', language).replace(
                      '{error}',
                      validationResult.error || ''
                    )}
              </div>
            )}
          </div>

          {/* Author Name */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('aiGithubAuthorName', language)}</label>
            <TextInput
              value={formData.ai_github_author_name || ''}
              onChange={(value: string) =>
                setFormData((prev) => ({ ...prev, ai_github_author_name: value }))
              }
              placeholder="Open ACE AI"
            />
            <small className="text-muted">{t('aiGithubAuthorNameHelp', language)}</small>
          </div>

          {/* Author Email */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('aiGithubAuthorEmail', language)}</label>
            <TextInput
              value={formData.ai_github_author_email || ''}
              onChange={(value: string) =>
                setFormData((prev) => ({ ...prev, ai_github_author_email: value }))
              }
              placeholder="bot@open-ace.com"
            />
            <small className="text-muted">{t('aiGithubAuthorEmailHelp', language)}</small>
          </div>
        </div>
      </Card>

      {/* Save Button */}
      <div className="d-flex gap-2 justify-content-end">
        <Button
          variant="primary"
          onClick={handleSave}
          loading={updateSettings.isPending}
        >
          {t('save', language)}
        </Button>
      </div>
    </div>
  );
};
