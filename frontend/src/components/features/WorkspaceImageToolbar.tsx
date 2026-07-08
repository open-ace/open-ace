/**
 * WorkspaceImageToolbar Component - Image upload toolbar for Workspace
 *
 * Provides image upload functionality with:
 * - Upload button in toolbar
 * - Integration with iframe via postMessage
 * - Storage quota display
 */

import React, { useCallback, useState } from 'react';
import { useToast } from '@/components/common/Toast';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { uploadImage, validateFile, getUserQuota } from '@/api/images';
import type { UploadedImage, StorageQuota } from '@/api/images';

interface WorkspaceImageToolbarProps {
  sessionId?: string;
  onImageUploaded?: (image: UploadedImage) => void;
  disabled?: boolean;
}

export const WorkspaceImageToolbar: React.FC<WorkspaceImageToolbarProps> = ({
  sessionId,
  onImageUploaded,
  disabled = false,
}) => {
  const language = useLanguage();
  const toast = useToast();
  const fileInputRef = React.useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [quota, setQuota] = useState<StorageQuota | null>(null);

  // Load quota on mount
  React.useEffect(() => {
    getUserQuota()
      .then((res) => {
        if (res.success) {
          setQuota(res.quota);
        }
      })
      .catch(() => {});
  }, []);

  const handleUploadClick = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

  const handleFileChange = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      // Validate
      const validation = validateFile(file);
      if (!validation.valid) {
        toast.error(validation.error ?? t('invalidFile', language));
        return;
      }

      // Upload
      setUploading(true);
      try {
        const response = await uploadImage(file, sessionId);

        if (response.success && response.image) {
          toast.success(t('uploadSuccess', language) || '图片上传成功');

          // Notify iframe via postMessage
          if (response.image.stored_path) {
            window.postMessage(
              {
                type: 'openace-image-uploaded',
                imagePath: response.image.stored_path,
                imageId: response.image.id,
                filename: response.image.filename,
              },
              '*'
            );
          }

          onImageUploaded?.(response.image);

          // Refresh quota
          const quotaRes = await getUserQuota();
          if (quotaRes.success) {
            setQuota(quotaRes.quota);
          }
        }
      } catch (err) {
        const error = err instanceof Error ? err.message : 'Upload failed';
        toast.error(error);
      } finally {
        setUploading(false);
        // Reset input
        if (fileInputRef.current) {
          fileInputRef.current.value = '';
        }
      }
    },
    [sessionId, language, toast, onImageUploaded]
  );

  const quotaWarning = quota && quota.usage_percentage >= 80;

  return (
    <div className="workspace-image-toolbar d-flex align-items-center gap-2">
      {/* Upload button */}
      <button
        className="btn btn-sm btn-outline-secondary"
        onClick={handleUploadClick}
        disabled={disabled || uploading}
        title={t('uploadImage', language) || '上传图片'}
        type="button"
      >
        {uploading ? (
          <span className="spinner-border spinner-border-sm" role="status" />
        ) : (
          <i className="bi bi-image" />
        )}
      </button>

      {/* Hidden file input */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/png,image/jpeg,image/gif,image/webp,image/bmp"
        onChange={handleFileChange}
        className="d-none"
        disabled={disabled || uploading}
      />

      {/* Quota indicator */}
      {quota && (
        <span
          className={`badge ${quotaWarning ? 'bg-warning' : 'bg-secondary'}`}
          style={{ fontSize: '0.7rem' }}
          title={`${t('storageUsage', language) || '存储用量'}: ${(quota.used_bytes / (1024 * 1024)).toFixed(1)}MB / ${(quota.quota_bytes / (1024 * 1024)).toFixed(0)}MB`}
        >
          {quota.usage_percentage.toFixed(0)}%
        </span>
      )}
    </div>
  );
};
