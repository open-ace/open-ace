/**
 * LocalDirectoryBrowser Component - Browse directories on the local server
 *
 * Features:
 * - Navigate local directory structure
 * - Create new directories
 * - Select directory as project path
 * - Path history (saves recent paths to localStorage with user/tenant scoping)
 *
 * Issue #1813: Added user/tenant scoping for path history and display-time validation
 */

import React, { useState, useEffect, useCallback, useMemo, useRef } from 'react';
import { useLanguage, useAppStore } from '@/store';
import { t } from '@/i18n';
import {
  fsApi,
  type DirectoryEntry,
  type FileEntry,
  type SearchEntry,
  MAX_UPLOAD_SIZE_MB,
} from '@/api/fs';
import { Loading, Button, EmptyState } from '@/components/common';
import { useToast, useConfirm } from '@/components/common';
import { downloadBlob, formatBytes } from '@/utils';

interface LocalDirectoryBrowserProps {
  initialPath?: string;
  onSelectPath: (path: string) => void;
  onClose?: () => void;
  listMaxHeight?: number | string;
  lockToRoot?: boolean; // Issue #1813: Disable up navigation and root button
  rootPath?: string; // Issue #1813: Locked root path for range checking
  hideManualInput?: boolean; // Issue #1813: Hide manual path input
  hideRecentPaths?: boolean; // Issue #1813: Hide recent paths history
  // Personal-files feature: enables the file list + upload/download/delete UI.
  // Defaults to false so the component's other callers
  // (LocalDirectoryBrowserModal → NewAutonomousModal) are unaffected.
  enableFileActions?: boolean;
}

const MAX_PATH_HISTORY = 5;
const PATH_HISTORY_VERSION = 1;
const PATH_HISTORY_KEY_PREFIX = 'local-path-history';

/**
 * Generate scoped localStorage key for path history.
 * Format: 'local-path-history:v{version}:{tenantId}:{userId}'
 * Issue #1813: Ensures user/tenant isolation
 */
function getPathHistoryKey(userId: string | null, tenantId: number | null): string {
  const tenant = tenantId ?? 'default';
  const user = userId ?? 'anonymous';
  return `${PATH_HISTORY_KEY_PREFIX}:v${PATH_HISTORY_VERSION}:${tenant}:${user}`;
}

function isWindowsPath(path: string): boolean {
  return /^[A-Za-z]:([\\/]|$)/.test(path);
}

