import React from 'react';
import { useLanguage } from '@/store';
import { Modal } from '@/components/common';
import { MarkdownContent } from './MarkdownContent';
import gettingStartedEn from './docs/getting-started-en.md?raw';
import gettingStartedZh from './docs/getting-started-zh.md?raw';
import gettingStartedJa from './docs/getting-started-ja.md?raw';
import gettingStartedKo from './docs/getting-started-ko.md?raw';
import promptsGuideEn from './docs/prompts-guide-en.md?raw';
import promptsGuideZh from './docs/prompts-guide-zh.md?raw';
import promptsGuideJa from './docs/prompts-guide-ja.md?raw';
import promptsGuideKo from './docs/prompts-guide-ko.md?raw';
import keyboardShortcutsEn from './docs/keyboard-shortcuts-en.md?raw';
import keyboardShortcutsZh from './docs/keyboard-shortcuts-zh.md?raw';
import keyboardShortcutsJa from './docs/keyboard-shortcuts-ja.md?raw';
import keyboardShortcutsKo from './docs/keyboard-shortcuts-ko.md?raw';
import faqEn from './docs/faq-en.md?raw';
import faqZh from './docs/faq-zh.md?raw';
import faqJa from './docs/faq-ja.md?raw';
import faqKo from './docs/faq-ko.md?raw';
import './DocumentViewer.css';

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
    en: keyboardShortcutsEn,
    zh: keyboardShortcutsZh,
    ja: keyboardShortcutsJa,
    ko: keyboardShortcutsKo,
  },
  faq: {
    en: faqEn,
    zh: faqZh,
    ja: faqJa,
    ko: faqKo,
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
          <MarkdownContent content={content} />
        </div>
      </div>
    </Modal>
  );
};

export default DocumentViewer;
