/**
 * Tests for i18n module
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { t, setLanguage, getLanguage, initLanguage } from './index';

describe('i18n', () => {
  beforeEach(() => {
    vi.stubGlobal('navigator', { language: 'en-US' });
    vi.stubGlobal('localStorage', {
      getItem: vi.fn(() => null),
      setItem: vi.fn(),
      removeItem: vi.fn(),
      clear: vi.fn(),
    });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  describe('t (translate)', () => {
    it('should return English translation by default', () => {
      expect(t('loading')).toBe('Loading...');
      expect(t('error')).toBe('Error');
      expect(t('dashboard')).toBe('Dashboard');
    });

    it('should return key if translation not found', () => {
      expect(t('nonexistent.key')).toBe('nonexistent.key');
    });

    it('should translate to specified language', () => {
      expect(t('loading', 'zh')).toBe('加载中...');
      expect(t('loading', 'ja')).toBe('読み込み中...');
      expect(t('loading', 'ko')).toBe('로딩 중...');
    });
  });

  describe('setLanguage', () => {
    it('should change current language', () => {
      setLanguage('zh');
      expect(getLanguage()).toBe('zh');
      expect(t('loading')).toBe('加载中...');
    });

    it('should save language to localStorage', () => {
      setLanguage('ja');
      expect(localStorage.setItem).toHaveBeenCalledWith('language', 'ja');
    });
  });

  describe('getLanguage', () => {
    it('should return current language', () => {
      setLanguage('ko');
      expect(getLanguage()).toBe('ko');
    });
  });

  describe('initLanguage', () => {
    it('should use saved language if available', () => {
      vi.mocked(localStorage.getItem).mockReturnValue('zh');

      initLanguage();

      expect(getLanguage()).toBe('zh');
    });

    it('should use browser language if no saved language', () => {
      vi.stubGlobal('navigator', { language: 'ja-JP' });
      vi.mocked(localStorage.getItem).mockReturnValue(null);

      initLanguage();

      expect(getLanguage()).toBe('ja');
    });

    it('should fallback to English for unsupported languages', () => {
      vi.stubGlobal('navigator', { language: 'fr-FR' });
      vi.mocked(localStorage.getItem).mockReturnValue(null);

      initLanguage();

      expect(getLanguage()).toBe('en');
    });
  });

  describe('translations completeness', () => {
    const languages = ['en', 'zh', 'ja', 'ko'] as const;
    const essentialKeys = [
      'loading',
      'error',
      'retry',
      'refresh',
      'dashboard',
      'messages',
      'analysis',
      'login',
      'logout',
    ];

    it.each(languages)('should have all essential keys in %s', (lang) => {
      essentialKeys.forEach((key) => {
        const translation = t(key, lang);
        expect(translation).not.toBe(key);
      });
    });
  });
});