export const LocalDirectoryBrowser: React.FC<LocalDirectoryBrowserProps> = ({
  initialPath,
  onSelectPath,
  onClose,
  listMaxHeight = 300,
  lockToRoot = false,
  rootPath,
  hideManualInput = false,
  hideRecentPaths = false,
  enableFileActions = false,
}) => {
  const language = useLanguage();
  const user = useAppStore((state) => state.user);
  const toast = useToast();

  // Extract user ID and tenant ID for localStorage scoping (Issue #1813)
  const userId = user?.id ?? null;
  const tenantId = user?.tenant_id ?? null;
  const scopedHistoryKey = useMemo(() => getPathHistoryKey(userId, tenantId), [userId, tenantId]);

  const [currentPath, setCurrentPath] = useState(initialPath ?? '');
  const [directories, setDirectories] = useState<DirectoryEntry[]>([]);
  const [files, setFiles] = useState<FileEntry[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parentPath, setParentPath] = useState<string | null>(null);
  const [isWritable, setIsWritable] = useState(false);
  const [showCreateInput, setShowCreateInput] = useState(false);
  const [newDirName, setNewDirName] = useState('');
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [downloadingPath, setDownloadingPath] = useState<string | null>(null);
  const [deletingPath, setDeletingPath] = useState<string | null>(null);
  const [isCreating, setIsCreating] = useState(false);
  const [pathHistory, setPathHistory] = useState<string[]>([]);
  const [fallbackNote, setFallbackNote] = useState<string | null>(null);

  // Issue #1923: recursive name search state. `searchQuery` is the input
  // value; `searchResults` is null when NOT searching (normal listing shown)
  // and an array (possibly empty) when a search has resolved.
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SearchEntry[] | null>(null);
  const [isSearching, setIsSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchTruncated, setSearchTruncated] = useState(false);
  const searchSeqRef = useRef(0);

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

  // Load path history from scoped localStorage key (Issue #1813)
  // Includes migration from old global key and display-time validation
  useEffect(() => {
    // Try to load from new scoped key
    const savedHistory = localStorage.getItem(scopedHistoryKey);
    if (savedHistory) {
      try {
        const parsed = JSON.parse(savedHistory);
        if (Array.isArray(parsed)) {
          // Display-time validation: filter paths based on lockToRoot and rootPath
          const validatedHistory =
            lockToRoot && rootPath ? parsed.filter((p: string) => p.startsWith(rootPath)) : parsed;
          setPathHistory(validatedHistory.slice(0, MAX_PATH_HISTORY));
        }
      } catch {
        // Ignore parse errors
      }
    } else {
      // Migration: Try to load from old global key and migrate to new key
      const oldHistory = localStorage.getItem(PATH_HISTORY_KEY_PREFIX);
      if (oldHistory) {
        try {
          const parsed = JSON.parse(oldHistory);
          if (Array.isArray(parsed) && parsed.length > 0) {
            // Migrate to new scoped key
            localStorage.setItem(scopedHistoryKey, JSON.stringify(parsed));
            // Clean up old key
            localStorage.removeItem(PATH_HISTORY_KEY_PREFIX);

            // Display-time validation
            const validatedHistory =
              lockToRoot && rootPath
                ? parsed.filter((p: string) => p.startsWith(rootPath))
                : parsed;
            setPathHistory(validatedHistory.slice(0, MAX_PATH_HISTORY));
          }
        } catch {
          // Ignore parse errors
        }
      }
    }
  }, [scopedHistoryKey, lockToRoot, rootPath]);

  const savePathToHistory = useCallback(
    (path: string) => {
      if (!path) return;

      // Display-time validation before saving
      if (lockToRoot && rootPath && !path.startsWith(rootPath)) {
        return; // Don't save paths outside locked range
      }

      const newHistory = [path, ...pathHistory.filter((p) => p !== path)].slice(
        0,
        MAX_PATH_HISTORY
      );
      setPathHistory(newHistory);
      localStorage.setItem(scopedHistoryKey, JSON.stringify(newHistory));
    },
    [pathHistory, scopedHistoryKey, lockToRoot, rootPath]
  );

  const fetchDirectories = useCallback(
    async (path?: string) => {
      setIsLoading(true);
      setError(null);
      setFallbackNote(null);
      try {
        const result = await fsApi.browseDirectory(path, {
          includeFiles: enableFileActions,
        });
        setDirectories(result.directories);
        setFiles(enableFileActions ? result.files : []);
        setCurrentPath(result.path);
        setParentPath(result.parent);
        setIsWritable(result.is_writable);
        if (result.fallback_note) {
          setFallbackNote(result.fallback_note);
        }
      } catch (err) {
        const msg = (err as Error)?.message || 'Failed to browse directory';
        setError(msg);
        // Issue #1912: Also surface the error as a toast so a failed
        // navigation into a subdirectory is not missed (the inline alert
        // can be overlooked when the list state visually does not change).
        toast.error(t('browseDirectory', language) || 'Browse Directory', msg);
      } finally {
        setIsLoading(false);
      }
    },
    [enableFileActions, toast, language]
  );

  useEffect(() => {
    void fetchDirectories(initialPath);
  }, [initialPath, fetchDirectories]);

  // Issue #1923: debounced recursive name search. Fires 300ms after the user
  // stops typing; an empty query exits search mode and restores the normal
  // listing. A monotonically increasing seq ref guards against stale responses
  // (slow query resolving after a faster one).
  useEffect(() => {
    const q = searchQuery.trim();
    if (!q) {
      // Empty query → exit search mode.
      searchSeqRef.current += 1;
      setSearchResults(null);
      setSearchError(null);
      setSearchTruncated(false);
      setIsSearching(false);
      return;
    }
    setIsSearching(true);
    setSearchError(null);
    const seq = ++searchSeqRef.current;
    const timer = setTimeout(() => {
      fsApi
        .searchFiles(q, currentPath)
        .then((result) => {
          if (seq !== searchSeqRef.current) return; // stale
          setSearchResults(result.results);
          setSearchTruncated(result.truncated);
        })
        .catch((err) => {
          if (seq !== searchSeqRef.current) return;
          setSearchError((err as Error)?.message ?? 'Search failed');
          setSearchResults([]);
          setSearchTruncated(false);
        })
        .finally(() => {
          if (seq !== searchSeqRef.current) return;
          setIsSearching(false);
        });
    }, 300);
    return () => clearTimeout(timer);
  }, [searchQuery, currentPath]);

  const isSearchMode = searchResults !== null;

  // Navigate to a search-result directory: fetch it and exit search mode so
  // the user lands in a normal listing of that directory.
  const handleNavigateToSearchResult = (entry: SearchEntry) => {
    setSearchQuery('');
    setSearchResults(null);
    setSearchTruncated(false);
    void fetchDirectories(entry.path);
  };

  const handleNavigate = (dir: DirectoryEntry) => {
    void fetchDirectories(dir.path);
  };

  // Issue #1813: Breadcrumb click handler with range checking
  const handleBreadcrumbClick = (path: string) => {
    // If lockToRoot is enabled and rootPath exists, check if target path is within range
    if (lockToRoot && rootPath) {
      if (!path.startsWith(rootPath) && path !== rootPath) {
        // Prevent navigation outside locked range
        console.warn(`Navigation blocked: path ${path} is outside locked range ${rootPath}`);
        return;
      }
    }
    // Issue #1923: exit search mode when the user navigates via breadcrumbs.
    setSearchQuery('');
    void fetchDirectories(path);
  };

  const handleNavigateUp = () => {
    // Issue #1813: If lockToRoot is enabled, don't allow navigating up
    if (lockToRoot) {
      return;
    }
    if (parentPath) {
      // Issue #1923: exit search mode when navigating up.
      setSearchQuery('');
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

  // ---- File actions (only wired when enableFileActions is set) ----

  const handleUploadClick = () => {
    fileInputRef.current?.click();
  };

  const handleFileSelected = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = e.target.files;
    if (!selected || selected.length === 0) return;

    // Reset input so selecting the same file again fires onChange.
    e.target.value = '';

    if (!isWritable) {
      toast.error(
        t('uploadFailed', language) || 'Upload failed',
        t('notWritable', language) || 'Directory is not writable'
      );
      return;
    }

    for (const file of Array.from(selected)) {
      if (file.size > MAX_UPLOAD_SIZE_MB * 1024 * 1024) {
        toast.error(
          t('uploadTooLarge', language) || 'File too large',
          (t('uploadTooLargeDesc', language, { size: String(MAX_UPLOAD_SIZE_MB) }) as string) ||
            `Maximum size is ${MAX_UPLOAD_SIZE_MB}MB`
        );
        continue;
      }

      setIsUploading(true);
      try {
        const result = await fsApi.uploadFile(file, currentPath);
        if (result.success) {
          toast.success(t('uploadSuccess', language) || 'File uploaded', file.name);
        } else {
          toast.error(t('uploadFailed', language) || 'Upload failed', result.error ?? file.name);
        }
      } catch (err) {
        toast.error(
          t('uploadFailed', language) || 'Upload failed',
          (err as Error)?.message ?? file.name
        );
      } finally {
        setIsUploading(false);
      }
    }

    // Refresh the listing so the uploaded file appears.
    await fetchDirectories(currentPath);
  };

  const handleDownload = async (file: FileEntry) => {
    // Guard: don't issue a request that DAC will reject anyway. The backend
    // already reports per-file readability in is_readable (Issue #1902).
    if (!file.is_readable) {
      toast.error(
        t('downloadFailed', language) || 'Download failed',
        t('fileNotReadable', language) || 'You do not have permission to read this file'
      );
      return;
    }
    setDownloadingPath(file.path);
    try {
      const blob = await fsApi.downloadFile(file.path);
      downloadBlob(blob, file.name);
    } catch (err) {
      const msg = (err as Error)?.message ?? '';
      // P2 (Issue #1902): surface a friendlier message for 403 / permission
      // errors instead of raw backend text.
      const friendly = /403|permission|forbidden/i.test(msg)
        ? t('fileNotReadable', language) || 'You do not have permission to read this file'
        : msg || file.name;
      toast.error(t('downloadFailed', language) || 'Download failed', friendly);
    } finally {
      setDownloadingPath(null);
    }
  };

  // Issue #1917: use the app's rendered confirm modal instead of the native
  // `window.confirm()`. The native dialog can be silently suppressed in
  // sandboxed/iframe contexts (resolving without ever showing a UI), which
  // previously caused files to be deleted immediately on click with no
  // confirmation. `useConfirm()` is the project-wide standard for destructive
  // actions and is already used by Prompts/Sessions/UserManagement/etc.
  const confirm = useConfirm();

  const handleDelete = async (file: FileEntry) => {
    // The delete button is only shown when the parent directory is writable,
    // but we still gate on is_readable: an unreadable file (e.g. root-owned
    // 0600 config dropped into the user's home) cannot be stat'd as the owner
    // and would fail with a confusing "Not a file" (Issue #1902).
    if (!file.is_readable) {
      toast.error(
        t('deleteFailed', language) || 'Delete failed',
        t('fileNotReadable', language) || 'You do not have permission to access this file'
      );
      return;
    }
    const ok = await confirm({
      title: file.name,
      message: (t('confirmDeleteFile', language) as string) || `Delete "${file.name}"?`,
      confirmText: (t('confirm', language) as string) || 'Confirm',
      cancelText: (t('cancel', language) as string) || 'Cancel',
      variant: 'danger',
    });
    if (!ok) return;

    setDeletingPath(file.path);
    try {
      const result = await fsApi.deleteFile(file.path);
      if (result.success) {
        toast.success(t('deleteSuccess', language) || 'File deleted', file.name);
        await fetchDirectories(currentPath);
      } else {
        const errText = result.error ?? '';
        const friendly = /403|permission|forbidden/i.test(errText)
          ? t('fileNotReadable', language) || 'You do not have permission to delete this file'
          : errText || file.name;
        toast.error(t('deleteFailed', language) || 'Delete failed', friendly);
      }
    } catch (err) {
      const msg = (err as Error)?.message ?? '';
      const friendly = /403|permission|forbidden/i.test(msg)
        ? t('fileNotReadable', language) || 'You do not have permission to delete this file'
        : msg || file.name;
      toast.error(t('deleteFailed', language) || 'Delete failed', friendly);
    } finally {
      setDeletingPath(null);
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
      {/* Issue #1813: Hide recent paths when hideRecentPaths is true */}
      {pathHistory.length > 0 && !hideRecentPaths && (
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
          {/* Issue #1813: Hide root "/" button when lockToRoot is true */}
          {!isWindows && !lockToRoot && (
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
                onClick={() => handleBreadcrumbClick(crumb.path)}
              >
                {crumb.name}
              </button>
            </React.Fragment>
          ))}
        </div>
      </div>

      {/* Issue #1923: recursive name search (personal files only). Sits on its
          own row so it doesn't crowd the Up / New Folder / Upload buttons. The
          search root is the current directory; the backend enforces the
          home-subtree lock so users can't search outside their own home. */}
      {enableFileActions && (
        <div className="mb-2">
          <div className="input-group input-group-sm">
            <span className="input-group-text bg-transparent">
              <i className={`bi ${isSearching ? 'bi-arrow-repeat' : 'bi-search'}`} />
            </span>
            <input
              type="text"
              className="form-control"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={t('searchFilesPlaceholder', language) || 'Search files and folders…'}
              aria-label={t('searchFilesPlaceholder', language) || 'Search files and folders…'}
            />
            {searchQuery && (
              <button
                type="button"
                className="btn btn-outline-secondary"
                onClick={() => setSearchQuery('')}
                title={t('clearSearch', language) || 'Clear search'}
              >
                <i className="bi bi-x-lg" />
              </button>
            )}
          </div>
        </div>
      )}

      <div className="mb-2 d-flex gap-2">
        {/* Issue #1813: Hide Up button when lockToRoot is true */}
        {parentPath && !lockToRoot && (
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
        {enableFileActions && isWritable && (
          <Button
            variant="outline-primary"
            size="sm"
            onClick={handleUploadClick}
            disabled={isUploading}
          >
            <i className={`bi ${isUploading ? 'bi-arrow-repeat' : 'bi-upload'} me-1`} />
            {isUploading
              ? t('uploading', language) || 'Uploading…'
              : t('uploadFile', language) || 'Upload'}
          </Button>
        )}
      </div>

      {/* Hidden file input driven by the Upload button (enableFileActions only) */}
      {enableFileActions && (
        <input
          ref={fileInputRef}
          type="file"
          multiple
          className="d-none"
          onChange={(e) => void handleFileSelected(e)}
        />
      )}

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

      <div className="directory-list" style={{ maxHeight: listMaxHeight, overflow: 'auto' }}>
        {isSearchMode ? (
          isSearching ? (
            <Loading size="sm" text={t('loading', language)} />
          ) : searchError ? (
            <div className="alert alert-danger small mb-2">
              <i className="bi bi-exclamation-triangle me-1" />
              {searchError}
            </div>
          ) : searchResults.length === 0 ? (
            <EmptyState
              icon="bi-search"
              title={t('searchNoResults', language) || 'No matching files or folders'}
              description={t('searchFilesPlaceholder', language) || 'Search files and folders…'}
            />
          ) : (
            <>
              {searchTruncated && (
                <div className="alert alert-info small mb-2">
                  <i className="bi bi-info-circle me-1" />
                  {
                    t('searchTruncated', language, {
                      count: String(searchResults.length),
                    }) as string
                  }
                </div>
              )}
              <div className="small text-muted mb-1">
                {
                  t('searchResultsCount', language, {
                    count: String(searchResults.length),
                  }) as string
                }
              </div>
              <ul className="list-group">
                {searchResults.map((entry) => (
                  <li
                    key={`${entry.type}-${entry.path}`}
                    className={`list-group-item list-group-item-action d-flex justify-content-between align-items-center ${
                      entry.type === 'dir' ? 'search-result-dir' : 'search-result-file'
                    }`}
                    onClick={
                      entry.type === 'dir' ? () => handleNavigateToSearchResult(entry) : undefined
                    }
                    style={{ cursor: entry.type === 'dir' ? 'pointer' : 'default' }}
                    title={entry.relative_path}
                  >
                    <div
                      className="text-truncate d-flex align-items-center"
                      style={{ minWidth: 0 }}
                    >
                      <i
                        className={`bi ${entry.type === 'dir' ? 'bi-folder' : 'bi-file-earmark'} me-2`}
                      />
                      <span className="text-truncate">{entry.name}</span>
                      {entry.type === 'file' && typeof entry.size === 'number' && (
                        <span className="badge text-muted fw-normal ms-2 small">
                          {formatBytes(entry.size)}
                        </span>
                      )}
                    </div>
                    <span
                      className="text-muted small ms-2 text-truncate"
                      style={{ maxWidth: '45%' }}
                    >
                      {entry.relative_path}
                    </span>
                  </li>
                ))}
              </ul>
            </>
          )
        ) : isLoading ? (
          <Loading size="sm" text={t('loading', language)} />
        ) : directories.length === 0 && (!enableFileActions || files.length === 0) ? (
          <EmptyState
            icon="bi-folder"
            title={t('emptyDirectory', language) || 'Empty Directory'}
            description={
              enableFileActions
                ? t('noFiles', language) || 'No files or subdirectories'
                : t('noSubdirectories', language) || 'No subdirectories found'
            }
          />
        ) : (
          <>
            <ul className="list-group">
              {directories.map((dir) => (
                <li
                  key={dir.path}
                  className="list-group-item list-group-item-action d-flex justify-content-between align-items-center"
                  onClick={() => handleNavigate(dir)}
                  onDoubleClick={() => handleNavigate(dir)}
                  title={t('doubleClickToOpen', language) || 'Double-click to open'}
                  style={{ cursor: 'pointer' }}
                >
                  <div className="d-flex align-items-center text-truncate">
                    <i
                      className="bi bi-folder-fill me-2"
                      role="img"
                      aria-label={t('folder', language) || 'Folder'}
                    />
                    <span className="text-truncate">{dir.name}</span>
                  </div>
                  <div className="d-flex align-items-center gap-1 flex-shrink-0">
                    {dir.is_writable && (
                      <span className="badge bg-success-subtle text-success small">
                        <i className="bi bi-pencil me-1" />
                        {t('writable', language) || 'Writable'}
                      </span>
                    )}
                    {/* Issue #1912: visual affordance that the row enters a subdirectory */}
                    <i className="bi bi-chevron-right text-muted small" aria-hidden="true" />
                  </div>
                </li>
              ))}
            </ul>
            {enableFileActions && files.length > 0 && (
              <ul className="list-group file-list">
                {files.map((file) => (
                  <li
                    key={file.path}
                    className="list-group-item file-list-item d-flex justify-content-between align-items-center"
                  >
                    <div className="text-truncate">
                      <i
                        className="bi bi-file-earmark me-2"
                        role="img"
                        aria-label={t('file', language) || 'File'}
                      />
                      <span className="text-truncate">{file.name}</span>
                      <span className="badge text-muted fw-normal ms-2 small">
                        {formatBytes(file.size)}
                      </span>
                    </div>
                    <div className="file-actions btn-group">
                      <button
                        type="button"
                        className="btn btn-sm btn-outline-secondary"
                        title={
                          file.is_readable
                            ? t('downloadFile', language) || 'Download'
                            : t('fileNotReadable', language) || 'No read permission'
                        }
                        onClick={() => void handleDownload(file)}
                        disabled={downloadingPath === file.path || !file.is_readable}
                      >
                        <i
                          className={`bi ${
                            downloadingPath === file.path ? 'bi-arrow-repeat' : 'bi-download'
                          }`}
                        />
                      </button>
                      {isWritable && (
                        <button
                          type="button"
                          className="btn btn-sm btn-outline-danger"
                          title={
                            file.is_readable
                              ? t('deleteFile', language) || 'Delete'
                              : t('fileNotReadable', language) || 'No access permission'
                          }
                          onClick={() => void handleDelete(file)}
                          disabled={deletingPath === file.path || !file.is_readable}
                        >
                          <i
                            className={`bi ${
                              deletingPath === file.path ? 'bi-arrow-repeat' : 'bi-trash'
                            }`}
                          />
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      {/* Issue #1813: Hide manual input when hideManualInput is true */}
      {!hideManualInput && (
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
      )}

      {/* Issue #1813: Always show Select button when manual input is hidden */}
      {/* Issue #1912: Rename to "Open Session Here" + show the path that will be used, */}
      {/* so the button's behaviour matches its label (it opens a new session in currentPath). */}
      {hideManualInput && (
        <div className="mt-3 d-flex flex-column gap-2 align-items-start">
          {currentPath && (
            <span className="small text-muted text-break">
              <i className="bi bi-folder2-open me-1" aria-hidden="true" />
              {t('willUsePath', language, { path: currentPath }) || `Will use: ${currentPath}`}
            </span>
          )}
          <Button variant="primary" onClick={handleSelect}>
            <i className="bi bi-folder-plus me-1" />
            {t('openSessionHere', language) || 'Open Session Here'}
          </Button>
        </div>
      )}
    </div>
  );
};
