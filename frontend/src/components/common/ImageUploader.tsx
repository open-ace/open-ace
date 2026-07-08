/**
 * ImageUploader Component - Image upload with drag & drop and preview
 *
 * Features:
 * - Click to select file
 * - Drag and drop support
 * - Preview before upload
 * - Progress indicator
 * - Storage quota display
 */

import React, { useCallback, useRef, useState } from 'react';
import { DropZone } from './DropZone';
import { ImagePreviewModal } from './ImagePreviewModal';
import { useToast } from './Toast';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  uploadImage as uploadImageApi,
  validateFile,
  getUserQuota,
  UploadedImage,
  StorageQuota,
} from '@/api/images';

interface ImageUploaderProps {
  sessionId?: string;
  projectId?: number;
  onUploadSuccess?: (image: UploadedImage) => void;
  onUploadError?: (error: string) => void;
  maxSizeMb?: number;
  allowedTypes?: string[];
  allowSvg?: boolean;
  showQuota?: boolean;
  className?: string;
}

export const ImageUploader: React.FC<ImageUploaderProps> = ({
  sessionId,
  projectId,
  onUploadSuccess,
  onUploadError,
  maxSizeMb = 10,
  allowedTypes = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp'],
  allowSvg = false,
  showQuota = true,
  className = '',
}) => {
  const language = useLanguage();
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [showPreview, setShowPreview] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [progress, setProgress] = useState(0);
  const [quota, setQuota] = useState<StorageQuota | null>(null);

  // Load quota on mount
  React.useEffect(() => {
    if (showQuota) {
      getUserQuota()
        .then((res) => {
          if (res.success) {
            setQuota(res.quota);
          }
        })
        .catch(() => {
          // Ignore quota fetch errors
        });
    }
  }, [showQuota]);

  const handleFilesSelected = useCallback(
    (files: File[]) => {
      if (files.length === 0) return;

      const file = files[0];
      const validation = validateFile(file, maxSizeMb, allowedTypes, allowSvg);

      if (!validation.valid) {
        toast.error(validation.error || t('invalidFile', language) || '无效文件');
        return;
      }

      // Create preview URL
      const url = URL.createObjectURL(file);
      setSelectedFile(file);
      setPreviewUrl(url);
      setShowPreview(true);
    },
    [maxSizeMb, allowedTypes, allowSvg, language, toast]
  );

  const handleCancelPreview = useCallback(() => {
    setShowPreview(false);
    if (previewUrl) {
      URL.revokeObjectURL(previewUrl);
    }
    setSelectedFile(null);
    setPreviewUrl(null);
    setProgress(0);
  }, [previewUrl]);

  const handleConfirmUpload = useCallback(async () => {
    if (!selectedFile) return;

    setUploading(true);
    setProgress(0);

    try {
      const response = await uploadImageApi(
        selectedFile,
        sessionId,
        projectId,
        (p) => setProgress(p)
      );

      if (response.success && response.image) {
        toast.success(t('uploadSuccess', language) || '上传成功');
        onUploadSuccess?.(response.image);
        handleCancelPreview();

        // Refresh quota
        if (showQuota) {
          const quotaRes = await getUserQuota();
          if (quotaRes.success) {
            setQuota(quotaRes.quota);
          }
        }
      } else {
        throw new Error('Upload failed');
      }
    } catch (err) {
      const error = err instanceof Error ? err.message : 'Upload failed';
      toast.error(error);
      onUploadError?.(error);
    } finally {
      setUploading(false);
    }
  }, [selectedFile, sessionId, projectId, showQuota, language, toast, onUploadSuccess, onUploadError, handleCancelPreview]);

  const handleClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleInputChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files ? Array.from(e.target.files) : [];
      handleFilesSelected(files);
      // Reset input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    },
    [handleFilesSelected]
  );

  // Cleanup on unmount
  React.useEffect(() => {
    return () => {
      if (previewUrl) {
        URL.revokeObjectURL(previewUrl);
      }
    };
  }, [previewUrl]);

  const acceptTypes = allowedTypes.map((t) => `.${t}`).join(',');
  const formatSize = (bytes: number) => `${(bytes / (1024 * 1024)).toFixed(1)}MB`;

  return (
    <div className={`image-uploader ${className}`}>
      {/* Quota display */}
      {showQuota && quota && (
        <div className="quota-display mb-2 d-flex justify-content-between align-items-center">
          <small className="text-muted">
            {t('storageUsage', language) || '存储用量'}: {formatSize(quota.used_bytes)} / {formatSize(quota.quota_bytes)}
          </small>
          <small className={`text-${quota.usage_percentage >= 80 ? 'warning' : 'muted'}`}>
            {quota.usage_percentage.toFixed(0)}%
          </small>
        </div>
      )}

      {/* Upload button */}
      <div className="upload-button-container">
        <button
          className="btn btn-outline-primary"
          onClick={handleClick}
          disabled={uploading}
          type="button"
        >
          {uploading ? (
            <>
              <span className="spinner-border spinner-border-sm me-1" role="status" />
              {progress > 0 ? `${Math.round(progress)}%` : t('uploading', language) || '上传中'}
            </>
          ) : (
            <>
              <i className="bi bi-image me-1" />
              {t('uploadImage', language) || '上传图片'}
            </>
          )}
        </button>

        {/* Hidden file input */}
        <input
          ref={fileInputRef}
          type="file"
          accept={acceptTypes}
          onChange={handleInputChange}
          className="d-none"
          disabled={uploading}
        />
      </div>

      {/* Drag drop zone (optional) */}
      <DropZone
        onFilesSelected={handleFilesSelected}
        accept={acceptTypes}
        disabled={uploading}
        className="mt-2"
      >
        <small className="text-muted">
          {t('dragDropHint', language) || '或拖放图片到此处'}
          <br />
          {t('supportedFormats', language) || '支持格式'}: {allowedTypes.join(', ')}
          {' | '}
          {t('maxSize', language) || '最大'}: {maxSizeMb}MB
        </small>
      </DropZone>

      {/* Preview Modal */}
      <ImagePreviewModal
        isOpen={showPreview}
        onClose={handleCancelPreview}
        imageFile={selectedFile}
        imagePreviewUrl={previewUrl}
        onConfirm={handleConfirmUpload}
        uploading={uploading}
      />

      <style>{`
        .image-uploader {
          max-width: 100%;
        }
        .quota-display {
          font-size: 0.85rem;
        }
      `}</style>
    </div>
  );
};