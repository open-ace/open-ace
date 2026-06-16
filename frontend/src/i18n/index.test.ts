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

    it('should interpolate {placeholder} params', () => {
      expect(t('suggestionModelSwitchDesc', 'en', { model: 'gpt-4', avg_tokens: '250' })).toContain(
        'gpt-4'
      );
      expect(
        t('suggestionModelSwitchDesc', 'en', {
          model: 'gpt-4',
          avg_tokens: '250',
          cheaper_model: 'gpt-4o-mini',
          threshold: 500,
        })
      ).toBe(
        'Model gpt-4 is used for short requests (avg 250 tokens). Consider using gpt-4o-mini for tasks under 500 tokens.'
      );
    });

    it('should coerce numeric params to strings', () => {
      const result = t('suggestionToolConsolidationDesc', 'en', { tool_count: 5 });
      expect(result).toBe(
        'Currently using 5 different AI tools. Consolidation may enable volume discounts.'
      );
    });

    it('should leave unknown placeholders intact (never throw)', () => {
      const result = t('suggestionModelSwitchDesc', 'en', { model: 'gpt-4' });
      expect(result).toContain('gpt-4');
      expect(result).toContain('{avg_tokens}');
    });

    it('should behave identically when params is omitted (backward compatible)', () => {
      expect(t('loading', 'en')).toBe('Loading...');
      expect(t('loading')).toBe('Loading...');
    });
  });

  describe('t (ROI suggestion interpolation)', () => {
    // Exercise every suggestion type's full param path across languages to
    // guarantee no literal {placeholder} leaks into the rendered string.
    const cases: Array<{
      lang: 'en' | 'zh' | 'ja' | 'ko';
      type: string;
      params: Record<string, string | number>;
    }> = [
      {
        lang: 'en',
        type: 'model_switch',
        params: { model: 'gpt-4', cheaper_model: 'gpt-4o-mini', avg_tokens: '250', threshold: 500 },
      },
      {
        lang: 'zh',
        type: 'model_switch',
        params: { model: 'gpt-4', cheaper_model: 'gpt-4o-mini', avg_tokens: '250', threshold: 500 },
      },
      {
        lang: 'ja',
        type: 'time_optimization',
        params: { peak_hours: '9, 10, 14', peak_percentage: '68.5' },
      },
      {
        lang: 'ko',
        type: 'quota_adjustment',
        params: { low_usage_count: 7, usage_threshold: 20 },
      },
      {
        lang: 'en',
        type: 'token_optimization',
        params: { output_ratio: '12.3' },
      },
      {
        lang: 'zh',
        type: 'tool_consolidation',
        params: { tool_count: 4 },
      },
    ];

    const descKey: Record<string, string> = {
      model_switch: 'suggestionModelSwitchDesc',
      time_optimization: 'suggestionTimeOptimizationDesc',
      quota_adjustment: 'suggestionQuotaAdjustmentDesc',
      token_optimization: 'suggestionTokenOptimizationDesc',
      tool_consolidation: 'suggestionToolConsolidationDesc',
    };

    it.each(cases)(
      'renders $type desc in $lang with no residual placeholder',
      ({ type, lang, params }) => {
        const result = t(descKey[type], lang, params);
        expect(result).not.toMatch(/\{[a-z_]+\}/);
      }
    );
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

    it('should default to English if no saved language (browser language not auto-detected)', () => {
      // PR #710: Removed browser language auto-detection to avoid conflict with user settings
      vi.stubGlobal('navigator', { language: 'ja-JP' });
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

    // Regression gate: ROI optimization suggestions must exist in ALL four
    // languages. This is exactly the bug class that caused issue #819's
    // partial fix (suggestion keys added only to en/zh, missing ja/ko).
    const roiKeys = [
      'suggestionModelSwitchTitle',
      'suggestionModelSwitchDesc',
      'suggestionUsagePatternTitle',
      'suggestionUsagePatternDesc',
      'suggestionQuotaAdjustmentTitle',
      'suggestionQuotaAdjustmentDesc',
      'suggestionToolConsolidationTitle',
      'suggestionToolConsolidationDesc',
      'suggestionTimeOptimizationTitle',
      'suggestionTimeOptimizationDesc',
      'suggestionTokenOptimizationTitle',
      'suggestionTokenOptimizationDesc',
      'priorityHigh',
      'priorityMedium',
      'priorityLow',
      'impactHigh',
      'impactMedium',
      'impactLow',
      'recommendationLowEfficiency',
      'recommendationLowOutputRatio',
      'recommendationHighCostPerRequest',
      'recommendationHighAvgTokens',
      'recommendationHighModelConcentration',
      'recommendationHealthy',
      'optimizationSuggestions',
      'potentialSavings',
      'noSuggestions',
      'impact',
      'actionItems',
      'showActions',
      'hideActions',
    ];

    it.each(languages)('should have all ROI suggestion keys in %s', (lang) => {
      roiKeys.forEach((key) => {
        const translation = t(key, lang);
        expect(translation, `missing ROI key "${key}" in ${lang}`).not.toBe(key);
      });
    });

    // Action item keys (5 produced suggestion types x 3 items) across all langs.
    const actionTypes = [
      'ModelSwitch',
      'TimeOptimization',
      'QuotaAdjustment',
      'ToolConsolidation',
      'TokenOptimization',
    ];
    it.each(languages)('should have all action item keys in %s', (lang) => {
      actionTypes.forEach((prefix) => {
        [1, 2, 3].forEach((n) => {
          const key = `action${prefix}${n}`;
          const translation = t(key, lang);
          expect(translation, `missing action key "${key}" in ${lang}`).not.toBe(key);
        });
      });
    });

    // Common request labels used across multiple dashboards/charts; must exist in
    // all four languages or the raw key (e.g. "requests") leaks into the UI.
    const commonLabelKeys = ['requests', 'requestsMessages'];
    it.each(languages)('should have common request labels in %s', (lang) => {
      commonLabelKeys.forEach((key) => {
        const translation = t(key, lang);
        expect(translation, `missing common label "${key}" in ${lang}`).not.toBe(key);
      });
    });
  });
});
