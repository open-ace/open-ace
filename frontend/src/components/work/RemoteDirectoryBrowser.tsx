/**
 * RemoteDirectoryBrowser Component - Browse directories on remote machine
 *
 * Features:
 * - Navigate directory structure on remote machine
 * - Create new directories
 * - Select directory as project path
 * - Path history (saves recent paths to localStorage)
 *
 * Issue #317: Remote workspace lacks project creation functionality
 */

import React, { useState, useEffect, useCallback, useMemo } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { fsApi, type BrowseResult, type DirectoryEntry } from '@/api/fs';
import { Loading, Button, EmptyState } from '@/components/common';

interface RemoteDirectoryBrowserProps {
  machineId: string;
  initialPath?: string;
  osType?: string; // Operating system type for cross-platform path handling
  onSelectPath: (path: string) => void;
  onClose?: () => void;
}

// Maximum number of paths to save in history
const MAX_PATH_HISTORY = 5;

// Local storage key for path history
const PATH_HISTORY_KEY = 'remote-path-history';

export const RemoteDirectoryBrowser: React.FC<RemoteDirectoryBrowserProps> = ({
  machineId,
  initialPath,
  osType,
  onSelectPath,
  onClose,
}) => {
  const language = useLanguage();

  // Detect if the remote machine is Windows
  const isWindows = Boolean(osType?.toLowerCase().includes('windows'));

  // State variables
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

  // Cross-platform path utilities
  const getPathSeparator = () => (isWindows ? '\\' : '/');

  const splitPath = useCallback(
    (path: string): string[] => {
      if (!path) return [];
      if (isWindows) {
        const parts = path.split(/[\\/]/).filter(Boolean);
        if (parts.length > 0 && parts[0].match(/^[A-Za-z]:$/)) {
          return parts;
        }
        return parts;
      }
      return path.split('/').filter(Boolean);
    },
    [isWindows]
  );

  // Load path history from localStorage
  useEffect(() => {
    const savedHistory = localStorage.getItem(`${PATH_HISTORY_KEY}-${machineId}`);
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
  }, [machineId]);

  // Save path to history
  const savePathToHistory = useCallback(
    (path: string) => {
      if (!path) return;
      const newHistory = [path, ...pathHistory.filter((p) => p !== path)].slice(
        0,
        MAX_PATH_HISTORY
      );
      setPathHistory(newHistory);
      localStorage.setItem(`${PATH_HISTORY_KEY}-${machineId}`, JSON.stringify(newHistory));
    },
    [machineId, pathHistory]
  );

  // Fetch directory listing
  const fetchDirectories = useCallback(
    async (path: string) => {
      setIsLoading(true);
      setError(null);
      setFallbackNote(null); // Clear previous fallback note
      try {
        const result = await fsApi.browseRemoteDirectory(machineId, path);
        if (result.success && result.result) {
          const browseResult: BrowseResult = result.result;
          setDirectories(browseResult.directories);
          setCurrentPath(browseResult.path);
          setParentPath(browseResult.parent);
          setIsWritable(browseResult.is_writable);
          if (browseResult.fallback_note) {
            setFallbackNote(browseResult.fallback_note);
          }
        } else {
          setError(result.error ?? 'Failed to browse directory');
        }
      } catch (err) {
        setError((err as Error)?.message || 'Failed to browse directory');
      } finally {
        setIsLoading(false);
      }
    },
    [machineId]
  );

  // Initial load
  useEffect(() => {
    if (initialPath) {
      fetchDirectories(initialPath);
    } else {
      // Start from root or home
      fetchDirectories('');
    }
  }, [initialPath, fetchDirectories]);

  // Navigate to subdirectory
  const handleNavigate = (dir: DirectoryEntry) => {
    fetchDirectories(dir.path);
  };

  // Navigate to parent
  const handleNavigateUp = () => {
    if (parentPath) {
      fetchDirectories(parentPath);
    }
  };

  // Select current path as project path
  const handleSelect = () => {
    savePathToHistory(currentPath);
    onSelectPath(currentPath);
    if (onClose) onClose();
  };

  // Create new directory
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
      const result = await fsApi.createRemoteDirectory(machineId, fullPath);
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

  // Select path from history
  const handleSelectFromHistory = (path: string) => {
    fetchDirectories(path);
  };

  // Get display name for path (cross-platform)
  const getDisplayPath = (path: string) => {
    if (!path) return isWindows ? 'C:\\' : '/';
    const parts = splitPath(path);
    if (parts.length === 0) return isWindows ? 'C:\\' : '/';
    // For Windows drive like "C:", return "C:"
    if (parts.length === 1 && parts[0].match(/^[A-Za-z]:$/)) {
      return parts[0];
    }
    return parts[parts.length - 1];
  };

  // Get path breadcrumbs (cross-platform)
  const breadcrumbs = useMemo(() => {
    if (!currentPath) return [];
    const parts = splitPath(currentPath);
    const crumbs: { name: string; path: string }[] = [];

    if (isWindows) {
      // Windows: build paths with backslash
      let accumulatedPath = '';
      for (let i = 0; i < parts.length; i++) {
        const part = parts[i];
        if (i === 0 && part.match(/^[A-Za-z]:$/)) {
          // Drive letter - skip adding to crumbs since root button already shows it
          accumulatedPath = part + '\\';
        } else {
          accumulatedPath = accumulatedPath + (accumulatedPath.endsWith('\\') ? '' : '\\') + part;
          crumbs.push({ name: part, path: accumulatedPath });
        }
      }
    } else {
      // Unix: build paths with forward slash
      let accumulatedPath = '';
      for (const part of parts) {
        accumulatedPath += '/' + part;
        crumbs.push({ name: part, path: accumulatedPath });
      }
    }
    return crumbs;
  }, [currentPath, isWindows]);

  return (
    <div className="remote-directory-browser">
      {/* Path History */}
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

      {/* Breadcrumbs */}
      <div className="mb-2">
        <div className="d-flex align-items-center gap-1 small">
          {!isWindows && (
            <button className="btn btn-link btn-sm p-0" onClick={() => fetchDirectories('/')}>
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
                onClick={() => fetchDirectories(crumb.path)}
              >
                {crumb.name}
              </button>
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Navigation buttons */}
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

      {/* Create directory input */}
      {showCreateInput && (
        <div className="mb-2">
          <div className="input-group input-group-sm">
            <input
              type="text"
              className="form-control"
              value={newDirName}
              onChange={(e) => setNewDirName(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') handleCreateDirectory();
              }}
              placeholder={t('folderName', language) || 'Folder name'}
            />
            <Button
              variant="primary"
              size="sm"
              onClick={handleCreateDirectory}
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

      {/* Error message */}
      {error && (
        <div className="alert alert-danger small mb-2">
          <i className="bi bi-exclamation-triangle me-1" />
          {error}
        </div>
      )}

      {/* Fallback note - show when path was changed */}
      {fallbackNote && (
        <div className="alert alert-info small mb-2">
          <i className="bi bi-info-circle me-1" />
          {fallbackNote}
        </div>
      )}

      {/* Directory listing */}
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

      {/* Current path display */}
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
