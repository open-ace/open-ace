/**
 * LocalDirectoryBrowserModal Component - Modal wrapper for LocalDirectoryBrowser
 */

import React from 'react';
import { Modal } from '@/components/common';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { LocalDirectoryBrowser } from './LocalDirectoryBrowser';

interface LocalDirectoryBrowserModalProps {
  isOpen: boolean;
  onClose: () => void;
  initialPath?: string;
  onSelectPath: (path: string) => void;
}

export const LocalDirectoryBrowserModal: React.FC<LocalDirectoryBrowserModalProps> = ({
  isOpen,
  onClose,
  initialPath,
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
      <LocalDirectoryBrowser
        initialPath={initialPath}
        onSelectPath={(path) => {
          onSelectPath(path);
          onClose();
        }}
        onClose={onClose}
      />
    </Modal>
  );
};
