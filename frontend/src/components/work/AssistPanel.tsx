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

  // Help documents
  const helpDocs = [
    { id: 'getting-started', title: 'Getting Started', icon: 'bi-book' },
    { id: 'prompts-guide', title: 'Prompts Guide', icon: 'bi-file-text' },
    { id: 'keyboard-shortcuts', title: 'Keyboard Shortcuts', icon: 'bi-keyboard' },
    { id: 'faq', title: 'FAQ', icon: 'bi-question-circle' },
  ];

  const handleToolClick = (url: string) => {
    navigate(url);
  };

  const handleDocClick = (_docId: string, docTitle: string) => {
    // Show toast notification (help pages coming soon)
    toast.info(docTitle, t('comingSoon', language) || 'Coming soon...');
  };

  const handleCopyPrompt = async (content: string) => {
    try {
      await navigator.clipboard.writeText(content);
    } catch (err) {
      console.error('Failed to copy:', err);
    }
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
              <div className="assist-item">
                <div className="assist-item-header">
                  <span className="assist-item-title">{prompt.name}</span>
                  <button
                    className="btn btn-sm btn-link p-0"
                    onClick={() => handleCopyPrompt(prompt.content)}
                    title={t('copy', language)}
                  >
                    <i className="bi bi-clipboard" />
                  </button>
                </div>
                {prompt.category && (
                  <span className="badge bg-secondary">{prompt.category}</span>
                )}
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyState
          icon="bi-file-text"
          title={t('noPromptsFound', language)}
        />
      )}
      <button
        className="btn btn-link btn-sm mt-2"
        onClick={() => navigate('/work/prompts')}
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
              onClick={() => handleDocClick(doc.id, doc.title)}
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
      <toast.ToastContainer />
    </div>
  );
};