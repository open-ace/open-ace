/**
 * Workspace Component - AI workspace with iframe embedding and tab support
 *
 * Features:
 * - Multiple tabs for different conversations
 * - Each tab has its own iframe
 * - Support for creating new tabs via URL parameter
 * - Quota checking - disables workspace when quota exceeded
 * - Multi-user mode support with per-user webui instances
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useSearchParams, useNavigate } from 'react-router-dom';
import { workspaceApi, type WorkspaceConfig, type UserWebUIResponse } from '@/api';
import { requestApi, type QuotaStatusResponse } from '@/api/request';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Error, Button, Card } from '@/components/common';
import { cn } from '@/utils';

interface WorkspaceTab {
  id: string;
  title: string;
  url: string;
  token: string;
  createdAt: number;
}

// Generate unique tab ID
const generateTabId = () => `tab-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

// Quota check interval (5 minutes)
const QUOTA_CHECK_INTERVAL = 5 * 60 * 1000;

// Activity heartbeat interval (2 minutes)
const ACTIVITY_HEARTBEAT_INTERVAL = 2 * 60 * 1000;

export const Workspace: React.FC = () => {
  const language = useLanguage();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [config, setConfig] = useState<WorkspaceConfig | null>(null);
  const [userWebUI, setUserWebUI] = useState<UserWebUIResponse | null>(null);
  const [quotaStatus, setQuotaStatus] = useState<QuotaStatusResponse | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isQuotaLoading, setIsQuotaLoading] = useState(true);
  const [loadingStage, setLoadingStage] = useState<string>('initializing'); // Track loading progress
  const [error, setError] = useState<string | null>(null);
  const [tabs, setTabs] = useState<WorkspaceTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>('');
  const [loadingTabs, setLoadingTabs] = useState<Set<string>>(new Set());

  // Load workspace config and user webui URL
  useEffect(() => {
    const loadConfig = async () => {
      try {
        setLoadingStage('loadingConfig');
        const workspaceConfig = await workspaceApi.getConfig();
        setConfig(workspaceConfig);

        // If multi-user mode is enabled, get user-specific URL
        if (workspaceConfig.enabled && workspaceConfig.multi_user_mode) {
          setLoadingStage('startingWorkspace');
          const userWebUIResponse = await workspaceApi.getUserWebUIUrl();
          if (userWebUIResponse.success) {
            setUserWebUI(userWebUIResponse);
            setLoadingStage('ready');
          } else {
            setError(userWebUIResponse.error || 'Failed to get user workspace URL');
          }
        } else {
          setLoadingStage('ready');
        }
      } catch (err) {
        const error = err as Error;
        setError(error?.message || 'Failed to load workspace config');
      } finally {
        setIsLoading(false);
      }
    };

    loadConfig();
  }, []);

  // Activity heartbeat for multi-user mode
  useEffect(() => {
    if (!config?.multi_user_mode || !userWebUI?.success) return;

    const sendHeartbeat = async () => {
      try {
        await workspaceApi.getUserWebUIUrl();
      } catch (err) {
        console.error('Failed to send activity heartbeat:', err);
      }
    };

    const interval = setInterval(sendHeartbeat, ACTIVITY_HEARTBEAT_INTERVAL);
    return () => clearInterval(interval);
  }, [config?.multi_user_mode, userWebUI?.success]);

  // Check quota
  const checkQuota = useCallback(async () => {
    try {
      const status = await requestApi.getQuotaStatus();
      setQuotaStatus(status);
    } catch (err) {
      console.error('Failed to check quota:', err);
    } finally {
      setIsQuotaLoading(false);
    }
  }, []);

  // Initial quota check and periodic checks
  useEffect(() => {
    checkQuota();

    const interval = setInterval(checkQuota, QUOTA_CHECK_INTERVAL);

    return () => clearInterval(interval);
  }, [checkQuota]);

  // Get the effective URL for iframe
  const getEffectiveUrl = useCallback((): string => {
    if (!config?.enabled) return '';

    // Multi-user mode: use user-specific URL with token and openace_url
    if (config.multi_user_mode && userWebUI?.success) {
      const baseUrl = userWebUI.url;
      const token = userWebUI.token;
      const openaceUrl = userWebUI.openace_url;
      // Add token and openace_url as URL parameters
      const separator = baseUrl.includes('?') ? '&' : '?';
      let url = `${baseUrl}${separator}token=${encodeURIComponent(token)}`;
      if (openaceUrl) {
        url = `${url}&openace_url=${encodeURIComponent(openaceUrl)}`;
      }
      return url;
    }

    // Single-user mode: use configured URL
    return config.url;
  }, [config, userWebUI]);

  // Initialize first tab when config is loaded
  useEffect(() => {
    // Wait for both config and userWebUI (in multi-user mode) to be loaded
    if (!config?.enabled) return;

    // In multi-user mode, wait for userWebUI to be loaded
    if (config.multi_user_mode && !userWebUI?.success) return;

    const effectiveUrl = getEffectiveUrl();
    if (effectiveUrl && tabs.length === 0) {
      const initialTab: WorkspaceTab = {
        id: generateTabId(),
        title: t('newSession', language),
        url: effectiveUrl,
        token: userWebUI?.token || '',
        createdAt: Date.now(),
      };
      setTabs([initialTab]);
      setActiveTabId(initialTab.id);
      // Mark as loading
      setLoadingTabs(new Set([initialTab.id]));
    }
  }, [config, userWebUI, tabs.length, language, getEffectiveUrl]);

  // Handle URL parameter for creating new tab
  useEffect(() => {
    const newTab = searchParams.get('newTab');
    const effectiveUrl = getEffectiveUrl();
    if (newTab === 'true' && config?.enabled && effectiveUrl) {
      // Clear the URL parameter
      searchParams.delete('newTab');
      setSearchParams(searchParams, { replace: true });

      // Create new tab
      createNewTab();
    }
  }, [searchParams, config, getEffectiveUrl]);

  // Create a new tab
  const createNewTab = useCallback(() => {
    const effectiveUrl = getEffectiveUrl();
    if (!effectiveUrl) return;

    const newTab: WorkspaceTab = {
      id: generateTabId(),
      title: t('newSession', language),
      url: effectiveUrl,
      token: userWebUI?.token || '',
      createdAt: Date.now(),
    };

    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
    // Mark as loading
    setLoadingTabs((prev) => new Set(prev).add(newTab.id));
  }, [getEffectiveUrl, userWebUI, language]);

  // Close a tab
  const closeTab = useCallback(
    (tabId: string, e: React.MouseEvent) => {
      e.stopPropagation();

      setTabs((prev) => {
        const newTabs = prev.filter((tab) => tab.id !== tabId);

        // If closing the active tab, switch to another tab
        if (activeTabId === tabId && newTabs.length > 0) {
          // Find the previous tab or the first one
          const closedIndex = prev.findIndex((tab) => tab.id === tabId);
          const newActiveIndex = Math.min(closedIndex, newTabs.length - 1);
          setActiveTabId(newTabs[newActiveIndex].id);
        }

        return newTabs;
      });
    },
    [activeTabId]
  );

  // Switch to a tab
  const switchTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
  }, []);

  // Handle iframe load complete
  const handleIframeLoad = useCallback((tabId: string) => {
    setLoadingTabs((prev) => {
      const newSet = new Set(prev);
      newSet.delete(tabId);
      return newSet;
    });
  }, []);

  // Navigate to usage page
  const goToUsage = useCallback(() => {
    navigate('/work/usage');
  }, [navigate]);

  if (isLoading || isQuotaLoading) {
    // Get loading message based on stage
    const getLoadingMessage = (): string => {
      switch (loadingStage) {
        case 'loadingConfig':
          return t('loadingWorkspaceConfig', language) || 'Loading workspace configuration...';
        case 'startingWorkspace':
          return t('startingWorkspaceInstance', language) || 'Starting your workspace instance...';
        case 'ready':
          return t('workspaceReady', language) || 'Workspace ready!';
        default:
          return t('loading', language);
      }
    };

    // Show progress indicator for workspace startup
    const showProgress = loadingStage === 'startingWorkspace';

    return (
      <div className="workspace-loading d-flex align-items-center justify-content-center h-100">
        <div className="text-center">
          <div className="spinner-border text-primary mb-3" role="status">
            <span className="visually-hidden">{t('loading', language)}</span>
          </div>
          <h5 className="mb-2">{getLoadingMessage()}</h5>
          {showProgress && (
            <p className="text-muted small mb-3">
              {t('workspaceStartupNote', language) || 'This may take a few seconds on first visit'}
            </p>
          )}
          <div className="progress-steps mt-3">
            <div className={`progress-step ${loadingStage === 'loadingConfig' || loadingStage === 'startingWorkspace' || loadingStage === 'ready' ? 'active' : ''}`}>
              <i className="bi bi-check-circle-fill" />
              <span>{t('loadingConfig', language) || 'Load config'}</span>
            </div>
            <div className={`progress-step ${loadingStage === 'startingWorkspace' || loadingStage === 'ready' ? 'active' : ''}`}>
              <i className={`bi ${loadingStage === 'startingWorkspace' ? 'bi-arrow-repeat spin' : 'bi-check-circle-fill'}`} />
              <span>{t('startingInstance', language) || 'Start instance'}</span>
            </div>
            <div className={`progress-step ${loadingStage === 'ready' ? 'active' : ''}`}>
              <i className="bi bi-check-circle-fill" />
              <span>{t('ready', language) || 'Ready'}</span>
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (error) {
    return <Error message={error} />;
  }

  if (!config?.enabled) {
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

  // In multi-user mode, check if user webui is available
  if (config.multi_user_mode && !userWebUI?.success) {
    return (
      <div className="workspace">
        <div className="text-center py-5">
          <i className="bi bi-exclamation-circle fs-1 text-warning" />
          <h4 className="mt-3">{t('workspaceUnavailable', language)}</h4>
          <p className="text-muted">{userWebUI?.error || t('workspaceUnavailableHelp', language)}</p>
          <Button variant="primary" onClick={() => window.location.reload()}>
            <i className="bi bi-arrow-clockwise me-2" />
            {t('retry', language)}
          </Button>
        </div>
      </div>
    );
  }

  const effectiveUrl = getEffectiveUrl();
  if (!effectiveUrl) {
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

  // Check if quota is exceeded
  const isQuotaExceeded = quotaStatus?.over_quota?.any ?? false;

  // Render quota exceeded message
  if (isQuotaExceeded) {
    return (
      <div className="workspace h-100 d-flex flex-column">
        {/* Page Header */}
        <div className="page-header mb-3 px-3 pt-3">
          <h2>{t('workspace', language)}</h2>
        </div>

        {/* Quota Exceeded Warning */}
        <div className="flex-grow-1 d-flex align-items-center justify-content-center px-3">
          <Card className="text-center" style={{ maxWidth: '500px' }}>
            <div className="py-4">
              <i className="bi bi-exclamation-triangle-fill text-warning fs-1 mb-3" />
              <h4 className="text-danger mb-3">{t('quotaExceeded', language)}</h4>
              <p className="text-muted mb-4">
                {quotaStatus?.over_quota.daily_request && (
                  <span className="d-block">{t('dailyRequestQuotaExceeded', language)}</span>
                )}
                {quotaStatus?.over_quota.monthly_request && (
                  <span className="d-block">{t('monthlyRequestQuotaExceeded', language)}</span>
                )}
                {quotaStatus?.over_quota.daily_token && (
                  <span className="d-block">{t('dailyTokenQuotaExceeded', language)}</span>
                )}
                {quotaStatus?.over_quota.monthly_token && (
                  <span className="d-block">{t('monthlyTokenQuotaExceeded', language)}</span>
                )}
              </p>
              <p className="text-muted small mb-4">
                {t('quotaLimitsHelpDesc', language)}
              </p>
              <div className="d-flex gap-2 justify-content-center">
                <Button variant="outline-primary" onClick={goToUsage}>
                  <i className="bi bi-bar-chart me-2" />
                  {t('myUsage', language)}
                </Button>
                <Button variant="primary" onClick={checkQuota}>
                  <i className="bi bi-arrow-clockwise me-2" />
                  {t('retry', language)}
                </Button>
              </div>
            </div>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="workspace h-100 d-flex flex-column">
      {/* Page Header */}
      <div className="page-header mb-3 px-3 pt-3">
        <h2>{t('workspace', language)}</h2>
        {config.multi_user_mode && userWebUI?.system_account && (
          <small className="text-muted ms-2">
            ({userWebUI.system_account})
          </small>
        )}
      </div>
      {/* Tab Bar */}
      {tabs.length > 0 && (
        <div
          className="workspace-tabs d-flex align-items-center border-bottom bg-light"
          style={{ minHeight: '40px' }}
        >
          {/* Tabs */}
          <div className="d-flex flex-grow-1" style={{ overflowX: 'auto', overflowY: 'hidden' }}>
            {tabs.map((tab) => (
              <div
                key={tab.id}
                className={cn(
                  'workspace-tab d-flex align-items-center px-3 py-2 cursor-pointer',
                  'border-end position-relative',
                  activeTabId === tab.id && 'active bg-white'
                )}
                onClick={() => switchTab(tab.id)}
                style={{
                  minWidth: '120px',
                  maxWidth: '200px',
                  flexShrink: 0,
                  userSelect: 'none',
                }}
              >
                <i className="bi bi-chat-dots me-2 text-muted" />
                <span className="text-truncate flex-grow-1 small">{tab.title}</span>
                {tabs.length > 1 && (
                  <button
                    className="btn btn-sm btn-link p-0 ms-2 text-muted"
                    onClick={(e) => closeTab(tab.id, e)}
                    title={t('close', language)}
                    style={{ lineHeight: 1 }}
                  >
                    <i className="bi bi-x" />
                  </button>
                )}
              </div>
            ))}
          </div>

          {/* New Tab Button */}
          <button
            className="btn btn-sm btn-link px-3 py-2 text-muted"
            onClick={createNewTab}
            title={t('newSession', language)}
          >
            <i className="bi bi-plus-lg" />
          </button>
        </div>
      )}

      {/* Tab Content */}
      <div className="workspace-content flex-grow-1 position-relative">
        {tabs.map((tab) => (
          <div
            key={tab.id}
            className={cn(
              'position-absolute top-0 start-0 w-100 h-100',
              activeTabId === tab.id ? 'd-block' : 'd-none'
            )}
          >
            {/* Loading overlay */}
            {loadingTabs.has(tab.id) && (
              <div
                className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-light"
                style={{ zIndex: 10 }}
              >
                <div className="text-center">
                  <div className="spinner-border text-primary mb-3" role="status">
                    <span className="visually-hidden">{t('loading', language)}</span>
                  </div>
                  <p className="text-muted">{t('workspaceLoading', language)}</p>
                </div>
              </div>
            )}
            <iframe
              src={tab.url}
              title={`Workspace - ${tab.title}`}
              className="w-100 h-100"
              style={{ border: 'none' }}
              allow="clipboard-read; clipboard-write"
              onLoad={() => handleIframeLoad(tab.id)}
            />
          </div>
        ))}
      </div>

      {/* Styles */}
      <style>{`
        .workspace-tab {
          transition: background-color 0.15s ease;
        }
        .workspace-tab:hover {
          background-color: rgba(0, 0, 0, 0.05);
        }
        .workspace-tab.active {
          border-bottom: 2px solid var(--primary, #0d6efd);
          margin-bottom: -1px;
        }
        .workspace-tab.active::after {
          content: '';
          position: absolute;
          bottom: 0;
          left: 0;
          right: 0;
          height: 2px;
          background: var(--primary, #0d6efd);
        }
        .workspace-tabs::-webkit-scrollbar {
          height: 4px;
        }
        .workspace-tabs::-webkit-scrollbar-thumb {
          background: #ccc;
          border-radius: 2px;
        }
        .workspace-tabs::-webkit-scrollbar-track {
          background: transparent;
        }
        /* Loading progress steps */
        .workspace-loading {
          padding: 20px;
        }
        .progress-steps {
          display: flex;
          justify-content: center;
          gap: 20px;
        }
        .progress-step {
          display: flex;
          align-items: center;
          gap: 8px;
          padding: 8px 16px;
          border-radius: 8px;
          background: rgba(0, 0, 0, 0.05);
          opacity: 0.5;
          transition: opacity 0.3s ease, background-color 0.3s ease;
        }
        .progress-step.active {
          opacity: 1;
          background: rgba(13, 110, 253, 0.1);
        }
        .progress-step i {
          font-size: 1.2rem;
        }
        .progress-step.active i.bi-check-circle-fill {
          color: #28a745;
        }
        .progress-step.active i.bi-arrow-repeat {
          color: #0d6efd;
        }
        .progress-step span {
          font-size: 0.85rem;
        }
        .spin {
          animation: spin 1s linear infinite;
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
      `}</style>
    </div>
  );
};
