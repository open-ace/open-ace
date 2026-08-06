/**
 * AutoMappingPanel Component - UI for auto-mapping tool accounts
 *
 * Issue #2374: Provides frontend UI entry points for auto-mapping APIs:
 * - Run auto-mapping (with dry-run preview)
 * - View mapping statistics
 * - Suggest mapping for unmapped accounts
 * - Manual map accounts
 * - Test match rules
 */

import React, { useState, useCallback, useEffect } from 'react';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { Button, Modal, Badge, useToast, useConfirm, Loading } from '@/components/common';
import {
  mappingRulesApi,
  type MappingStats,
  type AutoMappingResult,
  type UnmappedAccount,
  type MappingSuggestion,
  type MatchTestResult,
} from '@/api/mappingRules';
import type { AdminUser } from '@/api';

interface AutoMappingPanelProps {
  users?: AdminUser[];
  onChange?: () => void;
}

export const AutoMappingPanel: React.FC<AutoMappingPanelProps> = ({ users, onChange }) => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();

  const [isExpanded, setIsExpanded] = useState(false);
  const [stats, setStats] = useState<MappingStats | null>(null);
  const [isLoadingStats, setIsLoadingStats] = useState(false);
  const [isAutoMapping, setIsAutoMapping] = useState(false);
  const [autoMapPreview, setAutoMapPreview] = useState<{
    mappings: AutoMappingResult[];
    unmapped_count: number;
    mapped_count: number;
  } | null>(null);
  const [showUnmappedModal, setShowUnmappedModal] = useState(false);
  const [unmappedAccounts, setUnmappedAccounts] = useState<UnmappedAccount[]>([]);
  const [isLoadingUnmapped, setIsLoadingUnmapped] = useState(false);
  const [suggestions, setSuggestions] = useState<Record<string, MappingSuggestion | null>>({});
  const [loadingSuggestions, setLoadingSuggestions] = useState<Set<string>>(new Set());
  const [manualMapTarget, setManualMapTarget] = useState<string | null>(null);
  const [selectedUserId, setSelectedUserId] = useState<number | ''>('');
  const [isManualMapping, setIsManualMapping] = useState(false);
  const [showTestMatchModal, setShowTestMatchModal] = useState(false);
  const [testMatchInput, setTestMatchInput] = useState('');
  const [testMatchResult, setTestMatchResult] = useState<MatchTestResult | null>(null);
  const [isTestingMatch, setIsTestingMatch] = useState(false);

  const loadStats = useCallback(async () => {
    setIsLoadingStats(true);
    try {
      const data = await mappingRulesApi.getMappingStats();
      setStats(data);
    } catch (err) {
      console.error('Failed to load mapping stats:', err);
    } finally {
      setIsLoadingStats(false);
    }
  }, []);

  useEffect(() => {
    if (isExpanded) {
      loadStats();
    }
  }, [isExpanded, loadStats]);

  const handleRunAutoMap = async () => {
    setIsAutoMapping(true);
    try {
      // Step 1: Dry-run preview
      const preview = await mappingRulesApi.runAutoMapping(true);
      setAutoMapPreview(preview);

      if (preview.mapped_count === 0) {
        toast.info(
          language === 'zh'
            ? '没有可自动映射的账号'
            : 'No accounts can be auto-mapped'
        );
        setAutoMapPreview(null);
        return;
      }

      // Step 2: Confirm with user
      const message =
        language === 'zh'
          ? `将匹配 ${preview.mapped_count} 个账号到用户，剩余 ${preview.unmapped_count} 个无法自动匹配。确认执行？`
          : `Will match ${preview.mapped_count} accounts to users, ${preview.unmapped_count} remaining unmapped. Confirm?`;

      if (!(await confirm({ message, variant: 'primary' }))) {
        setAutoMapPreview(null);
        return;
      }

      // Step 3: Execute actual mapping
      const result = await mappingRulesApi.runAutoMapping(false);
      toast.success(
        language === 'zh'
          ? `自动映射完成：已映射 ${result.mapped_count} 个账号`
          : `Auto-mapping complete: ${result.mapped_count} accounts mapped`
      );
      setAutoMapPreview(null);
      await loadStats();
      onChange?.();
    } catch (err) {
      console.error('Failed to run auto-mapping:', err);
      toast.error(
        language === 'zh' ? '自动映射失败' : 'Failed to run auto-mapping'
      );
    } finally {
      setIsAutoMapping(false);
    }
  };

  const loadUnmappedAccounts = useCallback(async () => {
    setIsLoadingUnmapped(true);
    try {
      const data = await mappingRulesApi.getUnmappedAccounts();
      setUnmappedAccounts(data);
      setSuggestions({});
    } catch (err) {
      console.error('Failed to load unmapped accounts:', err);
    } finally {
      setIsLoadingUnmapped(false);
    }
  }, []);

  const handleShowUnmapped = () => {
    setShowUnmappedModal(true);
    loadUnmappedAccounts();
  };

  const handleSuggestMapping = async (senderName: string) => {
    setLoadingSuggestions((prev) => new Set(prev).add(senderName));
    try {
      const suggestion = await mappingRulesApi.suggestMapping(senderName);
      setSuggestions((prev) => ({ ...prev, [senderName]: suggestion }));
    } catch (err) {
      console.error('Failed to get suggestion:', err);
      toast.error(
        language === 'zh' ? '获取建议失败' : 'Failed to get suggestion'
      );
    } finally {
      setLoadingSuggestions((prev) => {
        const next = new Set(prev);
        next.delete(senderName);
        return next;
      });
    }
  };

  const handleManualMap = async () => {
    if (!manualMapTarget || !selectedUserId) return;

    setIsManualMapping(true);
    try {
      await mappingRulesApi.manualMapAccount(manualMapTarget, Number(selectedUserId));
      toast.success(
        language === 'zh' ? '手动映射成功' : 'Manual mapping successful'
      );
      setManualMapTarget(null);
      setSelectedUserId('');
      // Refresh data
      await loadUnmappedAccounts();
      await loadStats();
      onChange?.();
    } catch (err) {
      console.error('Failed to manual map:', err);
      toast.error(
        language === 'zh' ? '手动映射失败' : 'Failed to manual map'
      );
    } finally {
      setIsManualMapping(false);
    }
  };

  const handleTestMatch = async () => {
    if (!testMatchInput.trim()) return;

    setIsTestingMatch(true);
    try {
      const result = await mappingRulesApi.testMatch(testMatchInput.trim());
      setTestMatchResult(result);
    } catch (err) {
      console.error('Failed to test match:', err);
      toast.error(
        language === 'zh' ? '测试匹配失败' : 'Failed to test match'
      );
    } finally {
      setIsTestingMatch(false);
    }
  };

  const zh = language === 'zh';

  return (
    <div className="auto-mapping-panel mb-3">
      {/* Toggle button */}
      <div className="d-flex align-items-center gap-2">
        <Button
          variant="outline-secondary"
          size="sm"
          onClick={() => setIsExpanded(!isExpanded)}
        >
          <i className={`bi ${isExpanded ? 'bi-chevron-up' : 'bi-chevron-down'} me-1`} />
          {zh ? '自动映射' : 'Auto Mapping'}
        </Button>
        {stats && (
          <span className="text-muted small">
            <Badge variant="success" className="me-1">
              {zh ? '已映射' : 'Mapped'}: {stats.total_mapped}
            </Badge>
            <Badge variant="warning">
              {zh ? '未映射' : 'Unmapped'}: {stats.total_unmapped}
            </Badge>
          </span>
        )}
      </div>

      {/* Expanded panel */}
      {isExpanded && (
        <div className="mt-2 p-3 border rounded bg-light">
          {isLoadingStats ? (
            <Loading size="sm" text={t('loading', language)} />
          ) : stats ? (
            <>
              {/* Stats overview */}
              <div className="d-flex align-items-center gap-3 mb-3">
                <div>
                  <strong className="text-success">{stats.total_mapped}</strong>
                  <span className="text-muted small ms-1">
                    {zh ? '已映射' : 'Mapped'}
                  </span>
                </div>
                <div>
                  <strong className="text-warning">{stats.total_unmapped}</strong>
                  <span className="text-muted small ms-1">
                    {zh ? '未映射' : 'Unmapped'}
                  </span>
                </div>
                {stats.total_unmapped > 20 && (
                  <small className="text-muted">
                    {zh
                      ? `(统计中仅显示前 20 条，共 ${stats.total_unmapped} 个未映射账号)`
                      : `(Showing first 20 of ${stats.total_unmapped} unmapped accounts)`}
                  </small>
                )}
              </div>

              {/* Action buttons */}
              <div className="d-flex gap-2 flex-wrap">
                <Button
                  variant="primary"
                  size="sm"
                  onClick={handleRunAutoMap}
                  loading={isAutoMapping}
                  disabled={isAutoMapping}
                >
                  <i className="bi bi-magic me-1" />
                  {zh ? '执行自动映射' : 'Run Auto-Mapping'}
                </Button>

                {stats.total_unmapped > 0 && (
                  <Button
                    variant="outline-warning"
                    size="sm"
                    onClick={handleShowUnmapped}
                  >
                    <i className="bi bi-list-ul me-1" />
                    {zh ? '查看未映射账号' : 'View Unmapped'}
                    <Badge variant="secondary" className="ms-1">
                      {stats.total_unmapped}
                    </Badge>
                  </Button>
                )}

                <Button
                  variant="outline-info"
                  size="sm"
                  onClick={() => setShowTestMatchModal(true)}
                >
                  <i className="bi bi-search me-1" />
                  {zh ? '测试匹配' : 'Test Match'}
                </Button>

                <Button
                  variant="outline-secondary"
                  size="sm"
                  onClick={loadStats}
                >
                  <i className="bi bi-arrow-clockwise me-1" />
                  {zh ? '刷新统计' : 'Refresh Stats'}
                </Button>
              </div>

              {/* Auto-map preview result */}
              {autoMapPreview && (
                <div className="mt-3 alert alert-info">
                  <div className="d-flex justify-content-between align-items-center">
                    <div>
                      <strong>
                        {zh
                          ? `预览：将匹配 ${autoMapPreview.mapped_count} 个账号`
                          : `Preview: Will match ${autoMapPreview.mapped_count} accounts`}
                      </strong>
                      <span className="text-muted ms-2">
                        {zh
                          ? `${autoMapPreview.unmapped_count} 个无法自动匹配`
                          : `${autoMapPreview.unmapped_count} cannot be auto-mapped`}
                      </span>
                    </div>
                  </div>
                  {autoMapPreview.mappings.length > 0 && (
                    <div className="mt-2" style={{ maxHeight: '200px', overflowY: 'auto' }}>
                      <table className="table table-sm table-bordered mb-0">
                        <thead>
                          <tr>
                            <th>{zh ? '账号' : 'Account'}</th>
                            <th>{zh ? '匹配用户' : 'Matched User'}</th>
                            <th>{zh ? '匹配方式' : 'Matched By'}</th>
                          </tr>
                        </thead>
                        <tbody>
                          {autoMapPreview.mappings.map((m, idx) => (
                            <tr key={idx}>
                              <td><code>{m.tool_account}</code></td>
                              <td>{m.username}</td>
                              <td>
                                <Badge variant="secondary">{m.matched_by}</Badge>
                              </td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <div className="text-muted small">
              {zh ? '无法加载统计信息' : 'Failed to load stats'}
            </div>
          )}
        </div>
      )}

      {/* Unmapped accounts modal */}
      <Modal
        isOpen={showUnmappedModal}
        onClose={() => setShowUnmappedModal(false)}
        title={zh ? '未映射账号' : 'Unmapped Accounts'}
        size="xl"
        footer={
          <Button variant="secondary" onClick={() => setShowUnmappedModal(false)}>
            {t('close', language)}
          </Button>
        }
      >
        {isLoadingUnmapped ? (
          <Loading size="sm" text={t('loading', language)} />
        ) : unmappedAccounts.length === 0 ? (
          <div className="text-muted text-center py-3">
            {zh ? '没有未映射的账号' : 'No unmapped accounts'}
          </div>
        ) : (
          <div style={{ maxHeight: '500px', overflowY: 'auto' }}>
            <table className="table table-sm table-hover">
              <thead>
                <tr>
                  <th>{zh ? '账号' : 'Account'}</th>
                  <th>{zh ? '消息数' : 'Messages'}</th>
                  <th>{zh ? '建议' : 'Suggestion'}</th>
                  <th>{zh ? '操作' : 'Actions'}</th>
                </tr>
              </thead>
              <tbody>
                {unmappedAccounts.map((account) => {
                  const senderName = account.sender_name;
                  const suggestion = suggestions[senderName];
                  const isLoadingSuggestion = loadingSuggestions.has(senderName);
                  return (
                    <tr key={senderName}>
                      <td><code>{senderName}</code></td>
                      <td>
                        <Badge variant="secondary">{account.message_count}</Badge>
                      </td>
                      <td>
                        {isLoadingSuggestion ? (
                          <span className="text-muted small">
                            {t('loading', language)}
                          </span>
                        ) : suggestion ? (
                          suggestion.suggested_username ? (
                            <span>
                              <Badge variant="info" className="me-1">
                                {suggestion.matched_by}
                              </Badge>
                              {suggestion.suggested_username}
                            </span>
                          ) : (
                            <span className="text-muted small">
                              {zh ? '无建议' : 'No suggestion'}
                            </span>
                          )
                        ) : (
                          <span className="text-muted">-</span>
                        )}
                      </td>
                      <td>
                        <div className="btn-group btn-group-sm">
                          <Button
                            variant="outline-info"
                            size="sm"
                            onClick={() => handleSuggestMapping(senderName)}
                            disabled={isLoadingSuggestion}
                            title={zh ? '获取建议' : 'Get Suggestion'}
                          >
                            <i className="bi bi-lightbulb" />
                          </Button>
                          <Button
                            variant="outline-primary"
                            size="sm"
                            onClick={() => {
                              setManualMapTarget(senderName);
                              setSelectedUserId(
                                suggestion?.suggested_user_id ?? ''
                              );
                            }}
                            title={zh ? '手动映射' : 'Manual Map'}
                          >
                            <i className="bi bi-link" />
                          </Button>
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Modal>

      {/* Manual map modal */}
      <Modal
        isOpen={!!manualMapTarget}
        onClose={() => {
          setManualMapTarget(null);
          setSelectedUserId('');
        }}
        title={zh ? '手动映射账号' : 'Manual Map Account'}
        size="md"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setManualMapTarget(null);
                setSelectedUserId('');
              }}
            >
              {t('cancel', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleManualMap}
              loading={isManualMapping}
              disabled={isManualMapping || !selectedUserId}
            >
              {t('save', language)}
            </Button>
          </>
        }
      >
        {manualMapTarget && (
          <div>
            <div className="mb-3">
              <label className="form-label">
                {zh ? '账号' : 'Account'}
              </label>
              <input
                type="text"
                className="form-control"
                value={manualMapTarget}
                readOnly
              />
            </div>
            <div className="mb-3">
              <label className="form-label">
                {zh ? '映射到用户' : 'Map to User'}
              </label>
              <select
                className="form-select"
                value={selectedUserId}
                onChange={(e) =>
                  setSelectedUserId(e.target.value ? Number(e.target.value) : '')
                }
              >
                <option value="">-- {zh ? '选择用户' : 'Select User'} --</option>
                {users?.map((user) => (
                  <option key={user.id} value={user.id}>
                    {user.username} ({user.email})
                  </option>
                ))}
              </select>
            </div>
            <div className="alert alert-warning small">
              <i className="bi bi-exclamation-triangle me-1" />
              {zh
                ? '此操作将创建映射并更新相关消息的用户归属，请谨慎操作。'
                : 'This will create a mapping and update the user assignment for related messages. Proceed with caution.'}
            </div>
          </div>
        )}
      </Modal>

      {/* Test match modal */}
      <Modal
        isOpen={showTestMatchModal}
        onClose={() => {
          setShowTestMatchModal(false);
          setTestMatchInput('');
          setTestMatchResult(null);
        }}
        title={zh ? '测试匹配规则' : 'Test Match Rules'}
        size="md"
        footer={
          <>
            <Button
              variant="secondary"
              onClick={() => {
                setShowTestMatchModal(false);
                setTestMatchInput('');
                setTestMatchResult(null);
              }}
            >
              {t('close', language)}
            </Button>
            <Button
              variant="primary"
              onClick={handleTestMatch}
              loading={isTestingMatch}
              disabled={isTestingMatch || !testMatchInput.trim()}
            >
              {zh ? '测试' : 'Test'}
            </Button>
          </>
        }
      >
        <div className="mb-3">
          <label className="form-label">
            {zh ? '工具账号名称' : 'Tool Account Name'}
          </label>
          <input
            type="text"
            className="form-control"
            value={testMatchInput}
            onChange={(e) => setTestMatchInput(e.target.value)}
            placeholder={zh ? '例如: alice-macbook-pro-qwen' : 'e.g., alice-macbook-pro-qwen'}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault();
                handleTestMatch();
              }
            }}
          />
        </div>
        {testMatchResult && (
          <div className="mt-3">
            {testMatchResult.matched ? (
              <div className="alert alert-success">
                <div className="d-flex align-items-center gap-2">
                  <i className="bi bi-check-circle-fill text-success" />
                  <div>
                    <strong>{zh ? '匹配成功' : 'Matched'}</strong>
                    <div className="mt-1">
                      <Badge variant="info" className="me-1">
                        {testMatchResult.matched_by}
                      </Badge>
                      {testMatchResult.username}
                      {testMatchResult.rule_id && (
                        <span className="text-muted ms-2 small">
                          (Rule #{testMatchResult.rule_id})
                        </span>
                      )}
                    </div>
                  </div>
                </div>
              </div>
            ) : (
              <div className="alert alert-secondary">
                <i className="bi bi-x-circle me-1" />
                {zh ? '未匹配到任何规则' : 'No rules matched'}
              </div>
            )}
          </div>
        )}
      </Modal>
    </div>
  );
};
