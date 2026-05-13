/**
 * AvatarUploader Component - Upload and crop user avatar
 */

import React, { useState, useRef, useCallback } from 'react';
import { Avatar } from './Avatar';
import { Modal } from './Modal';
import { useToast } from './Toast';
import { t } from '@/i18n';
import { useLanguage } from '@/store';

interface AvatarUploaderProps {
  currentAvatarUrl?: string;
  username?: string;
  onUpload: (file: File) => Promise<void>;
  onDelete: () => Promise<void>;
  uploading?: boolean;
}

const MAX_SIZE = 400; // Max output dimension
const QUALITY = 0.8; // JPEG quality

export const AvatarUploader: React.FC<AvatarUploaderProps> = ({
  currentAvatarUrl,
  username,
  onUpload,
  onDelete,
  uploading = false,
}) => {
  const language = useLanguage();
  const toast = useToast();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [showCropModal, setShowCropModal] = useState(false);
  const [imageSrc, setImageSrc] = useState<string | null>(null);
  const [crop, setCrop] = useState({ x: 0, y: 0, size: 0 });
  const [imageSize, setImageSize] = useState({ width: 0, height: 0 });
  const [dragging, setDragging] = useState(false);
  const [dragStart, setDragStart] = useState({ x: 0, y: 0 });
  const imageRef = useRef<HTMLImageElement | null>(null);

  const handleFileSelect = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      // Validate file type
      const allowedTypes = ['image/jpeg', 'image/png', 'image/gif', 'image/webp'];
      if (!allowedTypes.includes(file.type)) {
        toast.error(t('invalidFileType', language));
        return;
      }

      // Validate file size (2MB)
      if (file.size > 2 * 1024 * 1024) {
        toast.error(t('fileTooLarge', language));
        return;
      }

      const reader = new FileReader();
      reader.onload = (event) => {
        setImageSrc(event.target?.result as string);
        setShowCropModal(true);
      };
      reader.readAsDataURL(file);

      // Reset input
      if (fileInputRef.current) {
        fileInputRef.current.value = '';
      }
    },
    [language]
  );

  const handleImageLoad = useCallback((img: HTMLImageElement) => {
    imageRef.current = img;
    const containerSize = 300;
    const scale = Math.min(containerSize / img.naturalWidth, containerSize / img.naturalHeight);
    const displayWidth = img.naturalWidth * scale;
    const displayHeight = img.naturalHeight * scale;

    setImageSize({ width: displayWidth, height: displayHeight });

    // Initialize crop to center square
    const cropSize = Math.min(displayWidth, displayHeight) * 0.8;
    setCrop({
      x: (displayWidth - cropSize) / 2,
      y: (displayHeight - cropSize) / 2,
      size: cropSize,
    });
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      setDragging(true);
      setDragStart({ x: e.clientX - crop.x, y: e.clientY - crop.y });
    },
    [crop.x, crop.y]
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!dragging) return;

      let newX = e.clientX - dragStart.x;
      let newY = e.clientY - dragStart.y;

      // Constrain to image bounds
      newX = Math.max(0, Math.min(newX, imageSize.width - crop.size));
      newY = Math.max(0, Math.min(newY, imageSize.height - crop.size));

      setCrop((prev) => ({ ...prev, x: newX, y: newY }));
    },
    [dragging, dragStart, imageSize, crop.size]
  );

  const handleMouseUp = useCallback(() => {
    setDragging(false);
  }, []);

  const handleTouchStart = useCallback(
    (e: React.TouchEvent) => {
      const touch = e.touches[0];
      setDragging(true);
      setDragStart({ x: touch.clientX - crop.x, y: touch.clientY - crop.y });
    },
    [crop.x, crop.y]
  );

  const handleTouchMove = useCallback(
    (e: React.TouchEvent) => {
      if (!dragging) return;
      e.preventDefault();

      const touch = e.touches[0];
      let newX = touch.clientX - dragStart.x;
      let newY = touch.clientY - dragStart.y;

      // Constrain to image bounds
      newX = Math.max(0, Math.min(newX, imageSize.width - crop.size));
      newY = Math.max(0, Math.min(newY, imageSize.height - crop.size));

      setCrop((prev) => ({ ...prev, x: newX, y: newY }));
    },
    [dragging, dragStart, imageSize, crop.size]
  );

  const handleTouchEnd = useCallback(() => {
    setDragging(false);
  }, []);

  const handleCropConfirm = useCallback(async () => {
    if (!imageRef.current || !canvasRef.current) return;

    const img = imageRef.current;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Calculate scale factor
    const scaleX = img.naturalWidth / imageSize.width;
    const scaleY = img.naturalHeight / imageSize.height;

    // Set canvas size to output size
    canvas.width = MAX_SIZE;
    canvas.height = MAX_SIZE;

    // Draw cropped image
    const sourceX = crop.x * scaleX;
    const sourceY = crop.y * scaleY;
    const sourceSize = crop.size * scaleX;

    ctx.drawImage(img, sourceX, sourceY, sourceSize, sourceSize, 0, 0, MAX_SIZE, MAX_SIZE);

    // Convert to blob
    canvas.toBlob(
      async (blob) => {
        if (!blob) return;
        const file = new File([blob], 'avatar.jpg', { type: 'image/jpeg' });
        await onUpload(file);
        setShowCropModal(false);
        setImageSrc(null);
      },
      'image/jpeg',
      QUALITY
    );
  }, [imageSize, crop, onUpload]);

  const handleCancel = useCallback(() => {
    setShowCropModal(false);
    setImageSrc(null);
  }, []);

  return (
    <div className="avatar-uploader d-flex flex-column align-items-center">
      {/* Current Avatar */}
      <div className="mb-3">
        <Avatar src={currentAvatarUrl} name={username} size="xl" shape="circle" />
      </div>

      {/* Action Buttons */}
      <div className="d-flex gap-2">
        <button
          className="btn btn-sm btn-outline-primary"
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
        >
          {uploading ? (
            <>
              <span className="spinner-border spinner-border-sm me-1" role="status" />
              {t('uploading', language)}
            </>
          ) : (
            <>
              <i className="bi bi-upload me-1" />
              {currentAvatarUrl ? t('changeAvatar', language) : t('uploadAvatar', language)}
            </>
          )}
        </button>

        {currentAvatarUrl && (
          <button className="btn btn-sm btn-outline-danger" onClick={onDelete} disabled={uploading}>
            <i className="bi bi-trash me-1" />
            {t('removeAvatar', language)}
          </button>
        )}
      </div>

      {/* Hidden File Input */}
      <input
        ref={fileInputRef}
        type="file"
        accept="image/jpeg,image/png,image/gif,image/webp"
        onChange={handleFileSelect}
        className="d-none"
      />

      {/* Crop Modal */}
      <Modal
        isOpen={showCropModal}
        onClose={handleCancel}
        title={t('cropAvatar', language)}
        size="sm"
      >
        {imageSrc && (
          <div className="text-center">
            {/* Image with crop overlay */}
            <div
              className="position-relative d-inline-block overflow-hidden"
              style={{ maxWidth: '100%', maxHeight: '300px', touchAction: 'none' }}
              onMouseMove={handleMouseMove}
              onMouseUp={handleMouseUp}
              onMouseLeave={handleMouseUp}
              onTouchMove={handleTouchMove}
              onTouchEnd={handleTouchEnd}
            >
              <img
                src={imageSrc}
                alt="Avatar preview"
                style={{
                  maxWidth: '300px',
                  maxHeight: '300px',
                  display: 'block',
                  userSelect: 'none',
                }}
                onLoad={(e) => handleImageLoad(e.currentTarget)}
                draggable={false}
              />

              {/* Crop overlay */}
              {imageSize.width > 0 && (
                <>
                  {/* Dark overlay */}
                  <div
                    className="position-absolute top-0 start-0 w-100 h-100"
                    style={{ backgroundColor: 'rgba(0,0,0,0.5)' }}
                  />
                  {/* Crop area */}
                  <div
                    className="position-absolute"
                    style={{
                      left: crop.x,
                      top: crop.y,
                      width: crop.size,
                      height: crop.size,
                      border: '2px solid white',
                      cursor: 'move',
                      backgroundImage: `url(${imageSrc})`,
                      backgroundPosition: `-${crop.x}px -${crop.y}px`,
                      backgroundSize: `${imageSize.width}px ${imageSize.height}px`,
                    }}
                    onMouseDown={handleMouseDown}
                    onTouchStart={handleTouchStart}
                  />
                </>
              )}
            </div>

            {/* Hidden canvas for output */}
            <canvas ref={canvasRef} className="d-none" />
          </div>
        )}

        <div className="d-flex justify-content-end gap-2 mt-3">
          <button className="btn btn-secondary" onClick={handleCancel}>
            {t('cancel', language)}
          </button>
          <button className="btn btn-primary" onClick={handleCropConfirm}>
            {t('cropConfirm', language)}
          </button>
        </div>
      </Modal>
    </div>
  );
};
