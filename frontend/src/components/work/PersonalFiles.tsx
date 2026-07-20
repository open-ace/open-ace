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
import { sessionsApi } from '@/api/sessions';
import { Loading, Button } from '@/components/common';

/**
 * Tool name used for local workspace sessions.
 *
 * The local workspace always uses qwen-code (see
 * app/routes/workspace.py `get_session_models` which hardcodes
 * tool_name="qwen-code" for workspace_type="local"). Keeping this in one
 * place so PersonalFiles pre-creates sessions with the same tool the
 * embedded WebUI would pick.
 */
const LOCAL_TOOL_NAME = 'qwen-code';

export const PersonalFiles: React.FC = () => {
  const language = useLanguage();
  const navigate = useNavigate();

  // Issue #1813: State for home directory path (rootPath) and loading/error states
  const [homePath, setHomePath] = useState<string | null>(null);
  const [homePathError, setHomePathError] = useState<string | null>(null);
  const [isLoadingHome, setIsLoadingHome] = useState(true);

  // Issue #1924: Pre-creating a session before navigating so the embedded
  // qwen-code-webui iframe receives a sessionId and opens ChatPage directly
  // in the chosen directory, instead of falling back to the project picker
  // (which ignores encodedProjectName).
  const [isCreatingSession, setIsCreatingSession] = useState(false);
  const [createSessionError, setCreateSessionError] = useState<string | null>(null);

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
    async (path: string) => {
      setCreateSessionError(null);
      setIsCreatingSession(true);
      try {
        // Issue #1924: Pre-create a local session so the embedded WebUI
        // receives a sessionId and opens ChatPage directly in this directory.
        // Without a sessionId, qwen-code-webui's RootRedirect renders the
        // ProjectSelector, which ignores the encodedProjectName param and
        // strands the user in the picker.
        const resp = await sessionsApi.createSession({
          tool_name: LOCAL_TOOL_NAME,
          project_path: path,
        });
        const sessionId = resp.data?.session_id;
        if (!sessionId) {
          throw new Error(resp.error || 'Failed to create session');
        }

        const params = new URLSearchParams({
          newTab: 'true',
          workspaceType: 'local',
          projectPath: path,
          sessionId,
        });
        navigate(`/work?${params.toString()}`);
      } catch (err) {
        console.error('Failed to pre-create session for path:', path, err);
        setCreateSessionError(
          t('openSessionHereFailed', language) ||
            'Failed to open a session in this directory. Please try again.'
        );
      } finally {
        setIsCreatingSession(false);
      }
    },
    [navigate, language]
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
        {/* Issue #1924: Show error if session pre-creation failed; user stays
            on the page and can retry by clicking the button again. */}
        {createSessionError && (
          <div
            className="alert alert-danger d-flex align-items-center justify-content-between"
            role="alert"
          >
            <span>
              <i className="bi bi-exclamation-triangle me-2" />
              {createSessionError}
            </span>
            <Button
              variant="outline-danger"
              size="sm"
              onClick={() => setCreateSessionError(null)}
            >
              {t('dismiss', language) || 'Dismiss'}
            </Button>
          </div>
        )}
        {/* Issue #1813: Lock to home directory subtree */}
        <LocalDirectoryBrowser
          initialPath="home"
          onSelectPath={handleSelectPath}
          listMaxHeight="min(56vh, 620px)"
          lockToRoot={true}
          rootPath={homePath ?? undefined}
          hideManualInput={true}
          hideRecentPaths={true}
          enableFileActions={true}
        />
        {/* Issue #1924: Loading overlay while pre-creating the session so the
            user gets feedback and can't trigger duplicate navigations. */}
        {isCreatingSession && (
          <div
            className="position-absolute top-0 start-0 w-100 h-100 d-flex align-items-center justify-content-center bg-white bg-opacity-75"
            style={{ zIndex: 10 }}
          >
            <Loading size="sm" text={t('openingSession', language) || 'Opening session…'} />
          </div>
        )}
      </div>
    </div>
  );
};
