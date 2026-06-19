/**
 * LocalDirectoryBrowser Component - Browse directories on the local server
 *
 * Features:
 * - Navigate local directory structure
 * - Create new directories
 * - Select directory as project path
 * - Path history (saves recent paths to localStorage)
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { fsApi, type DirectoryEntry } from '@/api/fs';
import { Loading, Button, EmptyState } from '@/components/common';

interface LocalDirectoryBrowserProps {
  initialPath?: string;
  onSelectPath: (path: string) => void;
  onClose?: () => void;
}

const MAX_PATH_HISTORY = 5;
const PATH_HISTORY_KEY = 'local-path-history';

function isWindowsPath(path: string): boolean {
  return /^[A-Za-z]:([\\/]|$)/.test(path);
}

export const LocalDirectoryBrowser: React.FC<LocalDirectoryBrowserProps> = ({
  initialPath,
  onSelectPath,
  onClose,
}) => {
  const language = useLanguage();

  const [currentPath, setCurrentPath] = useState(initialPath ?? '');
  const [directories, setDirectories] = useState<DirectoryEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [isWritable, setIsWritable] = useState(false);
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [newDirName, setNewDirName] = useState('');
  const [isCreating, setIsCreating] = useState(false);
  const [pathHistory, setPathHistory] = useState<string[]>([]);
  const [fallbackNote, setFallbackNote] = useState<string | null>(null);

  const isWindows = useMemo(() => {
    const pathForDetection = currentPath !== '' ? currentPath : (initialPath ?? '');
    return isWindowsPath(pathForDetection);
  }, [currentPath, initialPath]);

  const getPathSeparator = useCallback(() => (isWindows ? '\\' : '/'), [isWindows]);

  const splitPath = useCallback((path: string): string[] => {
    if (!path) return [];
    const parts = path.split(/[\\/]/).filter(Boolean);
    return parts;
  }, []);

  useEffect(() => {
    const savedHistory = localStorage.getItem(PATH_HISTORY_KEY);
    if (savedHistory) {
      try {
        const parsed = JSON.parse(savedHistory);
        if (Array.isArray(parsed)) {
          setPathHistory(parsed.slice(0, MAX_PATH_HISTORY));
        }
      } catch {
        // Ignore parse errors
      }
    }
  }, []);

  const savePathToHistory = useCallback(
    (path: string) => {
      if (!path) return;
      const newHistory = [path, ...pathHistory.filter((p) => p !== path)].slice(
        0,
        MAX_PATH_HISTORY
      );
      setPathHistory(newHistory);
      localStorage.setItem(PATH_HISTORY_KEY, JSON.stringify(newHistory));
    },
    [pathHistory]
  );

  const fetchDirectories = useCallback(async (path?: string) => {
    setIsLoading(true);
    setError(null);
    setFallbackNote(null);
    try {
      const result = await fsApi.browseDirectory(path);
      setDirectories(result.directories);
      setCurrentPath(result.path);
      setParentPath(result.parent);
      setIsWritable(result.is_writable);
      if (result.fallback_note) {
        setFallbackNote(result.fallback_note);
      }
    } catch (err) {
      setError((err as Error)?.message || 'Failed to browse directory');
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    void fetchDirectories(initialPath);
  }, [initialPath, fetchDirectories]);

  const handleNavigate = (dir: DirectoryEntry) => {
    void fetchDirectories(dir.path);
  };

  const handleNavigateUp = () => {
    if (parentPath) {
      void fetchDirectories(parentPath);
    }
  };

  const handleSelect = () => {
    savePathToHistory(currentPath);
    onSelectPath(currentPath);
    if (onClose) onClose();
  };

  const handleCreateDirectory = async () => {
    if (!newDirName.trim() || !isWritable || isCreating) return;

    const separator = getPathSeparator();
    const fullPath = currentPath
      ? currentPath + (currentPath.endsWith(separator) ? '' : separator) + newDirName.trim()
      : newDirName.trim();

    setIsCreating(true);
    setError(null);
    setShowCreateInput(false);

    try {
      const result = await fsApi.createDirectory({ path: fullPath });
      if (result.success) {
        setNewDirName('');
        await fetchDirectories(currentPath);
      } else {
        const fallback = t('createDirError', language) ?? 'Failed to create directory';
        setError(result.error ?? fallback);
      }
    } catch (err) {
      const fallback = t('createDirError', language) ?? 'Failed to create directory';
      setError((err as Error)?.message ?? fallback);
    } finally {
      setIsCreating(false);
    }
  };

  const handleSelectFromHistory = (path: string) => {
    void fetchDirectories(path);
  };

  const getDisplayPath = useCallback(
    (path: string) => {
      if (!path) return isWindows ? 'C:\\' : '/';
      const parts = splitPath(path);
      if (parts.length === 0) return isWindows ? 'C:\\' : '/';
      if (parts.length === 1 && parts[0].match(/^[A-Za-z]:$/)) {
        return parts[0];
      }
      return parts[parts.length - 1];
    },
    [isWindows, splitPath]
  );

  const breadcrumbs = useMemo(() => {
    if (!currentPath) return [];
    const parts = splitPath(currentPath);
    const crumbs: { name: string; path: string }[] = [];

    if (isWindows) {
      let accumulatedPath = '';
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (i === 0 && part.match(/^[A-Za-z]:$/)) {
          accumulatedPath = part + '\\';
        } else {
          accumulatedPath = accumulatedPath + (accumulatedPath.endsWith('\\') ? '' : '\\') + part;
          crumbs.push({ name: part, path: accumulatedPath });
        }
      }
    } else {
      let accumulatedPath = '';
      for (const part of parts) {
        accumulatedPath += '/' + part;
        crumbs.push({ name: part, path: accumulatedPath });
      }
    }

    return crumbs;
  }, [currentPath, isWindows, splitPath]);

  return (
    <div className="local-directory-browser">
      {pathHistory.length > 0 && (
        <div className="mb-2">
          <label className="form-label small text-muted">
            {t('recentPaths', language) || 'Recent Paths'}
          </label>
          <div className="d-flex gap-1 flex-wrap">
            {pathHistory.map((path) => (
              <button
                key={path}
                className="btn btn-sm btn-outline-secondary"
                onClick={() => handleSelectFromHistory(path)}
              >
                {getDisplayPath(path)}
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mb-2">
        <div className="d-flex align-items-center gap-1 small">
          {!isWindows && (
            <button className="btn btn-link btn-sm p-0" onClick={() => void fetchDirectories('/')}>
              /
            </button>
          )}
          {breadcrumbs.map((crumb, index) => (
            <React.Fragment key={crumb.path}>
              {index > 0 && <span className="text-muted">{getPathSeparator()}</span>}
              <button
                className={`btn btn-link btn-sm p-0 ${
                  index === breadcrumbs.length - 1 ? 'fw-bold' : ''
                }`}
                onClick={() => void fetchDirectories(crumb.path)}
              >
                {crumb.name}
              </button>
            </React.Fragment>
          ))}
        </div>
      </div>

      <div className="mb-2 d-flex gap-2">
        {parentPath && (
          <Button variant="outline-secondary" size="sm" onClick={handleNavigateUp}>
            <i className="bi bi-arrow-up-circle me-1" />
            {t('up', language) || 'Up'}
          </Button>
        )}
        {isWritable && (
          <Button
            variant="outline-primary"
            size="sm"
            onClick={() => setShowCreateInput(!showCreateInput)}
          >
            <i className="bi bi-folder-plus me-1" />
            {t('newFolder', language) || 'New Folder'}
          </Button>
        )}
      </div>

      {showCreateInput && (
        <div className="mb-2">
          <div className="input-group input-group-sm">
            <input
              type="text"
              className="form-control"
              value={newDirName}
              onChange={(e) => setNewDirName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') void handleCreateDirectory();
              }}
              placeholder={t('folderName', language) || 'Folder name'}
            />
            <Button
              variant="primary"
              size="sm"
              onClick={() => void handleCreateDirectory()}
              disabled={isCreating}
            >
              {t('create', language)}
            </Button>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => {
                setShowCreateInput(false);
                setNewDirName('');
              }}
            >
              {t('cancel', language)}
            </Button>
          </div>
        </div>
      )}

      {error && (
        <div className="alert alert-danger small mb-2">
          <i className="bi bi-exclamation-triangle me-1" />
          {error}
        </div>
      )}

      {fallbackNote && (
        <div className="alert alert-info small mb-2">
          <i className="bi bi-info-circle me-1" />
          {fallbackNote}
        </div>
      )}

      <div className="directory-list" style={{ maxHeight: '300px', overflow: 'auto' }}>
        {isLoading ? (
          <Loading size="sm" text={t('loading', language)} />
        ) : directories.length === 0 ? (
          <EmptyState
            icon="bi-folder"
            title={t('emptyDirectory', language) || 'Empty Directory'}
            description={t('noSubdirectories', language) || 'No subdirectories found'}
          />
        ) : (
          <ul className="list-group">
            {directories.map((dir) => (
              <li
                key={dir.path}
                className="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
                onClick={() => handleNavigate(dir)}
                style={{ cursor: 'pointer' }}
              >
                <div>
                  <i className="bi bi-folder me-2" />
                  <span>{dir.name}</span>
                </div>
                {dir.is_writable && (
                  <span className="badge bg-success-subtle text-success small">
                    <i className="bi bi-pencil me-1" />
                    {t('writable', language) || 'Writable'}
                  </span>
                )}
              </li>
            ))}
          </ul>
        )}
      </div>

      <div className="mt-3">
        <label className="form-label small text-muted">
          {t('currentPath', language) || 'Current Path'}
        </label>
        <div className="input-group">
          <input
            type="text"
            className="form-control"
            value={currentPath}
            onChange={(e) => setCurrentPath(e.target.value)}
            placeholder="/path/to/project"
          />
          <Button variant="primary" onClick={handleSelect}>
            <i className="bi bi-check-lg me-1" />
            {t('select', language) || 'Select'}
          </Button>
        </div>
      </div>
    </div>
  );
};
