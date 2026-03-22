/**
 * Workspace Component - AI workspace with iframe embedding
 */

import React, { useState, useEffect } from 'react';
import { workspaceApi } from '@/api';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Loading, Error } from '@/components/common';

export const Workspace: React.FC = () => {
  const language = useLanguage();
  const [config, setConfig] = useState<{ enabled: boolean; url: string } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadConfig = async () => {
      try {
        const workspaceConfig = await workspaceApi.getConfig();
        setConfig(workspaceConfig);
      } catch (err) {
        // Handle error safely
        const error = err as Error;
        setError(error?.message || 'Failed to load workspace config');
      } finally {
        setIsLoading(false);
      }
    };

    loadConfig();
  }, []);

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} />;
  }

  if (!config?.enabled || !config.url) {
    return (
      <div className="workspace">
        <div className="text-center py-5">
          <i className="bi bi-tools fs-1 text-muted" />
          <h4 className="mt-3">{t('workspaceNotConfigured', language)}</h4>
          <p className="text-muted">{t('workspaceNotConfiguredHelp', language)}</p>
        </div>
      </div>
    );
  }

  return (
    <div className="workspace h-100">
      <iframe
        src={config.url}
        title="AI Workspace"
        className="w-100"
        style={{
          height: 'calc(100vh - 160px)',
          border: 'none',
          borderRadius: '0.375rem',
        }}
        allow="clipboard-read; clipboard-write"
      />
    </div>
  );
};
