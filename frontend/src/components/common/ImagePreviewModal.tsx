/**
 * ImagePreviewModal Component - Preview uploaded image before confirmation
 */

import React, { useState } from 'react';
import { Modal } from './Modal';
import { Button } from './Button';
import { useLanguage } from '@/store';
import { t } from '@/i18n';

interface ImagePreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  imageFile: File | null;
  imagePreviewUrl: string | null;
  onConfirm: () => void;
  uploading?: boolean;
}

export const ImagePreviewModal: React.FC<ImagePreviewModalProps> = ({
  isOpen,
  onClose,
  imageFile,
  imagePreviewUrl,
  onConfirm,
  uploading = false,
}) => {
  const language = useLanguage();
  const [zoom, setZoom] = useState(1);

  if (!isOpen || !imageFile || !imagePreviewUrl) return null;

  const handleZoomIn = () => setZoom((z) => Math.min(z + 0.25, 3));
  const handleZoomOut = () => setZoom((z) => Math.max(z - 0.25, 0.5));

  const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(2)} MB`;
  };

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('previewImage', language) || '预览图片'}
      size="lg"
    >
      <div className="image-preview-container">
        {/* Image display */}
        <div
          className="image-preview-display position-relative overflow-auto"
          style={{
            maxHeight: '400px',
            minHeight: '200px',
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            backgroundColor: '#f8f9fa',
            borderRadius: '4px',
            marginBottom: '16px',
          }}
        >
          <img
            src={imagePreviewUrl}
            alt={imageFile.name}
            style={{
              maxWidth: '100%',
              maxHeight: '100%',
              transform: `scale(${zoom})`,
              transition: 'transform 0.2s ease',
            }}
          />
        </div>

        {/* Zoom controls */}
        <div className="d-flex justify-content-center gap-2 mb-3">
          <button
            className="btn btn-sm btn-outline-secondary"
            onClick={handleZoomOut}
            disabled={zoom <= 0.5}
          >
            <i className="bi bi-zoom-out" />
          </button>
          <span className="align-self-center text-muted small">{Math.round(zoom * 100)}%</span>
          <button
            className="btn btn-sm btn-outline-secondary"
            onClick={handleZoomIn}
            disabled={zoom >= 3}
          >
            <i className="bi bi-zoom-in" />
          </button>
        </div>

        {/* File info */}
        <div className="image-info mb-3">
          <div className="row g-2">
            <div className="col-6">
              <small className="text-muted">{t('fileName', language) || '文件名'}</small>
              <div className="text-truncate">{imageFile.name}</div>
            </div>
            <div className="col-3">
              <small className="text-muted">{t('fileSize', language) || '大小'}</small>
              <div>{formatFileSize(imageFile.size)}</div>
            </div>
            <div className="col-3">
              <small className="text-muted">{t('fileType', language) || '类型'}</small>
              <div>{imageFile.type}</div>
            </div>
          </div>
        </div>

        {/* Actions */}
        <div className="d-flex justify-content-end gap-2">
          <Button variant="secondary" onClick={onClose} disabled={uploading}>
            {t('cancel', language) || '取消'}
          </Button>
          <Button variant="primary" onClick={onConfirm} disabled={uploading}>
            {uploading ? (
              <>
                <span className="spinner-border spinner-border-sm me-1" role="status" />
                {t('uploading', language) || '上传中...'}
              </>
            ) : (
              <>
                <i className="bi bi-upload me-1" />
                {t('confirmUpload', language) || '确认上传'}
              </>
            )}
          </Button>
        </div>
      </div>

      <style>{`
        .image-preview-container {
          padding: 8px;
        }
      `}</style>
    </Modal>
  );
};
