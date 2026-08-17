/**
 * ModeSwitcher Component - Switch between Work and Manage modes
 *
 * Note: Only visible for admin and manager users.
 * Non-admin/non-manager users are restricted to Work mode.
 */

import React from 'react';
import { Link } from 'react-router-dom';
import { cn } from '@/utils';
import { useAppMode, useLanguage } from '@/store';
import { useAppStore } from '@/store';
import { useAuth } from '@/hooks';
import { t } from '@/i18n';
import type { AppMode } from '@/types';
import { canAccessManageMode } from '@/utils/permissions';

interface ModeSwitcherProps {
  className?: string;
}

export const ModeSwitcher: React.FC<ModeSwitcherProps> = ({ className }) => {
  const appMode = useAppMode();
  const language = useLanguage();
  const { user } = useAuth();

  const userCanAccessManage = canAccessManageMode(user);

  // Don't render for users who can't access manage mode
  if (!userCanAccessManage) {
    return null;
  }

  const handleModeClick = (mode: AppMode) => {
    useAppStore.getState().setAppMode(mode);
  };

  return (
    <div className={cn('mode-switcher', className)}>
      <Link
        to="/work"
        className={cn('mode-btn', appMode === 'work' && 'active')}
        onClick={() => handleModeClick('work')}
        title={t('workMode', language)}
      >
        <i className="bi bi-rocket" />
        <span className="mode-label">{t('workMode', language)}</span>
      </Link>
      <Link
        to="/manage/dashboard"
        className={cn('mode-btn', appMode === 'manage' && 'active')}
        onClick={() => handleModeClick('manage')}
        title={t('manageMode', language)}
      >
        <i className="bi bi-bar-chart" />
        <span className="mode-label">{t('manageMode', language)}</span>
      </Link>
    </div>
  );
};
