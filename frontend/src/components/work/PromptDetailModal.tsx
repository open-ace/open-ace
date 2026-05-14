/**
 * PromptDetailModal Component - Modal for viewing prompt details and filling variables
 */

import React, { useState, useEffect, useMemo, useRef } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { promptsApi } from '@/api';
import type { PromptTemplate, PromptVariable } from '@/api/prompts';
import { useCopyPrompt } from '@/hooks';
import { Modal, useToast } from '@/components/common';

interface PromptDetailModalProps {
  isOpen: boolean;
  onClose: () => void;
  prompt: PromptTemplate | null;
}

export const PromptDetailModal: React.FC<PromptDetailModalProps> = ({
  isOpen,
  onClose,
  prompt,
}) => {
  const language = useLanguage();
  const toast = useToast();
  const copyPromptMutation = useCopyPrompt();
  const [variableValues, setVariableValues] = useState<Record<string, string>>({});
  const [renderedContent, setRenderedContent] = useState<string>('');
  const [isRendering, setIsRendering] = useState(false);
  const [hasRendered, setHasRendered] = useState(false);
  const [copied, setCopied] = useState(false);
  const firstInputRef = useRef<HTMLInputElement>(null);

  // Check if prompt has variables
  const hasVariables = useMemo(() => {
    return (prompt?.variables?.length ?? 0) > 0;
  }, [prompt]);

  // Required variables
  const requiredVariables = useMemo(() => {
    return prompt?.variables?.filter((v) => v.required) ?? [];
  }, [prompt]);

  // Check if all required variables are filled
  const allRequiredFilled = useMemo(() => {
    if (!hasVariables) return true;
    return requiredVariables.every((v) => variableValues[v.name]?.trim());
  }, [hasVariables, requiredVariables, variableValues]);

  // Initialize variable values with defaults
  useEffect(() => {
    if (prompt?.variables) {
      const defaults: Record<string, string> = {};
      prompt.variables.forEach((v) => {
        defaults[v.name] = v.default ?? '';
      });
      setVariableValues(defaults);
      setRenderedContent('');
      setHasRendered(false);
      setCopied(false);
    }
  }, [prompt]);

  // Auto-focus first variable input when modal opens
  useEffect(() => {
    if (isOpen && hasVariables && firstInputRef.current) {
      // Small delay to ensure modal is rendered
      setTimeout(() => {
        firstInputRef.current?.focus();
      }, 100);
    }
  }, [isOpen, hasVariables]);

  // Handle variable input change
  const handleVariableChange = (name: string, value: string) => {
    setVariableValues((prev) => ({ ...prev, [name]: value }));
    setHasRendered(false);
    setCopied(false);
  };

  // Render prompt with variables
  const handleRender = async () => {
    if (!prompt || !allRequiredFilled) return;

    setIsRendering(true);
    try {
      const result = await promptsApi.render(prompt.id, variableValues);
      setRenderedContent(result);
      setHasRendered(true);
      copyPromptMutation.mutate(prompt.id);
      toast.success(t('copied', language));
    } catch (err) {
      console.error('Failed to render prompt:', err);
      toast.error(t('renderFailed', language) || 'Failed to render prompt');
    } finally {
      setIsRendering(false);
    }
  };

  // Copy content to clipboard and close modal
  const handleCopy = async () => {
    const contentToCopy = hasVariables && hasRendered ? renderedContent : (prompt?.content ?? '');
    if (!contentToCopy) return;

    try {
      await navigator.clipboard.writeText(contentToCopy);
      if (prompt) {
        copyPromptMutation.mutate(prompt.id);
      }
      setCopied(true);
      setTimeout(() => {
        onClose();
      }, 800);
    } catch (err) {
      console.error('Failed to copy:', err);
      toast.error(t('copyFailed', language) || 'Copy failed');
    }
  };

  if (!prompt) return null;

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={prompt.name} size="lg">
      <div className="prompt-detail">
        {/* Category */}
        {prompt.category && (
          <div className="mb-2">
            <span className="badge bg-secondary">{prompt.category}</span>
          </div>
        )}

        {/* Description */}
        {prompt.description && (
          <div className="mb-3">
            <small className="text-muted">{prompt.description}</small>
          </div>
        )}

        {/* Variables Section */}
        {hasVariables && (
          <div className="prompt-variables-section mb-3">
            <h6 className="mb-2">
              <i className="bi bi-input-cursor-text me-1" />
              {t('variables', language)}
            </h6>
            <div className="prompt-variables-grid">
              {prompt.variables.map((v: PromptVariable, index: number) => (
                <div key={v.name} className="prompt-variable-item">
                  <label className="form-label small">
                    {v.name}
                    {v.required && <span className="text-danger ms-1">*</span>}
                    {v.description && (
                      <span className="text-muted ms-2 small">({v.description})</span>
                    )}
                  </label>
                  <input
                    ref={index === 0 ? firstInputRef : null}
                    type="text"
                    className="form-control form-control-sm"
                    value={variableValues[v.name] ?? ''}
                    onChange={(e) => handleVariableChange(v.name, e.target.value)}
                    placeholder={v.default ?? ''}
                  />
                </div>
              ))}
            </div>
            <div className="mt-2">
              <button
                className="btn btn-sm btn-outline-primary"
                onClick={handleRender}
                disabled={!allRequiredFilled || isRendering}
              >
                {isRendering ? (
                  <span className="spinner-border spinner-border-sm me-1" />
                ) : (
                  <i className="bi bi-magic me-1" />
                )}
                {t('generate', language)}
              </button>
              {!allRequiredFilled && (
                <small className="text-muted ms-2">{t('fillRequiredFirst', language)}</small>
              )}
            </div>
          </div>
        )}

        {/* Content Display */}
        <div className="prompt-content-section">
          <h6 className="mb-2">
            <i className="bi bi-file-text me-1" />
            {t('promptContent', language)}
          </h6>
          <div className="prompt-content-box">
            <pre className="prompt-content-text">
              {hasVariables && hasRendered ? renderedContent : prompt.content}
            </pre>
          </div>
        </div>

        {/* Copy Button */}
        <div className="mt-3 d-flex justify-content-end">
          <button
            className={`btn ${copied ? 'btn-success' : 'btn-primary'}`}
            onClick={handleCopy}
            disabled={(hasVariables && !hasRendered) || copied}
          >
            {copied ? (
              <>
                <i className="bi bi-check2 me-1" />
                {t('copied', language)}
              </>
            ) : (
              <>
                <i className="bi bi-clipboard me-1" />
                {t('copy', language)}
              </>
            )}
          </button>
        </div>
      </div>
    </Modal>
  );
};
