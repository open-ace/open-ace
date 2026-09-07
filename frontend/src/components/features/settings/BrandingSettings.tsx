/**
 * BrandingSettings Component - System branding configuration page
 * Issue #3271: Custom branding for login page
 *
 * Features:
 * - Logo URL input with preview
 * - System name configuration
 * - Multi-language welcome message editor
 * - Copyright text configuration
 * - Save and preview functionality
 */

import React, { useState, useEffect, useCallback } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Card, Button, TextInput, Loading, useToast } from '@/components/common';
import { systemApi, type SystemSettings } from '@/api';

const SUPPORTED_LANGUAGES = [
  { code: 'en', label: 'English' },
  { code: 'zh', label: '中文' },
  { code: 'ja', label: '日本語' },
  { code: 'ko', label: '한국어' },
] as const;

type LanguageCode = (typeof SUPPORTED_LANGUAGES)[number]['code'];

export const BrandingSettings: React.FC = () => {
  const language = useLanguage();
  const { success, error: toastError } = useToast();

  const [isLoading, setIsLoading] = useState(true);
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Branding settings state
  const [logoUrl, setLogoUrl] = useState<string>('');
  const [systemName, setSystemName] = useState<string>('');
  const [welcomeMessages, setWelcomeMessages] = useState<Record<LanguageCode, string>>({
    en: '',
    zh: '',
    ja: '',
    ko: '',
  });
  const [copyrightText, setCopyrightText] = useState<string>('');

  // Active language tab
  const [activeLang, setActiveLang] = useState<LanguageCode>('en');

  // Logo preview state
  const [logoError, setLogoError] = useState(false);

  // Fetch current branding settings
  const fetchSettings = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const settings = await systemApi.getSystemSettings();
      setLogoUrl((settings.brand_logo_url as string) || '');
      setSystemName((settings.brand_system_name as string) || '');
      setCopyrightText((settings.brand_copyright_text as string) || '');

      // Parse welcome messages
      const messages = (settings.brand_welcome_message as Record<string, string>) || {};
      setWelcomeMessages({
        en: messages.en || '',
        zh: messages.zh || '',
        ja: messages.ja || '',
        ko: messages.ko || '',
      });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to fetch branding settings';
      setError(errorMessage);
      toastError(t('failedToFetchBrandingSettings', language));
    } finally {
      setIsLoading(false);
    }
  }, [language, toastError]);

  useEffect(() => {
    fetchSettings();
  }, [fetchSettings]);

  // Handle save
  const handleSave = async () => {
    setIsSaving(true);
    try {
      // Build settings object (only include non-empty fields)
      const settings: Partial<SystemSettings> = {};

      if (logoUrl.trim()) {
        settings.brand_logo_url = logoUrl.trim();
      } else {
        settings.brand_logo_url = null;
      }

      if (systemName.trim()) {
        settings.brand_system_name = systemName.trim();
      } else {
        settings.brand_system_name = null;
      }

      // Only include non-empty welcome messages
      const nonEmptyMessages: Record<string, string> = {};
      for (const [lang, msg] of Object.entries(welcomeMessages)) {
        if (msg.trim()) {
          nonEmptyMessages[lang] = msg.trim();
        }
      }
      if (Object.keys(nonEmptyMessages).length > 0) {
        settings.brand_welcome_message = nonEmptyMessages;
      } else {
        settings.brand_welcome_message = null;
      }

      if (copyrightText.trim()) {
        settings.brand_copyright_text = copyrightText.trim();
      } else {
        settings.brand_copyright_text = null;
      }

      await systemApi.updateSystemSettings(settings);
      success(t('brandingSettingsSaved', language));
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to save branding settings';
      toastError(errorMessage);
    } finally {
      setIsSaving(false);
    }
  };

  // Handle welcome message change
  const handleWelcomeMessageChange = (lang: LanguageCode, value: string) => {
    setWelcomeMessages((prev) => ({
      ...prev,
      [lang]: value,
    }));
  };

  // Handle logo error
  const handleLogoError = () => {
    setLogoError(true);
  };

  // Handle logo load success
  const handleLogoLoad = () => {
    setLogoError(false);
  };

  if (isLoading) {
    return (
      <div className="flex justify-center items-center min-h-96">
        <Loading text={t('loading', language)} />
      </div>
    );
  }

  if (error) {
    return (
      <Card className="p-6">
        <div className="text-red-600 dark:text-red-400">{error}</div>
        <Button variant="outline-primary" onClick={fetchSettings} className="mt-4">
          {t('retry', language)}
        </Button>
      </Card>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex justify-between items-center">
        <h1 className="text-2xl font-bold">{t('brandingSettings', language)}</h1>
        <Button variant="primary" onClick={handleSave} disabled={isSaving}>
          {isSaving ? t('saving', language) : t('save', language)}
        </Button>
      </div>

      {/* Logo Configuration */}
      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">{t('logoConfiguration', language)}</h2>
        <div className="space-y-4">
          <TextInput
            label={t('logoUrl', language)}
            value={logoUrl}
            onChange={setLogoUrl}
            placeholder="https://example.com/logo.png"
            hint={t('logoUrlHelper', language)}
          />

          {/* Logo Preview */}
          {logoUrl && (
            <div className="mt-4">
              <label className="block text-sm font-medium mb-2">{t('logoPreview', language)}</label>
              <div className="flex items-center justify-center w-32 h-32 border-2 border-dashed border-gray-300 dark:border-gray-600 rounded-lg bg-gray-50 dark:bg-gray-800">
                {logoError ? (
                  <span className="text-sm text-gray-500">{t('logoLoadFailed', language)}</span>
                ) : (
                  <img
                    src={logoUrl}
                    alt="Logo preview"
                    className="max-w-full max-h-full object-contain"
                    onError={handleLogoError}
                    onLoad={handleLogoLoad}
                  />
                )}
              </div>
            </div>
          )}
        </div>
      </Card>

      {/* System Name */}
      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">{t('systemNameConfiguration', language)}</h2>
        <TextInput
          label={t('systemName', language)}
          value={systemName}
          onChange={setSystemName}
          placeholder="Open ACE"
          hint={t('systemNameHelper', language)}
        />
      </Card>

      {/* Welcome Message (Multi-language) */}
      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">{t('welcomeMessageConfiguration', language)}</h2>
        <p className="text-sm text-gray-600 dark:text-gray-400 mb-4">
          {t('welcomeMessageHelper', language)}
        </p>

        {/* Language Tabs */}
        <div className="border-b border-gray-200 dark:border-gray-700 mb-4">
          <nav className="-mb-px flex space-x-8">
            {SUPPORTED_LANGUAGES.map((lang) => (
              <button
                key={lang.code}
                onClick={() => setActiveLang(lang.code)}
                className={cn(
                  'py-2 px-1 border-b-2 font-medium text-sm transition-colors',
                  activeLang === lang.code
                    ? 'border-primary-500 text-primary-600 dark:text-primary-400'
                    : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300 dark:text-gray-400 dark:hover:text-gray-300'
                )}
              >
                {lang.label}
              </button>
            ))}
          </nav>
        </div>

        {/* Welcome Message Input */}
        <TextInput
          label={`${t('welcomeMessage', language)} (${SUPPORTED_LANGUAGES.find((l) => l.code === activeLang)?.label})`}
          value={welcomeMessages[activeLang]}
          onChange={(value) => handleWelcomeMessageChange(activeLang, value)}
          placeholder={t('welcomeMessagePlaceholder', language)}
        />
      </Card>

      {/* Copyright Text */}
      <Card className="p-6">
        <h2 className="text-lg font-semibold mb-4">{t('copyrightConfiguration', language)}</h2>
        <TextInput
          label={t('copyrightText', language)}
          value={copyrightText}
          onChange={setCopyrightText}
          placeholder="© 2026 Open ACE. All rights reserved."
          hint={t('copyrightHelper', language)}
        />
      </Card>

      {/* Info Card */}
      <Card className="p-6 bg-blue-50 dark:bg-blue-900/20 border-blue-200 dark:border-blue-800">
        <div className="flex items-start space-x-3">
          <i className="bi bi-info-circle text-blue-600 dark:text-blue-400 text-xl mt-0.5" />
          <div className="text-sm text-blue-800 dark:text-blue-200">
            <p className="font-medium mb-1">{t('brandingInfoTitle', language)}</p>
            <p>{t('brandingInfoDescription', language)}</p>
          </div>
        </div>
      </Card>
    </div>
  );
};

export default BrandingSettings;
