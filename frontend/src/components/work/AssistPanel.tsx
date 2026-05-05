/**
 * AssistPanel Component - Right panel for Work Mode
 *
 * Features:
 * - Quick access to prompts
 * - AI tools shortcuts
 * - Help documentation
 */

import React, { useState, useEffect } from 'react';
import { useNavigate } from 'react-router-dom';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { promptsApi } from '@/api';
import type { PromptTemplate } from '@/api';
import { Loading, EmptyState, SimpleTabs, useToast } from '@/components/common';
import { DocumentViewer } from './DocumentViewer';
import './DocumentViewer.css';

interface AssistPanelProps {
  collapsed?: boolean;
}

export const AssistPanel: React.FC<AssistPanelProps> = ({ collapsed = false }) => {
  const language = useLanguage();
  const navigate = useNavigate();
  const toast = useToast();
  const [activeTab, setActiveTab] = useState('prompts');
  const [prompts, setPrompts] = useState<PromptTemplate[]>([]);
  const [promptsLoading, setPromptsLoading] = useState(true);
  const [docViewerOpen, setDocViewerOpen] = useState(false);
  const [selectedDocId, setSelectedDocId] = useState<string>('');

  // Fetch prompts
  useEffect(() => {
    const fetchPrompts = async () => {
      try {
        const result = await promptsApi.list({ page: 1, limit: 10 });
        setPrompts(result.templates);
      } catch (err) {
        console.error('Failed to fetch prompts:', err);
      } finally {
        setPromptsLoading(false);
      }
    };
    fetchPrompts();
  }, []);

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

  const handleToolClick = (url: string) => {
    navigate(url);
  };

  const handleDocClick = (docId: string) => {
    setSelectedDocId(docId);
    setDocViewerOpen(true);
  };

  const handleCopyPrompt = async (content: string, promptName: string) => {
    try {
      await navigator.clipboard.writeText(content);
      toast.success(t('copied', language) || 'Copied!', promptName);
    } catch (err) {
      console.error('Failed to copy:', err);
      toast.error(t('copyFailed', language) || 'Copy failed', promptName);
    }
  };

  const handlePromptClick = (promptId: number) => {
    // Open in new window to avoid unloading the workspace iframe
    window.open(`/work/prompts?highlight=${promptId}`, '_blank');
  };

  if (collapsed) {
    return null;
  }

  // Prompts Tab Content
  const PromptsContent = (
    <div className="assist-prompts">
      {promptsLoading ? (
        <Loading size="sm" text={t('loading', language)} />
      ) : prompts.length > 0 ? (
        <ul className="assist-items list-unstyled">
          {prompts.slice(0, 5).map((prompt) => (
            <li key={prompt.id}>
              <div
                className="assist-item assist-item-clickable"
                onClick={() => handlePromptClick(prompt.id)}
                title={t('clickToView', language) || 'Click to view details'}
              >
                <div className="assist-item-header">
                  <span className="assist-item-title">{prompt.name}</span>
                  <button
                    className="btn btn-sm btn-link p-0"
                    onClick={(e) => {
                      e.stopPropagation();
                      handleCopyPrompt(prompt.content, prompt.name);
                    }}
                    title={t('copy', language)}
                  >
                    <i className="bi bi-clipboard" />
                  </button>
                </div>
                {prompt.category && <span className="badge bg-secondary">{prompt.category}</span>}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState icon="bi-file-text" title={t('noPromptsFound', language)} />
      )}
      <button
        className="btn btn-link btn-sm mt-2"
        onClick={() => window.open('/work/prompts', '_blank')}
      >
        <i className="bi bi-arrow-right me-1" />
        {t('viewAll', language)}
      </button>
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
              <i className={cn('bi', tool.icon, 'me-2')} />
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
              <i className={cn('bi', doc.icon, 'me-2')} />
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
    </div>
  );
};
