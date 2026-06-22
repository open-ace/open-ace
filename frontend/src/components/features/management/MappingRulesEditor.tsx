/**
 * MappingRulesEditor Component - Edit auto-mapping rules for a user
 */

import React, { useState, useEffect, useCallback } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Button, TextInput, Modal, Badge, useToast } from '@/components/common';
import { mappingRulesApi, type MappingRule } from '@/api/mappingRules';

// Match types with display names
const MATCH_TYPES = [
  { value: 'exact', display: 'Exact', display_zh: '完全匹配' },
  { value: 'prefix', display: 'Prefix', display_zh: '前缀匹配' },
  { value: 'suffix', display: 'Suffix', display_zh: '后缀匹配' },
  { value: 'contains', display: 'Contains', display_zh: '包含匹配' },
  { value: 'regex', display: 'Regex', display_zh: '正则表达式' },
];

interface MappingRulesEditorProps {
  userId: number;
  username?: string;
  onChange?: () => void;
}

export const MappingRulesEditor: React.FC<MappingRulesEditorProps> = ({
  userId,
  username,
  onChange
}) => {
  const language = useLanguage();
  const [rules, setRules] = useState<MappingRule[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [showAddModal, setShowAddModal] = useState(false);
  const [isGenerating, setIsGenerating] = useState(false);
  const [newRule, setNewRule] = useState({
    pattern: '',
    match_type: 'prefix',
    tool_type: '',
    priority: 10,
    is_auto: true,
    description: '',
  });
  const [isAdding, setIsAdding] = useState(false);
  const toast = useToast();

  const loadRules = useCallback(async () => {
    setIsLoading(true);
    try {
      const data = await mappingRulesApi.getUserRules(userId);
      setRules(data);
    } catch (err) {
      console.error('Failed to load mapping rules:', err);
    } finally {
      setIsLoading(false);
    }
  }, [userId]);

  useEffect(() => {
    loadRules();
  }, [loadRules]);

  const handleGenerateDefaultRules = async () => {
    setIsGenerating(true);
    try {
      const generated = await mappingRulesApi.generateDefaultRules(userId);
      setRules(generated);
      toast.success(
        language === 'zh'
          ? `已生成 ${generated.length} 条默认规则`
          : `Generated ${generated.length} default rules`
      );
      onChange?.();
    } catch (err) {
      console.error('Failed to generate default rules:', err);
      toast.error(
        language === 'zh'
          ? '生成默认规则失败'
          : 'Failed to generate default rules'
      );
    } finally {
      setIsGenerating(false);
    }
  };

  const handleAddRule = async () => {
    if (!newRule.pattern.trim()) {
      toast.error(language === 'zh' ? '模式为必填项' : 'Pattern is required');
      return;
    }

    setIsAdding(true);
    try {
      await mappingRulesApi.createRule({
        user_id: userId,
        pattern: newRule.pattern,
        match_type: newRule.match_type,
        tool_type: newRule.tool_type || undefined,
        priority: newRule.priority,
        is_auto: newRule.is_auto,
        description: newRule.description || undefined,
      });
      setNewRule({
        pattern: '',
        match_type: 'prefix',
        tool_type: '',
        priority: 10,
        is_auto: true,
        description: '',
      });
      setShowAddModal(false);
      loadRules();
      onChange?.();
      toast.success(language === 'zh' ? '规则添加成功' : 'Rule added successfully');
    } catch (err) {
      console.error('Failed to add rule:', err);
      toast.error(language === 'zh' ? '规则添加失败' : 'Failed to add rule');
    } finally {
      setIsAdding(false);
    }
  };

  const handleDeleteRule = async (id: number) => {
    if (!window.confirm(language === 'zh' ? '确认删除此规则？' : 'Delete this rule?')) return;

    try {
      await mappingRulesApi.deleteRule(id);
      loadRules();
      onChange?.();
    } catch (err) {
      console.error('Failed to delete rule:', err);
    }
  };

  const handleToggleRuleActive = async (rule: MappingRule) => {
    try {
      await mappingRulesApi.updateRule(rule.id, { is_active: !rule.is_active });
      loadRules();
    } catch (err) {
      console.error('Failed to toggle rule:', err);
    }
  };

  const getMatchTypeDisplay = (matchType: string) => {
    const type = MATCH_TYPES.find((t) => t.value === matchType);
    return language === 'zh' ? type?.display_zh : type?.display;
  };

  if (isLoading) {
    return <div className="text-muted small">{t('loading', language)}</div>;
  }

  return (
    <div className="mapping-rules-editor">
      {/* Current rules */}
      <div className="mb-2">
        {rules.length === 0 ? (
          <span className="text-muted small">
            {language === 'zh' ? '未配置自动映射规则' : 'No auto-mapping rules configured'}
          </span>
        ) : (
          <div className="d-flex align-items-center gap-2 flex-wrap">
            {rules.map((rule) => (
              <Badge
                key={rule.id}
                variant={rule.is_active ? 'primary' : 'secondary'}
                className="d-flex align-items-center gap-1"
                style={{ opacity: rule.is_active ? 1 : 0.6 }}
              >
                <span
                  style={{ cursor: 'pointer' }}
                  onClick={() => handleToggleRuleActive(rule)}
                  title={rule.is_active ? 'Click to disable' : 'Click to enable'}
                >
                  {rule.pattern}
                </span>
                <small className="text-light">
                  ({getMatchTypeDisplay(rule.match_type)})
                </small>
                <button
                  type="button"
                  className="btn-close btn-close-white ms-1"
                  style={{ fontSize: '0.6rem' }}
                  onClick={() => handleDeleteRule(rule.id)}
                  title={t('delete', language)}
                />
              </Badge>
            ))}
          </div>
        )}
      </div>

      {/* Action buttons */}
      <div className="d-flex gap-2">
        <Button variant="outline-primary" size="sm" onClick={() => setShowAddModal(true)}>
          <i className="bi bi-plus-lg me-1" />
          {language === 'zh' ? '添加规则' : 'Add Rule'}
        </Button>
        <Button
          variant="outline-secondary"
          size="sm"
          onClick={handleGenerateDefaultRules}
          loading={isGenerating}
          disabled={isGenerating}
        >
          <i className="bi bi-magic me-1" />
          {language === 'zh' ? '生成默认规则' : 'Generate Default'}
        </Button>
      </div>

      {/* Add rule modal */}
      <Modal
        isOpen={showAddModal}
        onClose={() => setShowAddModal(false)}
        title={language === 'zh' ? '添加映射规则' : 'Add Mapping Rule'}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={() => setShowAddModal(false)}>
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleAddRule}
              loading={isAdding}
              disabled={isAdding}
            >
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleAddRule();
          }}
        >
          <div className="mb-3">
            <label className="form-label">
              {language === 'zh' ? '匹配模式' : 'Pattern'}
            </label>
            <TextInput
              value={newRule.pattern}
              onChange={(value) => setNewRule({ ...newRule, pattern: value })}
              placeholder={
                language === 'zh'
                  ? `例如: ${username || 'user'}-* (匹配用户名开头的账号)`
                  : `e.g., ${username || 'user'}-*`
              }
            />
            <small className="text-muted">
              {language === 'zh'
                ? '使用 * 作为通配符，如 user-* 匹配所有以 user- 开头的账号'
                : 'Use * as wildcard, e.g., user-* matches all accounts starting with user-'}
            </small>
          </div>

          <div className="mb-3">
            <label className="form-label">
              {language === 'zh' ? '匹配类型' : 'Match Type'}
            </label>
            <select
              className="form-select"
              value={newRule.match_type}
              onChange={(e) => setNewRule({ ...newRule, match_type: e.target.value })}
            >
              {MATCH_TYPES.map((type) => (
                <option key={type.value} value={type.value}>
                  {language === 'zh' ? type.display_zh : type.display}
                </option>
              ))}
            </select>
          </div>

          <div className="mb-3">
            <label className="form-label">
              {language === 'zh' ? '优先级' : 'Priority'}
            </label>
            <input
              type="number"
              className="form-control"
              value={newRule.priority}
              onChange={(e) =>
                setNewRule({ ...newRule, priority: parseInt(e.target.value) || 0 })
              }
              min={0}
              max={100}
            />
            <small className="text-muted">
              {language === 'zh'
                ? '数字越大优先级越高，会优先匹配'
                : 'Higher number = higher priority, matched first'}
            </small>
          </div>

          <div className="mb-3">
            <label className="form-label">
              {language === 'zh' ? '描述' : 'Description'}
            </label>
            <TextInput
              value={newRule.description}
              onChange={(value) => setNewRule({ ...newRule, description: value })}
              placeholder={language === 'zh' ? '可选描述' : 'Optional description'}
            />
          </div>

          <div className="form-check">
            <input
              type="checkbox"
              className="form-check-input"
              checked={newRule.is_auto}
              onChange={(e) => setNewRule({ ...newRule, is_auto: e.target.checked })}
            />
            <label className="form-check-label">
              {language === 'zh'
                ? '自动应用（无需管理员确认）'
                : 'Auto apply (no admin confirmation needed)'}
            </label>
          </div>
        </form>
      </Modal>
    </div>
  );
};