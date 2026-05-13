/**
 * UserSettingsModal - User personal settings dialog
 */

import React, { useState, useCallback } from 'react';
import { Modal } from './Modal';
import { AvatarUploader } from './AvatarUploader';
import { useLanguage, useAppStore, useUser } from '@/store';
import { useAutoFullscreenOnEnterChat, useEnableTabNotifications } from '@/store';
import { authApi } from '@/api/auth';
import { t } from '@/i18n';
import { useToast } from './Toast';

interface UserSettingsModalProps {
  isOpen: boolean;
  onClose: () => void;
}

export const UserSettingsModal: React.FC<UserSettingsModalProps> = ({ isOpen, onClose }) => {
  const language = useLanguage();
  const user = useUser();
  const toast = useToast();
  const autoFullscreenOnEnterChat = useAutoFullscreenOnEnterChat();
  const enableTabNotifications = useEnableTabNotifications();
  const { toggleAutoFullscreenOnEnterChat, toggleTabNotifications, setUser } = useAppStore();
  const [uploading, setUploading] = useState(false);

  const handleUpload = useCallback(
    async (file: File) => {
      setUploading(true);
      try {
        const result = await authApi.uploadAvatar(file);
        if (result.success && result.avatar_url) {
          // Update user in store
          if (user) {
            setUser({ ...user, avatar_url: result.avatar_url });
          }
          toast.success(t('avatarUploadSuccess', language));
        } else {
          toast.error(t('avatarUploadFailed', language));
        }
      } catch {
        toast.error(t('avatarUploadFailed', language));
      } finally {
        setUploading(false);
      }
    },
    [user, setUser, toast, language]
  );

  const handleDelete = useCallback(async () => {
    setUploading(true);
    try {
      const result = await authApi.deleteAvatar();
      if (result.success) {
        // Update user in store
        if (user) {
          const { avatar_url, ...rest } = user;
          setUser(rest as typeof user);
        }
        toast.success(t('avatarDeleteSuccess', language));
      } else {
        toast.error(t('avatarDeleteFailed', language));
      }
    } catch {
      toast.error(t('avatarDeleteFailed', language));
    } finally {
      setUploading(false);
    }
  }, [user, setUser, toast, language]);

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('personalSettings', language)} size="md">
      <div className="user-settings">
        {/* Avatar Section */}
        <div className="mb-4">
          <h6 className="text-muted mb-3">
            <i className="bi bi-person-circle me-2" />
            {t('avatar', language)}
          </h6>

          <AvatarUploader
            currentAvatarUrl={user?.avatar_url}
            username={user?.username}
            onUpload={handleUpload}
            onDelete={handleDelete}
            uploading={uploading}
          />
        </div>

        {/* Divider */}
        <hr className="my-4" />

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
      </div>
    </Modal>
  );
};
