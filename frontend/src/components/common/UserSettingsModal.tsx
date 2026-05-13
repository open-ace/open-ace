/**
 * UserSettingsModal Component - User personal settings dialog
 *
 * Features:
 * - Workspace settings (auto fullscreen, tab notifications)
 * - Extensible for future settings (profile, avatar, etc.)
 */

import React from 'react';
import { Modal } from './Modal';
import { useLanguage, useAppStore } from '@/store';
import { useAutoFullscreenOnEnterChat, useEnableTabNotifications } from '@/store';
import { t } from '@/i18n';

interface UserSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const UserSettingsModal: React.FC<UserSettingsModalProps> = ({ isOpen, onClose }) => {
  const language = useLanguage();
  const autoFullscreenOnEnterChat = useAutoFullscreenOnEnterChat();
  const enableTabNotifications = useEnableTabNotifications();
  const { toggleAutoFullscreenOnEnterChat, toggleTabNotifications } = useAppStore();

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('personalSettings', language)} size="md">
      <div className="user-settings">
        {/* Workspace Settings Section */}
        <div className="mb-4">
          <h6 className="text-muted mb-3">
            <i className="bi bi-window-desktop me-2" />
            {t('workspaceSettings', language)}
          </h6>

          {/* Auto fullscreen on enter chat */}
          <div className="form-check form-switch mb-3 d-flex align-items-start">
            <input
              className="form-check-input mt-1"
              type="checkbox"
              id="autoFullscreenOnEnterChat"
              checked={autoFullscreenOnEnterChat}
              onChange={toggleAutoFullscreenOnEnterChat}
            />
            <div className="ms-2">
              <label className="form-check-label fw-medium" htmlFor="autoFullscreenOnEnterChat">
                {t('autoFullscreenOnEnterChat', language)}
              </label>
              <p className="text-muted small mb-0 mt-1">
                {t('autoFullscreenOnEnterChatDesc', language)}
              </p>
            </div>
          </div>

          {/* Tab notifications */}
          <div className="form-check form-switch mb-3 d-flex align-items-start">
            <input
              className="form-check-input mt-1"
              type="checkbox"
              id="enableTabNotifications"
              checked={enableTabNotifications}
              onChange={toggleTabNotifications}
            />
            <div className="ms-2">
              <label className="form-check-label fw-medium" htmlFor="enableTabNotifications">
                {t('tabNotifications', language)}
              </label>
              <p className="text-muted small mb-0 mt-1">{t('tabNotificationsDesc', language)}</p>
            </div>
          </div>
        </div>

        {/* Divider */}
        <hr className="my-4" />

        {/* Placeholder for future settings */}
        <div className="text-muted small">
          <i className="bi bi-person me-2" />
          {t('moreSettingsComingSoon', language)}
        </div>
      </div>
    </Modal>
  );
};
