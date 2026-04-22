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
import './DocumentViewer.css';

interface DocumentViewerProps {
  isOpen: boolean;
  onClose: () => void;
  docId: string;
}

const documents: Record<string, Record<string, string>> = {
  'getting-started': { en: gettingStartedEn, zh: gettingStartedZh, ja: gettingStartedJa, ko: gettingStartedKo },
  'prompts-guide': { en: '# Prompts Guide\n\nComing soon...', zh: '# 提示词指南\n\n即将推出...' },
  'keyboard-shortcuts': { en: '# Keyboard Shortcuts\n\nComing soon...', zh: '# 键盘快捷键\n\n即将推出...' },
  'faq': { en: '# FAQ\n\nComing soon...', zh: '# 常见问题\n\n即将推出...' },
};

// Document titles in different languages
const docTitles: Record<string, Record<string, string>> = {
  'getting-started': {
    en: 'Getting Started',
    zh: '快速上手指南',
    ja: 'クイックスタートガイド',
    ko: '빠른 시작 가이드',
  },
};

export const DocumentViewer: React.FC<DocumentViewerProps> = ({ isOpen, onClose, docId }) => {
  const language = useLanguage();
  const doc = documents[docId];
  const content = doc ? (doc[language] || doc['en'] || '') : (language === 'zh' ? '# 文档未找到' : '# Document Not Found');

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
          <ReactMarkdown remarkPlugins={[remarkGfm]} components={{
            h1: ({ children }) => <h1 className="doc-heading-1">{children}</h1>,
            h2: ({ children }) => <h2 className="doc-heading-2">{children}</h2>,
            h3: ({ children }) => <h3 className="doc-heading-3">{children}</h3>,
            p: ({ children }) => <p className="doc-paragraph">{children}</p>,
            ul: ({ children }) => <ul className="doc-list">{children}</ul>,
            li: ({ children }) => <li className="doc-list-item">{children}</li>,
            table: ({ children }) => <div className="doc-table-wrapper"><table className="doc-table">{children}</table></div>,
            th: ({ children }) => <th className="doc-table-header">{children}</th>,
            td: ({ children }) => <td className="doc-table-cell">{children}</td>,
            blockquote: ({ children }) => <blockquote className="doc-quote">{children}</blockquote>,
            code: ({ className, children, ...props }) => !className ? <code className="doc-code-inline" {...props}>{children}</code> : <code className={cn('doc-code-block', className)} {...props}>{children}</code>,
            pre: ({ children }) => <pre className="doc-pre">{children}</pre>,
            a: ({ href, children }) => <a className="doc-link" href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
            hr: () => <hr className="doc-divider" />,
            strong: ({ children }) => <strong className="doc-strong">{children}</strong>,
          }}>{content}</ReactMarkdown>
        </div>
      </div>
    </Modal>
  );
};

export default DocumentViewer;