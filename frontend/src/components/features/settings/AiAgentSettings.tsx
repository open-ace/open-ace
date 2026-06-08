/**
 * AiAgentSettings Component - AI Agent settings page
 *
 * Features:
 * - Configure the GitHub account used by autonomous workflows
 * - Token input with show/hide toggle
 * - Test connection for both new and already-saved tokens
 * - Author name and email for git commits
 * - Dirty detection: Save button only enabled when form has changes
 */

import React, { useState, useEffect, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, TextInput, Loading, Error as ErrorDisplay } from '@/components/common';
import { useToast } from '@/components/common';
import { useAiAgentSettings, useUpdateAiAgentSettings, useValidateGithubToken } from '@/hooks';
import type { AiAgentSettings as AiAgentSettingsType } from '@/api/aiAgentSettings';

export const AiAgentSettings: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();

  // Data
  const { data: settings, isLoading, isError, error: fetchError, refetch } = useAiAgentSettings();
  const updateSettings = useUpdateAiAgentSettings();
  const validateToken = useValidateGithubToken();

  // Form state — token is managed separately from name/email
  const [newToken, setNewToken] = useState('');
  const [authorName, setAuthorName] = useState('');
  const [authorEmail, setAuthorEmail] = useState('');
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
      setAuthorName(settings.ai_github_author_name || 'Open ACE AI');
      setAuthorEmail(settings.ai_github_author_email || 'bot@open-ace.com');
      setNewToken('');
    }
  }, [settings]);

  // Dirty detection: compare current form values against loaded settings
  const isDirty = useMemo(() => {
    if (!settings) return false;

    // Token is dirty if user typed something new
    if (newToken.trim() !== '') return true;

    // Name/email dirty if different from loaded settings
    const origName = settings.ai_github_author_name || 'Open ACE AI';
    const origEmail = settings.ai_github_author_email || 'bot@open-ace.com';
    if (authorName !== origName) return true;
    if (authorEmail !== origEmail) return true;

    return false;
  }, [newToken, authorName, authorEmail, settings]);

  const hasMaskedToken = settings?.ai_github_token?.includes('****');
  const hasNewToken = newToken.trim() !== '' && !newToken.includes('****');

  const handleSave = async () => {
    try {
      const payload: Partial<AiAgentSettingsType> = {
        ai_github_author_name: authorName,
        ai_github_author_email: authorEmail,
      };
      // Only include token if user entered a new one
      if (hasNewToken) {
        payload.ai_github_token = newToken.trim();
      }
      await updateSettings.mutateAsync(payload);
      toast.success(t('settingsSaved', language));
      setNewToken('');
      setValidationResult(null);
      // Await refetch so useEffect updates authorName/authorEmail immediately,
      // ensuring isDirty becomes false before the next render.
      await refetch();
    } catch (err) {
      console.error('Failed to save AI agent settings:', err);
      toast.error(t('error', language));
    }
  };

  // Test a new token entered by the user
  const handleTestNewToken = async () => {
    if (!hasNewToken) {
      toast.warning(t('aiGithubEnterTokenFirst', language));
      return;
    }

    setIsValidating(true);
    setValidationResult(null);
    try {
      const result = await validateToken.mutateAsync({ token: newToken.trim() });
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

  // Test the already-saved token (server reads from DB)
  const handleTestSavedToken = async () => {
    setIsValidating(true);
    setValidationResult(null);
    try {
      // Use explicit source field instead of embedding meaning in token value
      const result = await validateToken.mutateAsync({ source: 'saved' });
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
        <div className="alert alert-success d-flex align-items-center mb-3" role="status">
          <i className="bi bi-check-circle-fill me-2" />
          <span>
            <i className="bi bi-github me-1" />
            {t('aiGithubAccountConfigured', language)}
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
                value={newToken}
                onChange={(e) => setNewToken(e.target.value)}
              />
              <Button variant="outline-secondary" onClick={() => setShowToken(!showToken)}>
                <i className={`bi ${showToken ? 'bi-eye-slash' : 'bi-eye'}`} />
              </Button>
            </div>
            <small className="text-muted">{t('aiGithubTokenHelp', language)}</small>
          </div>

          {/* Test Connection */}
          <div className="col-12 d-flex gap-2 flex-wrap">
            <Button
              variant="outline-primary"
              onClick={handleTestNewToken}
              loading={isValidating && hasNewToken}
              disabled={!hasNewToken}
            >
              <i className="bi bi-plug me-1" />
              {t('aiGithubTestConnection', language)}
            </Button>
            {hasMaskedToken && (
              <Button
                variant="outline-secondary"
                onClick={handleTestSavedToken}
                loading={isValidating && !hasNewToken}
              >
                <i className="bi bi-arrow-repeat me-1" />
                {t('aiGithubTestSavedToken', language)}
              </Button>
            )}
            {validationResult && (
              <div
                className={`alert ${validationResult.valid ? 'alert-success' : 'alert-danger'} mt-2 mb-0 py-2 w-100`}
              >
                <i
                  className={`bi ${validationResult.valid ? 'bi-check-circle-fill' : 'bi-x-circle-fill'} me-1`}
                />
                {validationResult.valid
                  ? t('aiGithubTestSuccess', language).replace(
                      '{username}',
                      validationResult.username ?? ''
                    )
                  : t('aiGithubTestFailed', language).replace(
                      '{error}',
                      validationResult.error ?? ''
                    )}
              </div>
            )}
          </div>

          {/* Author Name */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('aiGithubAuthorName', language)}</label>
            <TextInput
              value={authorName}
              onChange={(value: string) => setAuthorName(value)}
              placeholder="Open ACE AI"
            />
            <small className="text-muted">{t('aiGithubAuthorNameHelp', language)}</small>
          </div>

          {/* Author Email */}
          <div className="col-md-6">
            <label className="form-label fw-semibold">{t('aiGithubAuthorEmail', language)}</label>
            <TextInput
              value={authorEmail}
              onChange={(value: string) => setAuthorEmail(value)}
              placeholder="bot@open-ace.com"
            />
            <small className="text-muted">{t('aiGithubAuthorEmailHelp', language)}</small>
          </div>
        </div>
      </Card>

      {/* Save / Reset Buttons */}
      <div className="d-flex gap-2 justify-content-end">
        <Button
          variant="secondary"
          onClick={() => {
            if (settings) {
              setAuthorName(settings.ai_github_author_name || 'Open ACE AI');
              setAuthorEmail(settings.ai_github_author_email || 'bot@open-ace.com');
              setNewToken('');
              setValidationResult(null);
            }
          }}
        >
          {t('reset', language)}
        </Button>
        <Button
          variant="primary"
          onClick={handleSave}
          loading={updateSettings.isPending}
          disabled={!isDirty}
        >
          {t('save', language)}
        </Button>
      </div>
    </div>
  );
};
