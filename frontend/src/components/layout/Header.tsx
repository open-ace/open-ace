/**
 * Header Component - Top navigation header
 */

import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/utils';
import { useAuth, useTheme, useLanguage } from '@/hooks';
import { useAppStore } from '@/store';
import { t, setLanguage as setI18nLanguage } from '@/i18n';
import { UserSettingsModal, Avatar } from '@/components/common';

interface HeaderProps {
  compact?: boolean;
}

export const Header: React.FC<HeaderProps> = ({ compact = false }) => {
  const { user, isAuthenticated, logout } = useAuth();
  const theme = useTheme();
  const language = useLanguage();
  const [showSettings, setShowSettings] = useState(false);

  const handleThemeToggle = () => {
    const newTheme = theme === 'light' ? 'dark' : 'light';
    useAppStore.getState().setTheme(newTheme);
  };

  const handleLanguageChange = (newLanguage: string) => {
    const lang = newLanguage as 'en' | 'zh' | 'ja' | 'ko';
    // 1. Update Zustand store
    useAppStore.getState().setLanguage(lang);
    // 2. Sync i18n module
    setI18nLanguage(lang);
    // 3. Sync i18next for Workspace iframe WebUI
    localStorage.setItem('i18nextLng', lang);
  };

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
            {user.avatar_url ? (
              <Avatar src={user.avatar_url} name={user.username} size="sm" shape="circle" />
            ) : (
              <i className="bi bi-person-circle fs-4 me-1" />
            )}
            <span className="d-none d-md-inline ms-1">{user.username}</span>
          </button>
          <ul className="dropdown-menu dropdown-menu-end">
            <li>
              <span className="dropdown-item-text text-muted">{user.email}</span>
            </li>
            <li>
              <hr className="dropdown-divider" />
            </li>
            <li>
              <button className="dropdown-item" onClick={() => setShowSettings(true)}>
                <i className="bi bi-gear me-2" />
                {t('settings', language)}
              </button>
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
        <Link to="/login" className="btn btn-primary btn-sm">
          {t('login', language)}
        </Link>
      )}
    </div>
  );

  // In compact mode (WorkLayout), just return the right content without wrapper
  if (compact) {
    return (
      <>
        {rightContent}
        <UserSettingsModal isOpen={showSettings} onClose={() => setShowSettings(false)} />
      </>
    );
  }

  // In normal mode (ManageLayout), return full header with left and right content
  return (
    <header className="header">
      {/* Left side - Hamburger */}
      <div className="d-flex align-items-center">
        <button
          className="hamburger-btn btn btn-link p-0 me-2"
          onClick={() => useAppStore.getState().toggleMobileSidebar()}
          aria-label="Toggle menu"
        >
          <i className="bi bi-list fs-4" />
        </button>
      </div>

      {/* Right side */}
      {rightContent}

      {/* User Settings Modal */}
      <UserSettingsModal isOpen={showSettings} onClose={() => setShowSettings(false)} />
    </header>
  );
};
