/**
 * ModeSwitcher Component - Switch between Work and Manage modes
 *
 * Note: Only visible for admin users. Non-admin users are restricted to Work mode.
 */

import React from 'react';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/utils';
import { useAppMode, useLanguage } from '@/store';
import { useAppStore } from '@/store';
import { useAuth } from '@/hooks';
import { t } from '@/i18n';
import type { AppMode } from '@/types';

interface ModeSwitcherProps {
  className?: string;
}

export const ModeSwitcher: React.FC<ModeSwitcherProps> = ({ className }) => {
  const appMode = useAppMode();
  const language = useLanguage();
  const navigate = useNavigate();
  const { user } = useAuth();

  const isAdmin = user?.role === 'admin';

  // Don't render for non-admin users
  if (!isAdmin) {
    return null;
  }

  const handleModeChange = (mode: AppMode) => {
    useAppStore.getState().setAppMode(mode);

    // Navigate to the appropriate route based on mode
    if (mode === 'work') {
      navigate('/work');
    } else {
      navigate('/manage/dashboard');
    }
  };

  return (
    <div className={cn('mode-switcher', className)}>
      <button
        className={cn('mode-btn', appMode === 'work' && 'active')}
        onClick={() => handleModeChange('work')}
        title={t('workMode', language)}
      >
        <i className="bi bi-rocket" />
        <span className="mode-label">{t('workMode', language)}</span>
      </button>
      <button
        className={cn('mode-btn', appMode === 'manage' && 'active')}
        onClick={() => handleModeChange('manage')}
        title={t('manageMode', language)}
      >
        <i className="bi bi-bar-chart" />
        <span className="mode-label">{t('manageMode', language)}</span>
      </button>
    </div>
  );
};
