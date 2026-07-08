/**
 * DropZone Component - Drag and drop area for file upload
 */

import React, { useCallback, useState } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface DropZoneProps {
  onFilesSelected: (files: File[]) => void;
  accept?: string; // e.g. 'image/*' or '.png,.jpg'
  maxFiles?: number;
  disabled?: boolean;
  children?: React.ReactNode;
  className?: string;
}

export const DropZone: React.FC<DropZoneProps> = ({
  onFilesSelected,
  accept = 'image/*',
  maxFiles = 1,
  disabled = false,
  children,
  className = '',
}) => {
  const language = useLanguage();
  const [isDragOver, setIsDragOver] = useState(false);

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (!disabled) {
      setIsDragOver(true);
    }
  }, [disabled]);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  }, []);

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setIsDragOver(false);

      if (disabled) return;

      const files = Array.from(e.dataTransfer.files);
      if (files.length === 0) return;

      if (maxFiles > 0 && files.length > maxFiles) {
        // Only take first maxFiles files
        files.splice(maxFiles);
      }

      onFilesSelected(files);
    },
    [disabled, maxFiles, onFilesSelected]
  );

  const handleClick = useCallback(() => {
    if (disabled) return;
    // Trigger file input click via ref (handled by parent)
  }, [disabled]);

  return (
    <div
      className={`dropzone ${isDragOver ? 'dropzone-active' : ''} ${disabled ? 'dropzone-disabled' : ''} ${className}`}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      onClick={handleClick}
      style={{
        border: `2px dashed ${isDragOver ? '#0d6efd' : '#ccc'}`,
        borderRadius: '8px',
        padding: '20px',
        textAlign: 'center',
        cursor: disabled ? 'not-allowed' : 'pointer',
        transition: 'all 0.2s ease',
        backgroundColor: isDragOver ? '#f8f9fa' : 'transparent',
      }}
    >
      {children || (
        <div className="dropzone-content">
          <i
            className={`bi ${isDragOver ? 'bi-cloud-upload' : 'bi-cloud-arrow-up'} fs-3 ${isDragOver ? 'text-primary' : 'text-muted'}`}
          />
          <p className={`mt-2 mb-0 ${isDragOver ? 'text-primary' : 'text-muted'}`}>
            {isDragOver
              ? t('dropToUpload', language) || '释放上传'
              : t('dragDropOrClick', language) || '拖放文件或点击选择'}
          </p>
        </div>
      )}
    </div>
  );
};