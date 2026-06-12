/**
 * ReportPreviewModal Component - Preview HTML compliance reports
 *
 * Features:
 * - HTML report preview using iframe with sandbox security
 * - Responsive design (mobile full-screen, desktop modal)
 * - Accessibility: ESC key support, screen reader support
 * - Print functionality integration
 * - Download options
 */

import React, { useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Modal, Button, Loading } from '@/components/common';
import { getReportTypeName } from '@/utils/compliance';

interface ReportPreviewModalProps {
  isOpen: boolean;
  onClose: () => void;
  htmlContent: string;
  reportType: string;
  reportId?: string;
  onDownload?: (format: 'html' | 'excel' | 'csv' | 'json') => void;
  isDownloading?: boolean;
}

export const ReportPreviewModal: React.FC<ReportPreviewModalProps> = ({
  isOpen,
  onClose,
  htmlContent,
  reportType,
  reportId,
  onDownload,
  isDownloading,
}) => {
  const language = useLanguage();

  // Handle ESC key for accessibility
  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (event.key === 'Escape' && isOpen) {
        onClose();
      }
    },
    [isOpen, onClose]
  );

  useEffect(() => {
    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [handleKeyDown]);

  // Handle print
  const handlePrint = useCallback(() => {
    // Create a new window for printing
    const printWindow = window.open('', '_blank');
    if (printWindow) {
      printWindow.document.write(htmlContent);
      printWindow.document.close();
      // Wait for content to load then print
      printWindow.onload = () => {
        printWindow.print();
      };
    }
  }, [htmlContent]);

  if (!isOpen) return null;

  const reportTypeName = getReportTypeName(reportType, language, reportType);

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={t('reportPreview', language)}
      size="lg"
      footer={
        <div className="d-flex gap-2 justify-content-between">
          <div className="d-flex gap-2">
            <Button
              variant="outline-secondary"
              onClick={handlePrint}
              title={t('printReport', language)}
            >
              <i className="bi bi-printer me-1" aria-hidden="true" />
              {t('print', language)}
            </Button>
            {onDownload && (
              <>
                <Button
                  variant="outline-primary"
                  onClick={() => onDownload('html')}
                  loading={isDownloading}
                  title={t('downloadHtml', language)}
                >
                  <i className="bi bi-filetype-html me-1" aria-hidden="true" />
                  HTML
                </Button>
                <Button
                  variant="outline-success"
                  onClick={() => onDownload('excel')}
                  loading={isDownloading}
                  title={t('downloadExcel', language)}
                >
                  <i className="bi bi-filetype-xlsx me-1" aria-hidden="true" />
                  Excel
                </Button>
              </>
            )}
          </div>
          <Button variant="secondary" onClick={onClose}>
            {t('close', language)}
          </Button>
        </div>
      }
    >
      {/* Report type badge */}
      <div className="mb-3">
        <span className="badge bg-primary">{reportTypeName}</span>
        {reportId && (
          <span className="ms-2 text-muted small">
            {t('reportId', language)}: {reportId}
          </span>
        )}
      </div>

      {/* Preview iframe with sandbox security */}
      <div
        className="preview-container border rounded"
        style={{
          height: '500px',
          overflow: 'auto',
          backgroundColor: '#f8fafc',
        }}
      >
        {!htmlContent ? (
          <div className="d-flex justify-content-center align-items-center h-100">
            <Loading size="lg" text={t('loadingPreview', language)} />
          </div>
        ) : (
          <iframe
            srcDoc={htmlContent}
            title={`${reportTypeName} Preview`}
            sandbox="allow-same-origin"
            style={{
              width: '100%',
              height: '100%',
              border: 'none',
            }}
            // Accessibility: meaningful title
            aria-label={`${t('reportPreview', language)} - ${reportTypeName}`}
          />
        )}
      </div>

      {/* Print tip */}
      <div className="mt-2 text-muted small">
        <i className="bi bi-info-circle me-1" aria-hidden="true" />
        {t('printTip', language)}
      </div>
    </Modal>
  );
};