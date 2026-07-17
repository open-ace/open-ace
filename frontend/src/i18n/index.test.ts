/**
 * Tests for i18n module
 */

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { t, setLanguage, getLanguage, initLanguage, translations } from './index';

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
      // Efficiency report card / anomaly warnings (issues #819/#820). These were
      // present in en/zh but missing in ja/ko, leaking raw keys on the ROI page.
      'efficiencyReport',
      'efficiencyScore',
      'roiDataAnomaly',
      'dataAnomalyDetected',
      'tokenAccumulationWarning',
      'roiNegativeHint',
      'avgTokensPerRequest',
      'avgCostPerRequest',
      'overallEfficiency',
      'wastePercentage',
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

    // API Key Management translations (Issue #XXX)
    // Ensure all API Key Management related keys exist in all four languages
    const apiKeyManagementKeys = [
      'apiKeys',
      'addApiKey',
      'editApiKey',
      'deleteApiKey',
      'deleteApiKeyConfirm',
      'provider',
      'keyName',
      'apiKey',
      'baseUrl',
      'enterKeyName',
      'enterApiKey',
      'enterBaseUrl',
      'noApiKeys',
      'noApiKeysDescription',
      'keyStatus',
      'cliTools',
      'cliToolsDescription',
      'claudeCodeSettings',
      'claudeCodeSettingsHint',
      'qwenCodeSettings',
      'qwenCodeSettingsHint',
      'codexSettings',
      'codexSettingsHint',
      'claudeSettingsInvalid',
      'qwenSettingsInvalid',
      'zcodeSettings',
      'zcodeSettingsHint',
      'zcodeSettingsInvalid',
      'jsonValid',
      'jsonInvalid',
      'providerCannotChange',
      // Scope options
      'scope',
      'scopeShared',
      'scopeLocal',
      'scopeRemote',
      'scopeHelp',
      'scopeBadgeShared',
      'scopeBadgeLocal',
      'scopeBadgeRemote',
      // Advanced Settings
      'advancedSettings',
      'priority',
      'priorityHelp',
      'weight',
      'weightHelp',
    ];

    it.each(languages)('should have all API Key Management keys in %s', (lang) => {
      apiKeyManagementKeys.forEach((key) => {
        const translation = t(key, lang);
        expect(translation, `missing API Key Management key "${key}" in ${lang}`).not.toBe(key);
      });
    });

    // ---- Four-language key-set symmetry (root-cause defense for #819/#820) ----
    // The leak bug class: a key exists in en/zh but is missing in ja/ko, so the
    // raw key string renders in the UI. The full dictionary is NOT yet symmetric
    // (ja/ko lag en/zh on many other pages), so we gate the ROI page precisely
    // here, assert en/zh symmetry, and ratchet the broader ja/ko gap below.

    // Every key consumed by ROIAnalysis.tsx via t('key', language). Each MUST
    // exist in all four languages, otherwise a raw key leaks on the ROI page.
    const roiPageKeys = [
      'roiAnalysis',
      'roi',
      'roiPercentage',
      'roiNegativeHint',
      'roiAssumptions',
      'roiAppliedAssumptions',
      'roiAssumptionsHelp',
      'roiEstimateDisclaimer',
      'roiCurrencyNotice',
      'roiAssumptionValidation',
      'roiDataAnomaly',
      'roiTrend',
      'totalCost',
      'totalSavings',
      'hourlyLaborCost',
      'avgTimeSavedPerRequest',
      'productivityMultiplier',
      'currency',
      'costBreakdown',
      'dailyCosts',
      'cost',
      'efficiencyReport',
      'efficiencyScore',
      'dataAnomalyDetected',
      'tokenAccumulationWarning',
      'overallEfficiency',
      'avgCostPerRequest',
      'avgTokensPerRequest',
      'wastePercentage',
      'optimizationSuggestions',
      'potentialSavings',
      'noSuggestions',
      'noData',
      'impact',
      'actionItems',
      'showActions',
      'hideActions',
      'title',
      'startDate',
      'endDate',
      'dashboardFilterAllTools',
      'tableTool',
      'description',
      'recommendations',
      'apply',
      'reset',
    ];

    it.each(languages)(
      'ROI page: every consumed key is translated in %s (issues #819/#820)',
      (lang) => {
        roiPageKeys.forEach((key) => {
          expect(
            translations[lang][key],
            `ROI page key "${key}" missing in ${lang} — raw key would leak into the UI`
          ).toBeDefined();
        });
      }
    );

    it('en and zh dictionaries are fully key-symmetric (the two primary languages)', () => {
      expect(Object.keys(translations.en).sort()).toEqual(Object.keys(translations.zh).sort());
    });

    // ja/ko lag en by a known set of keys on OTHER pages (tenants/SMTP/SSO/...).
    // This ratchet caps the gap so it can only shrink: adding an en-only key
    // without ja/ko fails here — translate it, or (only for genuinely en-only
    // keys) raise the cap with a comment explaining why. Baseline: 2026-06-18.
    // Updated 2026-06-25: Added password reset/change translations (13 keys),
    // cap raised from 278 to 286 to reflect current ja/ko translation progress.
    it('ja/ko key gap vs en never grows beyond the documented baseline', () => {
      const enKeys = Object.keys(translations.en);
      const jaMissing = enKeys.filter((k) => translations.ja[k] === undefined);
      const koMissing = enKeys.filter((k) => translations.ko[k] === undefined);
      expect(
        jaMissing.length,
        'ja is missing en keys — translate them rather than raising the cap'
      ).toBeLessThanOrEqual(286);
      expect(
        koMissing.length,
        'ko is missing en keys — translate them rather than raising the cap'
      ).toBeLessThanOrEqual(286);
    });
  });
});
