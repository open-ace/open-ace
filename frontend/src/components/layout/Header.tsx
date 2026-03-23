/**
 * Header Component - Top navigation header
 */

import React from 'react';
import { cn } from '@/utils';
import { useAuth, useTheme, useLanguage } from '@/hooks';
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

  const handleThemeToggle = () => {
    const newTheme = theme === 'light' ? 'dark' : 'light';
    useAppStore.getState().setTheme(newTheme);
  };

  const handleLanguageChange = (newLanguage: string) => {
    useAppStore.getState().setLanguage(newLanguage as 'en' | 'zh' | 'ja' | 'ko');
  };

  return (
    <header className={cn('header', compact && 'header-compact')}>
      {/* Left side - empty for now, can be used for breadcrumbs or other content */}
      <div className="d-flex align-items-center" />

      {/* Right side */}
      <div className="d-flex align-items-center gap-2">
        {/* Language selector */}
        <div className="dropdown">
          <button
            className="btn btn-link text-dark p-0 dropdown-toggle"
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
          className="btn btn-link text-dark p-0"
          onClick={handleThemeToggle}
          title={t('toggleTheme', language)}
        >
          <i className={cn('bi', theme === 'light' ? 'bi-moon' : 'bi-sun')} />
        </button>

        {/* User menu */}
        {isAuthenticated && user ? (
          <div className="dropdown">
            <button
              className="btn btn-link text-dark p-0 dropdown-toggle d-flex align-items-center"
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
    </header>
  );
};
