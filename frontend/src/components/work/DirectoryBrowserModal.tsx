/**
 * DirectoryBrowserModal Component - Modal wrapper for RemoteDirectoryBrowser
 *
 * Features:
 * - Opens RemoteDirectoryBrowser in a modal dialog
 * - Allows user to browse and select directories on remote machine
 * - Returns selected path via callback
 *
 * Issue #584: Remote workspace directory browser
 */

import React from 'react';
import { Modal } from '@/components/common';
import { RemoteDirectoryBrowser } from './RemoteDirectoryBrowser';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface DirectoryBrowserModalProps {
  isOpen: boolean;
  onClose: () => void;
  machineId: string;
  initialPath?: string;
  osType?: string; // Operating system type for cross-platform path handling
  onSelectPath: (path: string) => void;
}

export const DirectoryBrowserModal: React.FC<DirectoryBrowserModalProps> = ({
  isOpen,
  onClose,
  machineId,
  initialPath,
  osType,
  onSelectPath,
}) => {
  const language = useLanguage();

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('browseDirectory', language) || 'Browse Directory'}
      size="lg"
    >
      <RemoteDirectoryBrowser
        machineId={machineId}
        initialPath={initialPath}
        osType={osType}
        onSelectPath={(path) => {
          onSelectPath(path);
          onClose();
        }}
        onClose={onClose}
      />
    </Modal>
  );
};
