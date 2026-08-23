/**
 * PromptsDrawer Component - Floating prompts drawer for the workspace route
 *
 * Replaces the retired right-side AssistPanel. Opens as a non-modal side
 * sheet anchored to the right edge so the user can copy a prompt and paste
 * it into the workspace iframe chat without closing the drawer.
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { usePrompts, usePromptCategories, useCopyPrompt } from '@/hooks';
import type { PromptTemplate } from '@/api/prompts';
import { Loading, EmptyState, useToast, Tooltip } from '@/components/common';
import { copyToClipboard } from '@/utils';
import { PromptDetailModal } from './PromptDetailModal';
import './PromptsDrawer.css';

interface PromptsDrawerProps {
  isOpen: boolean;
  onClose: () => void;
}

// Debounce delay in milliseconds
const DEBOUNCE_DELAY = 300;

export const PromptsDrawer: React.FC<PromptsDrawerProps> = ({ isOpen, onClose }) => {
  const language = useLanguage();
  const toast = useToast();
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [searchInput, setSearchInput] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [selectedPrompt, setSelectedPrompt] = useState<PromptTemplate | null>(null);
  const [promptModalOpen, setPromptModalOpen] = useState(false);
  const [copiedPromptId, setCopiedPromptId] = useState<number | null>(null);
  const debounceTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const copyPromptMutation = useCopyPrompt();

  // Debounce search input
  useEffect(() => {
    if (debounceTimerRef.current) {
      clearTimeout(debounceTimerRef.current);
    }
    debounceTimerRef.current = setTimeout(() => {
      setDebouncedSearch(searchInput);
    }, DEBOUNCE_DELAY);
    return () => {
      if (debounceTimerRef.current) {
        clearTimeout(debounceTimerRef.current);
      }
    };
  }, [searchInput]);

  // Fetch categories via React Query
  const { data: categories = [] } = usePromptCategories();

  // Fetch prompts via React Query
  const { data: promptsData, isLoading: promptsLoading } = usePrompts({
    page: 1,
    limit: 100,
    category: selectedCategory || undefined,
    search: debouncedSearch || undefined,
  });
  const prompts = promptsData?.templates ?? [];

  // Check if prompt has any variables (including optional)
  const hasAnyVariables = (prompt: PromptTemplate): boolean => {
    return (prompt.variables?.length ?? 0) > 0;
  };

  // Check if prompt has required variables
  const hasRequiredVariables = (prompt: PromptTemplate): boolean => {
    return prompt.variables?.some((v) => v.required) ?? false;
  };

  const handleSearchChange = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    setSearchInput(e.target.value);
  }, []);

  const handleCategoryClick = (category: string) => {
    setSelectedCategory(category === selectedCategory ? '' : category);
  };

  // Open prompt detail modal
  const handlePromptClick = (prompt: PromptTemplate) => {
    setSelectedPrompt(prompt);
    setPromptModalOpen(true);
  };

  // Direct copy (for prompts without required variables)
  const handleDirectCopy = async (e: React.MouseEvent, prompt: PromptTemplate) => {
    e.stopPropagation();
    if (hasRequiredVariables(prompt)) return;

    const success = await copyToClipboard(prompt.content);
    if (success) {
      // Record copy action separately - failure should not affect copy result
      try {
        await copyPromptMutation.mutateAsync(prompt.id);
      } catch (copyErr) {
        console.warn('Failed to record prompt copy:', copyErr);
      }
      setCopiedPromptId(prompt.id);
      setTimeout(() => setCopiedPromptId(null), 1500);
      toast.success(t('copied', language), prompt.name);
    } else {
      toast.error(t('copyFailed', language) || 'Copy failed');
    }
  };

  // Truncate content for tooltip preview
  const truncateContent = (content: string, maxLength: number = 150): string => {
    if (content.length <= maxLength) return content;
    return content.slice(0, maxLength) + '...';
  };

  if (!isOpen) {
    return null;
  }

  return (
    <div
      className="prompts-drawer"
      role="dialog"
      aria-modal="false"
      aria-label={t('prompts', language)}
    >
      <div className="prompts-drawer-header">
        <span className="prompts-drawer-title">{t('prompts', language)}</span>
        <button
          className="prompts-drawer-close"
          onClick={onClose}
          aria-label={t('close', language)}
          title={t('close', language)}
        >
          <i className="bi bi-x-lg" />
        </button>
      </div>

      <div className="prompts-drawer-body">
        {/* Search Box */}
        <div className="prompt-search">
          <div className="input-group input-group-sm">
            <span className="input-group-text">
              <i className="bi bi-search" />
            </span>
            <input
              type="text"
              className="form-control"
              placeholder={t('searchPrompts', language) || 'Search prompts...'}
              value={searchInput}
              onChange={handleSearchChange}
            />
          </div>
        </div>

        {/* Category Filters */}
        {categories.length > 0 && (
          <div className="prompt-categories">
            {categories.map((cat) => (
              <button
                key={cat.category}
                className={`category-filter-btn ${selectedCategory === cat.category ? 'active' : ''}`}
                onClick={() => handleCategoryClick(cat.category)}
              >
                {cat.category}
                <span className="category-count">{cat.count}</span>
              </button>
            ))}
          </div>
        )}

        {/* Prompts List */}
        {promptsLoading ? (
          <Loading size="sm" text={t('loading', language)} />
        ) : prompts.length > 0 ? (
          <ul className="prompt-list list-unstyled">
            {prompts.map((prompt) => (
              <li key={prompt.id}>
                <div className="prompt-item" onClick={() => handlePromptClick(prompt)}>
                  {/* Left: Name with tooltip */}
                  <Tooltip content={truncateContent(prompt.content)} placement="bottom" delay={100}>
                    <div className="prompt-item-name-wrapper">
                      <span className="prompt-item-name">{prompt.name}</span>
                    </div>
                  </Tooltip>

                  {/* Right: Action buttons */}
                  <div className="prompt-item-actions">
                    {/* Fill variables button - active when has any variables */}
                    <button
                      className={`prompt-action-btn ${hasAnyVariables(prompt) ? 'active' : 'disabled'}`}
                      onClick={(e) => {
                        e.stopPropagation();
                        handlePromptClick(prompt);
                      }}
                      title={
                        hasAnyVariables(prompt)
                          ? t('fillVariables', language) || 'Fill variables'
                          : t('noVariables', language) || 'No variables'
                      }
                      disabled={false}
                    >
                      <i className="bi bi-input-cursor-text" />
                    </button>

                    {/* Copy button - disabled only when has required variables */}
                    <button
                      className={`prompt-action-btn ${copiedPromptId === prompt.id ? 'copied' : hasRequiredVariables(prompt) ? 'disabled' : 'active'}`}
                      onClick={(e) => handleDirectCopy(e, prompt)}
                      title={
                        hasRequiredVariables(prompt)
                          ? t('fillVariablesFirst', language) || 'Fill variables first'
                          : copiedPromptId === prompt.id
                            ? t('copied', language)
                            : t('copy', language) || 'Copy'
                      }
                      disabled={hasRequiredVariables(prompt)}
                    >
                      {copiedPromptId === prompt.id ? (
                        <span className="copied-text">{t('copied', language)}</span>
                      ) : (
                        <i className="bi bi-clipboard" />
                      )}
                    </button>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        ) : (
          <EmptyState icon="bi-file-text" title={t('noPromptsFound', language)} />
        )}
      </div>

      <div className="prompts-drawer-footer">
        <Link to="/work/prompts" className="prompts-drawer-view-all" onClick={onClose}>
          {t('viewAll', language)}
          <i className="bi bi-arrow-right ms-1" />
        </Link>
      </div>

      <PromptDetailModal
        isOpen={promptModalOpen}
        onClose={() => setPromptModalOpen(false)}
        prompt={selectedPrompt}
      />
    </div>
  );
};
