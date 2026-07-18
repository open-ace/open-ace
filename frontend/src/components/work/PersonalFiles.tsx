/**
 * PersonalFiles Component - direct entry for browsing the current user's home workspace.
 *
 * Issue #1813: Implements path locking to restrict navigation to home directory subtree.
 * - Async fetches user's actual home directory path
 * - Locks LocalDirectoryBrowser to prevent navigation outside home
 * - Displays error state if home directory fetch fails
 */

import React, { useCallback, useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { LocalDirectoryBrowser } from './LocalDirectoryBrowser';
import { fsApi } from '@/api/fs';
import { Loading, Button } from '@/components/common';

export const PersonalFiles: React.FC = () => {
  const language = useLanguage();
  const navigate = useNavigate();

  // Issue #1813: State for home directory path (rootPath) and loading/error states
  const [homePath, setHomePath] = useState<string | null>(null);
  const [homePathError, setHomePathError] = useState<string | null>(null);
  const [isLoadingHome, setIsLoadingHome] = useState(true);

  // Issue #1813: Async fetch home directory path with caching
  useEffect(() => {
    // Only fetch if not already loaded and no error
    if (homePath || homePathError) return;

    setIsLoadingHome(true);
    fsApi
      .browseDirectory('home')
      .then((result) => {
        setHomePath(result.path);
        setHomePathError(null);
      })
      .catch((err) => {
        console.error('Failed to load home directory:', err);
        setHomePathError(
          t('homeDirectoryLoadError', language) ||
            'Failed to load home directory. Please refresh to try again.'
        );
      })
      .finally(() => {
        setIsLoadingHome(false);
      });
  }, [homePath, homePathError, language]);

  const handleSelectPath = useCallback(
    (path: string) => {
      const params = new URLSearchParams({
        newTab: 'true',
        workspaceType: 'local',
        projectPath: path,
      });
      navigate(`/work?${params.toString()}`);
    },
    [navigate]
  );

  // Issue #1813: Loading state
  if (isLoadingHome) {
    return (
      <div className="personal-files">
        <div className="personal-files-header">
          <h1>{t('personalFiles', language)}</h1>
        </div>
        <div className="personal-files-browser">
          <Loading size="sm" text={t('loading', language)} />
        </div>
      </div>
    );
  }

  // Issue #1813: Error state with retry button
  if (homePathError) {
    return (
      <div className="personal-files">
        <div className="personal-files-header">
          <h1>{t('personalFiles', language)}</h1>
        </div>
        <div className="personal-files-browser">
          <div className="alert alert-danger">
            <i className="bi bi-exclamation-triangle me-2" />
            {homePathError}
          </div>
          <Button
            variant="primary"
            onClick={() => {
              setHomePathError(null);
              setHomePath(null);
            }}
          >
            <i className="bi bi-arrow-clockwise me-1" />
            {t('retry', language) || 'Refresh'}
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="personal-files">
      <div className="personal-files-header">
        <h1>{t('personalFiles', language)}</h1>
      </div>
      <div className="personal-files-browser">
        {/* Issue #1813: Lock to home directory subtree */}
        <LocalDirectoryBrowser
          initialPath="home"
          onSelectPath={handleSelectPath}
          listMaxHeight="min(56vh, 620px)"
          lockToRoot={true}
          rootPath={homePath ?? undefined}
          hideManualInput={true}
          hideRecentPaths={true}
        />
      </div>
    </div>
  );
};
