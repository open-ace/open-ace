import React from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { useLanguage } from '@/store';
import { Modal } from '@/components/common';
import { cn } from '@/utils';
import gettingStartedEn from './docs/getting-started-en.md?raw';
import gettingStartedZh from './docs/getting-started-zh.md?raw';
import gettingStartedJa from './docs/getting-started-ja.md?raw';
import gettingStartedKo from './docs/getting-started-ko.md?raw';
import promptsGuideEn from './docs/prompts-guide-en.md?raw';
import promptsGuideZh from './docs/prompts-guide-zh.md?raw';
import promptsGuideJa from './docs/prompts-guide-ja.md?raw';
import promptsGuideKo from './docs/prompts-guide-ko.md?raw';
import './DocumentViewer.css';

// Helper function to generate heading id from children
const generateHeadingId = (children: React.ReactNode): string => {
  const text = React.Children.toArray(children)
    .map((child) => {
      if (typeof child === 'string') return child;
      if (typeof child === 'number') return String(child);
      return '';
    })
    .join('')
    .trim();
  
  // Generate slug: lowercase for English, keep original for CJK
  return text.toLowerCase().replace(/\s+/g, '-').replace(/[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af-]/g, '') || text;
};

interface DocumentViewerProps {
  isOpen: boolean;
  onClose: () => void;
  docId: string;
}

const documents: Record<string, Record<string, string>> = {
  'getting-started': {
    en: gettingStartedEn,
    zh: gettingStartedZh,
    ja: gettingStartedJa,
    ko: gettingStartedKo,
  },
  'prompts-guide': {
    en: promptsGuideEn,
    zh: promptsGuideZh,
    ja: promptsGuideJa,
    ko: promptsGuideKo,
  },
  'keyboard-shortcuts': {
    en: '# Keyboard Shortcuts\n\nComing soon...',
    zh: '# 键盘快捷键\n\n即将推出...',
    ja: '# キーボードショートカット\n\nComing soon...',
    ko: '# 키보드 단축키\n\nComing soon...',
  },
  faq: {
    en: '# FAQ\n\nComing soon...',
    zh: '# 常见问题\n\n即将推出...',
    ja: '# よくある質問\n\nComing soon...',
    ko: '# 자주 묻는 질문\n\nComing soon...',
  },
};

// Document titles in different languages
const docTitles: Record<string, Record<string, string>> = {
  'getting-started': {
    en: 'Getting Started',
    zh: '快速上手指南',
    ja: 'クイックスタートガイド',
    ko: '빠른 시작 가이드',
  },
  'prompts-guide': {
    en: 'Prompts Guide',
    zh: '提示词指南',
    ja: 'プロンプトガイド',
    ko: '프롬프트 가이드',
  },
  'keyboard-shortcuts': {
    en: 'Keyboard Shortcuts',
    zh: '键盘快捷键',
    ja: 'キーボードショートカット',
    ko: '키보드 단축키',
  },
  faq: {
    en: 'FAQ',
    zh: '常见问题',
    ja: 'よくある質問',
    ko: '자주 묻는 질문',
  },
};

export const DocumentViewer: React.FC<DocumentViewerProps> = ({ isOpen, onClose, docId }) => {
  const language = useLanguage();
  const doc = documents[docId];
  const content = doc
    ? doc[language] || doc['en'] || ''
    : language === 'zh'
      ? '# 文档未找到'
      : '# Document Not Found';

  // Get title based on docId and language
  const getTitle = () => {
    const titles = docTitles[docId];
    if (titles) {
      return titles[language] || titles['en'] || docId;
    }
    return docId;
  };

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={getTitle()} size="lg">
      <div className="document-viewer">
        <div className="document-content">
          <ReactMarkdown
            remarkPlugins={[remarkGfm]}
            components={{
              h1: ({ children }) => (
                <h1 className="doc-heading-1" id={generateHeadingId(children)}>
                  {children}
                </h1>
              ),
              h2: ({ children }) => (
                <h2 className="doc-heading-2" id={generateHeadingId(children)}>
                  {children}
                </h2>
              ),
              h3: ({ children }) => (
                <h3 className="doc-heading-3" id={generateHeadingId(children)}>
                  {children}
                </h3>
              ),
              p: ({ children }) => <p className="doc-paragraph">{children}</p>,
              ul: ({ children }) => <ul className="doc-list">{children}</ul>,
              li: ({ children }) => <li className="doc-list-item">{children}</li>,
              table: ({ children }) => (
                <div className="doc-table-wrapper">
                  <table className="doc-table">{children}</table>
                </div>
              ),
              th: ({ children }) => <th className="doc-table-header">{children}</th>,
              td: ({ children }) => <td className="doc-table-cell">{children}</td>,
              blockquote: ({ children }) => (
                <blockquote className="doc-quote">{children}</blockquote>
              ),
              code: ({ className, children, ...props }) =>
                !className ? (
                  <code className="doc-code-inline" {...props}>
                    {children}
                  </code>
                ) : (
                  <code className={cn('doc-code-block', className)} {...props}>
                    {children}
                  </code>
                ),
              pre: ({ children }) => <pre className="doc-pre">{children}</pre>,
              a: ({ href, children }) => {
                // Handle anchor links for internal navigation
                if (href && href.startsWith('#')) {
                  const handleClick = (e: React.MouseEvent) => {
                    e.preventDefault();
                    const targetId = href.slice(1);
                    const element = document.getElementById(targetId);
                    if (element) {
                      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
                    }
                  };
                  return (
                    <a className="doc-link doc-anchor-link" href={href} onClick={handleClick}>
                      {children}
                    </a>
                  );
                }
                // External links
                return (
                  <a className="doc-link" href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                );
              },
              hr: () => <hr className="doc-divider" />,
              strong: ({ children }) => <strong className="doc-strong">{children}</strong>,
            }}
          >
            {content}
          </ReactMarkdown>
        </div>
      </div>
    </Modal>
  );
};

export default DocumentViewer;
