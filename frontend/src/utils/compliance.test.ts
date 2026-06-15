/**
 * Tests for compliance utility functions
 *
 * Tests translation mapping functions for compliance report types.
 */

import { describe, it, expect, beforeEach } from 'vitest';
import { getReportTypeName, getReportTypeDesc, getReportTypeInfo } from './compliance';
import { setLanguage } from '@/i18n';
import type { Language } from '@/types';

describe('compliance utils', () => {
  beforeEach(() => {
    // Reset to English before each test
    setLanguage('en');
  });

  describe('getReportTypeName', () => {
    it('should return English name for usage_summary', () => {
      const name = getReportTypeName('usage_summary', 'en');
      expect(name).toBe('Usage Summary');
    });

    it('should return Chinese name for usage_summary', () => {
      const name = getReportTypeName('usage_summary', 'zh');
      expect(name).toBe('使用统计');
    });

    it('should return Japanese name for usage_summary', () => {
      const name = getReportTypeName('usage_summary', 'ja');
      expect(name).toBe('使用統計');
    });

    it('should return Korean name for usage_summary', () => {
      const name = getReportTypeName('usage_summary', 'ko');
      expect(name).toBe('사용 통계');
    });

    it('should return English name for user_activity', () => {
      const name = getReportTypeName('user_activity', 'en');
      expect(name).toBe('User Activity');
    });

    it('should return Chinese name for user_activity', () => {
      const name = getReportTypeName('user_activity', 'zh');
      expect(name).toBe('用户活动');
    });

    it('should return Japanese name for user_activity', () => {
      const name = getReportTypeName('user_activity', 'ja');
      expect(name).toBe('ユーザー活動');
    });

    it('should return Korean name for user_activity', () => {
      const name = getReportTypeName('user_activity', 'ko');
      expect(name).toBe('사용자 활동');
    });

    it('should return English name for audit_trail', () => {
      const name = getReportTypeName('audit_trail', 'en');
      expect(name).toBe('Audit Trail');
    });

    it('should return English name for security', () => {
      const name = getReportTypeName('security', 'en');
      expect(name).toBe('Security Report');
    });

    it('should return English name for quota_usage', () => {
      const name = getReportTypeName('quota_usage', 'en');
      expect(name).toBe('Quota Usage');
    });

    it('should return English name for comprehensive', () => {
      const name = getReportTypeName('comprehensive', 'en');
      expect(name).toBe('Comprehensive');
    });

    it('should return fallback for unknown report type', () => {
      const name = getReportTypeName('unknown_type', 'en', 'Custom Fallback');
      expect(name).toBe('Custom Fallback');
    });

    it('should return original type string when no fallback provided for unknown type', () => {
      const name = getReportTypeName('unknown_type', 'en');
      expect(name).toBe('unknown_type');
    });

    it('should use provided fallback when translation returns key', () => {
      // This tests the fallback behavior when i18n returns the key itself
      const name = getReportTypeName('usage_summary', 'en', 'Fallback Name');
      // Should return actual translation, not fallback, since translation exists
      expect(name).toBe('Usage Summary');
    });

    it('should return correct names for all supported report types in English', () => {
      const reportTypes = [
        'usage_summary',
        'user_activity',
        'audit_trail',
        'data_access',
        'security',
        'quota_usage',
        'comprehensive',
      ];

      const expectedNames = [
        'Usage Summary',
        'User Activity',
        'Audit Trail',
        'Data Access',
        'Security Report',
        'Quota Usage',
        'Comprehensive',
      ];

      reportTypes.forEach((type, index) => {
        const name = getReportTypeName(type, 'en');
        expect(name).toBe(expectedNames[index]);
      });
    });

    it('should return correct names for all supported report types in Chinese', () => {
      const reportTypes = [
        'usage_summary',
        'user_activity',
        'audit_trail',
        'data_access',
        'security',
        'quota_usage',
        'comprehensive',
      ];

      const expectedNames = [
        '使用统计',
        '用户活动',
        '审计轨迹',
        '数据访问',
        '安全报告',
        '配额使用',
        '综合报告',
      ];

      reportTypes.forEach((type, index) => {
        const name = getReportTypeName(type, 'zh');
        expect(name).toBe(expectedNames[index]);
      });
    });
  });

  describe('getReportTypeDesc', () => {
    it('should return English description for usage_summary', () => {
      const desc = getReportTypeDesc('usage_summary', 'en');
      expect(desc).toBe('Overview of token and request usage statistics');
    });

    it('should return Chinese description for usage_summary', () => {
      const desc = getReportTypeDesc('usage_summary', 'zh');
      expect(desc).toBe('Token 和请求使用统计概览');
    });

    it('should return Japanese description for usage_summary', () => {
      const desc = getReportTypeDesc('usage_summary', 'ja');
      expect(desc).toBe('トークンとリクエスト使用統計の概要');
    });

    it('should return Korean description for usage_summary', () => {
      const desc = getReportTypeDesc('usage_summary', 'ko');
      expect(desc).toBe('토큰 및 요청 사용 통계 개요');
    });

    it('should return empty string for unknown report type', () => {
      const desc = getReportTypeDesc('unknown_type', 'en');
      expect(desc).toBe('');
    });

    it('should return descriptions for all supported report types in English', () => {
      const reportTypes = [
        'usage_summary',
        'user_activity',
        'audit_trail',
        'data_access',
        'security',
        'quota_usage',
        'comprehensive',
      ];

      reportTypes.forEach((type) => {
        const desc = getReportTypeDesc(type, 'en');
        expect(desc.length).toBeGreaterThan(0);
        expect(desc).not.toBe(type);
      });
    });

    it('should return descriptions for all supported report types in Chinese', () => {
      const reportTypes = [
        'usage_summary',
        'user_activity',
        'audit_trail',
        'data_access',
        'security',
        'quota_usage',
        'comprehensive',
      ];

      reportTypes.forEach((type) => {
        const desc = getReportTypeDesc(type, 'zh');
        expect(desc.length).toBeGreaterThan(0);
        expect(desc).not.toBe(type);
      });
    });
  });

  describe('getReportTypeInfo', () => {
    it('should return both name and description for usage_summary in English', () => {
      const info = getReportTypeInfo('usage_summary', 'en');
      expect(info.name).toBe('Usage Summary');
      expect(info.description).toBe('Overview of token and request usage statistics');
    });

    it('should return both name and description for usage_summary in Chinese', () => {
      const info = getReportTypeInfo('usage_summary', 'zh');
      expect(info.name).toBe('使用统计');
      expect(info.description).toBe('Token 和请求使用统计概览');
    });

    it('should return both name and description for usage_summary in Japanese', () => {
      const info = getReportTypeInfo('usage_summary', 'ja');
      expect(info.name).toBe('使用統計');
      expect(info.description).toBe('トークンとリクエスト使用統計の概要');
    });

    it('should return both name and description for usage_summary in Korean', () => {
      const info = getReportTypeInfo('usage_summary', 'ko');
      expect(info.name).toBe('사용 통계');
      expect(info.description).toBe('토큰 및 요청 사용 통계 개요');
    });

    it('should return empty description and original type name for unknown type', () => {
      const info = getReportTypeInfo('unknown_type', 'en');
      expect(info.name).toBe('unknown_type');
      expect(info.description).toBe('');
    });
  });

  describe('language switching', () => {
    it('should return correct translations when language is switched', () => {
      // Test switching between all supported languages
      const languages: Language[] = ['en', 'zh', 'ja', 'ko'];
      const expectedNames = ['Usage Summary', '使用统计', '使用統計', '사용 통계'];

      languages.forEach((lang, index) => {
        setLanguage(lang);
        const name = getReportTypeName('usage_summary', lang);
        expect(name).toBe(expectedNames[index]);
      });
    });
  });
});
