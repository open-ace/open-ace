/**
 * Workspace Component - AI workspace with iframe embedding and tab support
 *
 * Features:
 * - Multiple tabs for different conversations
 * - Each tab has its own iframe
 * - Support for creating new tabs via URL parameter
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useSearchParams } from 'react-router-dom';
import { workspaceApi } from '@/api';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Loading, Error } from '@/components/common';
import { cn } from '@/utils';

interface WorkspaceTab {
  id: string;
  title: string;
  url: string;
  createdAt: number;
}

// Generate unique tab ID
const generateTabId = () => `tab-${Date.now()}-${Math.random().toString(36).substr(2, 9)}`;

export const Workspace: React.FC = () => {
  const language = useLanguage();
  const [searchParams, setSearchParams] = useSearchParams();
  const [config, setConfig] = useState<{ enabled: boolean; url: string } | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tabs, setTabs] = useState<WorkspaceTab[]>([]);
  const [activeTabId, setActiveTabId] = useState<string>('');

  // Load workspace config
  useEffect(() => {
    const loadConfig = async () => {
      try {
        const workspaceConfig = await workspaceApi.getConfig();
        setConfig(workspaceConfig);
      } catch (err) {
        const error = err as Error;
        setError(error?.message || 'Failed to load workspace config');
      } finally {
        setIsLoading(false);
      }
    };

    loadConfig();
  }, []);

  // Initialize first tab when config is loaded
  useEffect(() => {
    if (config?.enabled && config.url && tabs.length === 0) {
      const initialTab: WorkspaceTab = {
        id: generateTabId(),
        title: t('newSession', language),
        url: config.url,
        createdAt: Date.now(),
      };
      setTabs([initialTab]);
      setActiveTabId(initialTab.id);
    }
  }, [config, tabs.length, language]);

  // Handle URL parameter for creating new tab
  useEffect(() => {
    const newTab = searchParams.get('newTab');
    if (newTab === 'true' && config?.enabled && config.url) {
      // Clear the URL parameter
      searchParams.delete('newTab');
      setSearchParams(searchParams, { replace: true });

      // Create new tab
      createNewTab();
    }
  }, [searchParams, config]);

  // Create a new tab
  const createNewTab = useCallback(() => {
    if (!config?.url) return;

    const newTab: WorkspaceTab = {
      id: generateTabId(),
      title: t('newSession', language),
      url: config.url,
      createdAt: Date.now(),
    };

    setTabs((prev) => [...prev, newTab]);
    setActiveTabId(newTab.id);
  }, [config, language]);

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
    <div className="workspace h-100 d-flex flex-column">
      {/* Page Header */}
      <div className="page-header mb-3 px-3 pt-3">
        <h2>{t('workspace', language)}</h2>
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
            <iframe
              src={tab.url}
              title={`Workspace - ${tab.title}`}
              className="w-100 h-100"
              style={{ border: 'none' }}
              allow="clipboard-read; clipboard-write"
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
      `}</style>
    </div>
  );
};
