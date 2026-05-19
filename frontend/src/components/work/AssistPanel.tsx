/**
 * AssistPanel Component - Right panel for Work Mode
 *
 * Features:
 * - Quick access to prompts with search and category filter
 * - AI tools shortcuts
 * - Help documentation
 */

import React, { useState, useEffect, useRef, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { usePrompts, usePromptCategories, useCopyPrompt } from '@/hooks';
import type { PromptTemplate } from '@/api/prompts';
import { Loading, EmptyState, SimpleTabs, useToast, Tooltip } from '@/components/common';
import { copyToClipboard } from '@/utils';
import { DocumentViewer } from './DocumentViewer';
import { PromptDetailModal } from './PromptDetailModal';
import './AssistPanel.css';

interface AssistPanelProps {
  collapsed?: boolean;
}

// Debounce delay in milliseconds
const DEBOUNCE_DELAY = 300;

export const AssistPanel: React.FC<AssistPanelProps> = ({ collapsed = false }) => {
  const language = useLanguage();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState('prompts');
  const [selectedCategory, setSelectedCategory] = useState<string>('');
  const [searchInput, setSearchInput] = useState('');
  const [debouncedSearch, setDebouncedSearch] = useState('');
  const [docViewerOpen, setDocViewerOpen] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string>('');
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

  // AI Tools list
  const aiTools = [
    { id: 'openclaw', name: 'OpenClaw', icon: 'bi-robot', url: '/work?tool=openclaw' },
    { id: 'claude', name: 'Claude', icon: 'bi-chat-square-text', url: '/work?tool=claude' },
    { id: 'qwen', name: 'Qwen', icon: 'bi-stars', url: '/work?tool=qwen' },
  ];

  // Help documents - titles in different languages
  const helpDocs = [
    {
      id: 'getting-started',
      title:
        language === 'zh'
          ? '快速上手指南'
          : language === 'ja'
            ? 'クイックスタートガイド'
            : language === 'ko'
              ? '빠른 시작 가이드'
              : 'Getting Started',
      icon: 'bi-book',
    },
    {
      id: 'prompts-guide',
      title:
        language === 'zh'
          ? '提示词指南'
          : language === 'ja'
            ? 'プロンプトガイド'
            : language === 'ko'
              ? '프롬프트 가이드'
              : 'Prompts Guide',
      icon: 'bi-file-text',
    },
    {
      id: 'keyboard-shortcuts',
      title:
        language === 'zh'
          ? '键盘快捷键'
          : language === 'ja'
            ? 'キーボードショートカット'
            : language === 'ko'
              ? '키보드 단축키'
              : 'Keyboard Shortcuts',
      icon: 'bi-keyboard',
    },
    {
      id: 'faq',
      title:
        language === 'zh'
          ? '常见问题'
          : language === 'ja'
            ? 'よくある質問'
            : language === 'ko'
              ? '자주 묻는 질문'
              : 'FAQ',
      icon: 'bi-question-circle',
    },
  ];

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

  const handleToolClick = (url: string) => {
    window.location.href = url;
  };

  const handleDocClick = (docId: string) => {
    setSelectedDocId(docId);
    setDocViewerOpen(true);
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
      await copyPromptMutation.mutateAsync(prompt.id);
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

  if (collapsed) {
    return null;
  }

  // Prompts Tab Content
  const PromptsContent = (
    <div className="assist-prompts">
      {/* Search Box */}
      <div className="prompt-search mb-2">
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
        <div className="prompt-categories mb-2">
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
                  {/* Fill variables button */}
                  <button
                    className={`prompt-action-btn ${hasRequiredVariables(prompt) ? 'active' : 'disabled'}`}
                    onClick={(e) => {
                      e.stopPropagation();
                      handlePromptClick(prompt);
                    }}
                    title={t('fillVariables', language) || 'Fill variables'}
                    disabled={false}
                  >
                    <i className="bi bi-input-cursor-text" />
                  </button>

                  {/* Copy button */}
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
  );

  // Tools Tab Content
  const ToolsContent = (
    <div className="assist-tools">
      <ul className="assist-items list-unstyled">
        {aiTools.map((tool) => (
          <li key={tool.id}>
            <button
              className="assist-item assist-item-clickable"
              onClick={() => handleToolClick(tool.url)}
            >
              <i className={`bi ${tool.icon} me-2`} />
              <span>{tool.name}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );

  // Docs Tab Content
  const DocsContent = (
    <div className="assist-docs">
      <ul className="assist-items list-unstyled">
        {helpDocs.map((doc) => (
          <li key={doc.id}>
            <button
              className="assist-item assist-item-clickable"
              onClick={() => handleDocClick(doc.id)}
            >
              <i className={`bi ${doc.icon} me-2`} />
              <span>{doc.title}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );

  // Tabs configuration
  const tabs = [
    {
      id: 'prompts',
      label: t('prompts', language),
      icon: <i className="bi bi-file-text" />,
      content: PromptsContent,
    },
    {
      id: 'tools',
      label: t('tools', language),
      icon: <i className="bi bi-tools" />,
      content: ToolsContent,
    },
    {
      id: 'docs',
      label: t('docs', language),
      icon: <i className="bi bi-book" />,
      content: DocsContent,
    },
  ];

  return (
    <div className="assist-panel">
      <SimpleTabs tabs={tabs} defaultTab={activeTab} onTabChange={setActiveTab} />
      <DocumentViewer
        isOpen={docViewerOpen}
        onClose={() => setDocViewerOpen(false)}
        docId={selectedDocId}
      />
      <PromptDetailModal
        isOpen={promptModalOpen}
        onClose={() => setPromptModalOpen(false)}
        prompt={selectedPrompt}
      />
    </div>
  );
};
