/**
 * Modal Component - Reusable modal dialog with animations
 */

import React, { useEffect, useCallback, useRef } from 'react';
import { createPortal } from 'react-dom';
import { cn } from '@/utils';
import type { ModalProps } from '@/types';

const sizeClasses: Record<string, string> = {
  sm: 'modal-sm',
  md: '',
  lg: 'modal-lg',
  xl: 'modal-xl',
};

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  size = 'md',
  children,
  footer,
  className,
}) => {
  const modalRef = useRef<HTMLDivElement>(null);

  // Handle escape key
  const handleEscape = useCallback(
    (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
      }
    },
    [onClose]
  );

  // Handle enter key for form submission
  const handleEnter = useCallback((e: KeyboardEvent) => {
    if (e.key === 'Enter') {
      // Don't submit if focus is on textarea, button, or select
      const activeElement = document.activeElement as HTMLElement;
      const tagName = activeElement?.tagName?.toLowerCase();
      const isTextarea = tagName === 'textarea';
      const isButton = tagName === 'button';
      const isSelect = tagName === 'select';

      // Allow Enter on buttons (they handle their own click)
      if (isButton) return;

      // Don't submit if in textarea (user might want newline)
      if (isTextarea) return;

      // Don't submit if in select
      if (isSelect) return;

      // Find form inside modal and submit it
      if (modalRef.current) {
        const form = modalRef.current.querySelector('form') as HTMLFormElement | null;
        if (form) {
          e.preventDefault();
          // Use requestSubmit to properly trigger React's onSubmit handler
          form.requestSubmit();
        }
      }
    }
  }, []);

  // Add/remove event listener
  useEffect(() => {
    if (isOpen) {
      document.addEventListener('keydown', handleEscape);
      document.addEventListener('keydown', handleEnter);
      document.body.style.overflow = 'hidden';
    }
    return () => {
      document.removeEventListener('keydown', handleEscape);
      document.removeEventListener('keydown', handleEnter);
      document.body.style.overflow = '';
    };
  }, [isOpen, handleEscape, handleEnter]);

  if (!isOpen) return null;

  return createPortal(
    <>
      {/* Backdrop - rendered first (behind modal) */}
      <div
        className="modal-backdrop fade show"
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          backgroundColor: 'rgba(0, 0, 0, 0.5)',
          zIndex: 10040,
        }}
        onClick={onClose}
      />
      {/* Modal */}
      <div
        className="modal fade show d-block"
        role="dialog"
        aria-modal="true"
        style={{
          position: 'fixed',
          top: 0,
          left: 0,
          width: '100%',
          height: '100%',
          zIndex: 10050,
        }}
        onClick={(e) => {
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          className={cn(
            'modal-dialog modal-dialog-centered modal-dialog-scrollable',
            sizeClasses[size],
            className
          )}
        >
          <div ref={modalRef} className="modal-content animate-slide-up">
            {title && (
              <div className="modal-header">
                <h5 className="modal-title">{title}</h5>
                <button type="button" className="btn-close" onClick={onClose} aria-label="Close" />
              </div>
            )}
            <div className="modal-body">{children}</div>
            {footer && <div className="modal-footer">{footer}</div>}
          </div>
        </div>
      </div>
    </>,
    document.body
  );
};

/**
 * Confirm Modal - Pre-built confirmation dialog
 */
interface ConfirmModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  variant?: 'danger' | 'warning' | 'primary';
  loading?: boolean;
}

export const ConfirmModal: React.FC<ConfirmModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  title,
  message,
  confirmText = 'Confirm',
  cancelText = 'Cancel',
  variant = 'primary',
  loading = false,
}) => {
  return (
    <Modal isOpen={isOpen} onClose={onClose} title={title} size="sm">
      <p className="mb-0">{message}</p>
      <div className="modal-footer border-0 px-0 pb-0">
        <button type="button" className="btn btn-secondary" onClick={onClose} disabled={loading}>
          {cancelText}
        </button>
        <button
          type="button"
          className={cn('btn', `btn-${variant}`)}
          onClick={onConfirm}
          disabled={loading}
        >
          {loading && <span className="spinner-border spinner-border-sm me-2" role="status" />}
          {confirmText}
        </button>
      </div>
    </Modal>
  );
};
