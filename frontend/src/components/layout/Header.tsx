/**
 * Header Component - Top navigation header
 */

import React from 'react';
import { cn } from '@/utils';
import { useAuth, useTheme, useLanguage, useGlobalFetch, useAppMode } from '@/hooks';
import { useAppStore } from '@/store';
import { t } from '@/i18n';
import { Button } from '@/components/common';

interface HeaderProps {
  compact?: boolean;
}

export const Header: React.FC<HeaderProps> = ({ compact = false }) => {
  const { user, isAuthenticated, logout } = useAuth();
  const theme = useTheme();
  const language = useLanguage();
  const appMode = useAppMode();
  const { status, autoRefresh, setAutoRefresh, refreshAll } = useGlobalFetch();

  const handleThemeToggle = () => {
    const newTheme = theme === 'light' ? 'dark' : 'light';
    useAppStore.getState().setTheme(newTheme);
  };

  const handleLanguageChange = (newLanguage: string) => {
    useAppStore.getState().setLanguage(newLanguage as 'en' | 'zh' | 'ja' | 'ko');
  };

  const handleRefresh = async () => {
    await refreshAll();
  };

  // Only show refresh controls in manage mode
  const showRefreshControls = appMode === 'manage';

  // Content for right side (language, theme, user menu)
  const rightContent = (
    <div className="d-flex align-items-center gap-2">
      {/* Language selector */}
      <div className="dropdown">
        <button
          className="btn btn-link header-icon-btn p-0 dropdown-toggle"
          type="button"
          data-bs-toggle="dropdown"
          aria-expanded="false"
        >
          <i className="bi bi-globe" />
        </button>
        <ul className="dropdown-menu dropdown-menu-end">
          <li>
            <button
              className={cn('dropdown-item', language === 'en' && 'active')}
              onClick={() => handleLanguageChange('en')}
            >
              {t('english', language)}
            </button>
          </li>
          <li>
            <button
              className={cn('dropdown-item', language === 'zh' && 'active')}
              onClick={() => handleLanguageChange('zh')}
            >
              {t('chinese', language)}
            </button>
          </li>
          <li>
            <button
              className={cn('dropdown-item', language === 'ja' && 'active')}
              onClick={() => handleLanguageChange('ja')}
            >
              {t('japanese', language)}
            </button>
          </li>
          <li>
            <button
              className={cn('dropdown-item', language === 'ko' && 'active')}
              onClick={() => handleLanguageChange('ko')}
            >
              {t('korean', language)}
            </button>
          </li>
        </ul>
      </div>

      {/* Theme toggle */}
      <button
        className="btn btn-link header-icon-btn p-0"
        onClick={handleThemeToggle}
        title={t('toggleTheme', language)}
      >
        <i className={cn('bi', theme === 'light' ? 'bi-moon' : 'bi-sun')} />
      </button>

      {/* User menu */}
      {isAuthenticated && user ? (
        <div className="dropdown">
          <button
            className="btn btn-link header-icon-btn p-0 dropdown-toggle d-flex align-items-center"
            type="button"
            data-bs-toggle="dropdown"
            aria-expanded="false"
          >
            <i className="bi bi-person-circle fs-4 me-1" />
            <span className="d-none d-md-inline">{user.username}</span>
          </button>
          <ul className="dropdown-menu dropdown-menu-end">
            <li>
              <span className="dropdown-item-text text-muted">{user.email}</span>
            </li>
            <li>
              <hr className="dropdown-divider" />
            </li>
            <li>
              <button className="dropdown-item" onClick={logout}>
                <i className="bi bi-box-arrow-right me-2" />
                {t('logout', language)}
              </button>
            </li>
          </ul>
        </div>
      ) : (
        <Button variant="primary" size="sm" onClick={() => (window.location.href = '/login')}>
          {t('login', language)}
        </Button>
      )}
    </div>
  );

  // In compact mode (WorkLayout), just return the right content without wrapper
  if (compact) {
    return rightContent;
  }

  // In normal mode (ManageLayout), return full header with left and right content
  return (
    <header className="header">
      {/* Left side - Refresh controls (only in manage mode) */}
      <div className="d-flex align-items-center">
        {showRefreshControls && (
          <>
            {/* Global Auto-refresh toggle */}
            <div className="form-check form-switch d-flex align-items-center mb-0 me-2">
              <input
                className="form-check-input"
                type="checkbox"
                id="globalAutoRefresh"
                checked={autoRefresh}
                onChange={(e) => setAutoRefresh(e.target.checked)}
              />
              <label className="form-check-label small text-muted ms-1" htmlFor="globalAutoRefresh">
                {t('autoRefresh', language)}
              </label>
            </div>

            {/* Global Refresh button */}
            <button
              className="btn btn-outline-primary btn-sm"
              onClick={handleRefresh}
              disabled={status.is_running}
              title={t('refresh', language)}
            >
              {status.is_running ? (
                <>
                  <span
                    className="spinner-border spinner-border-sm me-1"
                    role="status"
                    aria-hidden="true"
                  />
                  {t('refreshing', language) || 'Refreshing...'}
                </>
              ) : (
                <>
                  <i className="bi bi-arrow-clockwise me-1" />
                  {t('refresh', language)}
                </>
              )}
            </button>
          </>
        )}
      </div>

      {/* Right side */}
      {rightContent}
    </header>
  );
};
