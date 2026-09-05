/**
 * EncryptionKeyManagement Component - 加密密钥管理页面
 *
 * 功能：
 * - 密钥列表展示（指纹、状态、创建时间）
 * - 轮换按钮 + 二次确认对话框
 * - 轮换进度展示 + 格式验证反馈
 * - 多副本同步状态展示
 * - 操作历史标签页
 */

import React, { useState, useCallback } from 'react';
import {
  useEncryptionKeys,
  useRotateKey,
  useEncryptionKeysSyncStatus,
  useReEncryptPreCheck,
} from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import type { Language } from '@/types';
import { Button, Modal, TextInput, Loading, Error, Badge } from '@/components/common';
import type { BadgeVariant } from '@/components/common';

const statusBadgeVariant: Record<string, BadgeVariant> = {
  active: 'success',
  deprecated: 'warning',
  revoked: 'danger',
};

const statusText: Record<string, Record<Language, string>> = {
  active: { zh: '活跃', en: 'Active', ja: 'アクティブ', ko: '활성' },
  deprecated: { zh: '已弃用', en: 'Deprecated', ja: '非推奨', ko: '사용되지 않음' },
  revoked: { zh: '已撤销', en: 'Revoked', ja: '失効', ko: '취소됨' },
};

/**
 * 轮换确认对话框
 */
interface RotateConfirmModalProps {
  isOpen: boolean;
  onClose: () => void;
  onConfirm: (confirmation: string) => void;
  isLoading: boolean;
  currentVersion: number;
}

const RotateConfirmModal: React.FC<RotateConfirmModalProps> = ({
  isOpen,
  onClose,
  onConfirm,
  isLoading,
  currentVersion,
}) => {
  const language = useLanguage();
  const [confirmationText, setConfirmationText] = useState('');

  const handleConfirm = useCallback(() => {
    onConfirm(confirmationText);
  }, [confirmationText, onConfirm]);

  const handleClose = useCallback(() => {
    setConfirmationText('');
    onClose();
  }, [onClose]);

  return (
    <Modal
      isOpen={isOpen}
      onClose={handleClose}
      title={t('rotateKeyConfirmation', language)}
      size="lg"
    >
      <div className="p-4">
        <div className="alert alert-warning mb-4" role="alert">
          <i className="bi bi-exclamation-triangle-fill me-2" />
          <strong>{t('warning', language)}:</strong> {t('rotateKeyWarning', language)}
        </div>

        <ul className="list-unstyled mb-4">
          <li className="mb-2">
            <i className="bi bi-check-circle text-success me-2" />
            {t('rotateKeyBenefit1', language)}
          </li>
          <li className="mb-2">
            <i className="bi bi-check-circle text-success me-2" />
            {t('rotateKeyBenefit2', language)}
          </li>
          <li className="mb-2">
            <i className="bi bi-check-circle text-success me-2" />
            {t('rotateKeyBenefit3', language)}
          </li>
          <li className="mb-2">
            <i className="bi bi-check-circle text-success me-2" />
            {t('rotateKeyBenefit4', language)}
          </li>
        </ul>

        <div className="mb-4">
          <strong>{t('currentConfigVersion', language)}:</strong> {currentVersion}
        </div>

        <div className="mb-4">
          <label className="form-label">{t('enterRotateToConfirm', language)}</label>
          <TextInput value={confirmationText} onChange={setConfirmationText} placeholder="ROTATE" />
        </div>

        <div className="d-flex justify-content-end gap-2">
          <Button variant="secondary" onClick={handleClose} disabled={isLoading}>
            {t('cancel', language)}
          </Button>
          <Button
            variant="primary"
            onClick={handleConfirm}
            disabled={confirmationText !== 'ROTATE' || isLoading}
          >
            {isLoading ? t('rotating', language) : t('confirmRotate', language)}
          </Button>
        </div>
      </div>
    </Modal>
  );
};

/**
 * 密钥列表标签页
 */
interface KeysListTabProps {
  keys: Array<{
    key_id: number;
    fingerprint: string;
    status: string;
    created_at: string;
    rotated_at: string | null;
    last_used_at: string | null;
  }>;
  language: Language;
}

const KeysListTab: React.FC<KeysListTabProps> = ({ keys, language }) => {
  return (
    <div className="table-responsive">
      <table className="table table-hover">
        <thead>
          <tr>
            <th>ID</th>
            <th>{t('status', language)}</th>
            <th>{t('fingerprint', language)}</th>
            <th>{t('createdAt', language)}</th>
            <th>{t('lastUsed', language)}</th>
          </tr>
        </thead>
        <tbody>
          {keys.map((key) => (
            <tr key={key.key_id}>
              <td>{key.key_id}</td>
              <td>
                <Badge variant={statusBadgeVariant[key.status] || 'secondary'}>
                  {statusText[key.status]?.[language] || key.status}
                  {key.status === 'active' && <i className="bi bi-check-circle-fill ms-1" />}
                </Badge>
              </td>
              <td>
                <code className="user-select-all">{key.fingerprint}</code>
              </td>
              <td>{key.created_at}</td>
              <td>{key.last_used_at ?? '-'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

/**
 * 操作历史标签页
 */
interface AuditLogTabProps {
  language: Language;
}

const AuditLogTab: React.FC<AuditLogTabProps> = ({ language }) => {
  // TODO: 实现审计日志查询
  return (
    <div className="text-center py-4">
      <p className="text-muted">{t('auditLogComingSoon', language)}</p>
    </div>
  );
};

/**
 * 主组件
 */
export const EncryptionKeyManagement: React.FC = () => {
  const language = useLanguage();

  // State
  const [activeTab, setActiveTab] = useState<'keys' | 'audit'>('keys');
  const [showRotateModal, setShowRotateModal] = useState(false);
  const [showSyncStatusModal, setShowSyncStatusModal] = useState(false);
  const [showReEncryptModal, setShowReEncryptModal] = useState(false);

  // Hooks
  const { data: keysData, isLoading, isError, error, refetch } = useEncryptionKeys();

  const rotateKeyMutation = useRotateKey();
  const { data: syncStatus } = useEncryptionKeysSyncStatus();
  const reEncryptPreCheckMutation = useReEncryptPreCheck();

  // Data
  const keys = keysData?.keys ?? [];
  const configVersion = keysData?.config_version ?? 0;
  const primaryKeyId = keysData?.primary_key_id ?? 0;
  const rotationInProgress = keysData?.rotation_in_progress ?? false;
  const consistencyStatus = keysData?.consistency_status ?? 'unknown';

  // Handlers
  const handleRotateKey = useCallback(
    async (confirmation: string) => {
      try {
        await rotateKeyMutation.mutateAsync({
          confirmation,
          expected_version: configVersion,
        });
        setShowRotateModal(false);
        refetch();
      } catch (err) {
        console.error('Rotate key failed:', err);
      }
    },
    [configVersion, rotateKeyMutation, refetch]
  );

  const handleRefresh = useCallback(() => {
    refetch();
  }, [refetch]);

  // Render
  if (isLoading) {
    return <Loading text={t('loading', language)} />;
  }

  if (isError) {
    return (
      <Error
        title={t('errorLoadingKeys', language)}
        message={error?.message || t('unknownError', language)}
        onRetry={refetch}
      />
    );
  }

  return (
    <div className="container-fluid py-4">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2 className="mb-0">
          <i className="bi bi-key me-2" />
          {t('encryptionKeyManagement', language)}
        </h2>
        <div className="d-flex gap-2">
          <Button variant="outline-secondary" onClick={handleRefresh}>
            <i className="bi bi-arrow-clockwise me-1" />
            {t('refresh', language)}
          </Button>
          <Button variant="outline-info" onClick={() => setShowSyncStatusModal(true)}>
            <i className="bi bi-diagram-3 me-1" />
            {t('syncStatus', language)}
          </Button>
          <Button
            variant="primary"
            onClick={() => setShowRotateModal(true)}
            disabled={rotationInProgress}
          >
            <i className="bi bi-arrow-repeat me-1" />
            {t('rotateKey', language)}
          </Button>
        </div>
      </div>

      {/* Status Cards */}
      <div className="row mb-4">
        <div className="col-md-3">
          <div className="card">
            <div className="card-body">
              <h6 className="card-subtitle mb-2 text-muted">{t('configVersion', language)}</h6>
              <h3 className="card-title mb-0">{configVersion}</h3>
            </div>
          </div>
        </div>
        <div className="col-md-3">
          <div className="card">
            <div className="card-body">
              <h6 className="card-subtitle mb-2 text-muted">{t('primaryKeyId', language)}</h6>
              <h3 className="card-title mb-0">{primaryKeyId}</h3>
            </div>
          </div>
        </div>
        <div className="col-md-3">
          <div className="card">
            <div className="card-body">
              <h6 className="card-subtitle mb-2 text-muted">{t('syncStatus', language)}</h6>
              <h3 className="card-title mb-0">
                {syncStatus?.sync_status === 'synchronized' ? (
                  <span className="text-success">
                    <i className="bi bi-check-circle-fill me-1" />
                    {t('synchronized', language)}
                  </span>
                ) : syncStatus?.sync_status === 'diverged' ? (
                  <span className="text-warning">
                    <i className="bi bi-exclamation-triangle-fill me-1" />
                    {t('diverged', language)}
                  </span>
                ) : (
                  <span className="text-secondary">
                    <i className="bi bi-question-circle me-1" />
                    {t('unknown', language)}
                  </span>
                )}
              </h3>
            </div>
          </div>
        </div>
        <div className="col-md-3">
          <div className="card">
            <div className="card-body">
              <h6 className="card-subtitle mb-2 text-muted">{t('consistencyStatus', language)}</h6>
              <h3 className="card-title mb-0">
                {consistencyStatus === 'consistent' ? (
                  <span className="text-success">
                    <i className="bi bi-check-circle-fill me-1" />
                    {t('consistent', language)}
                  </span>
                ) : (
                  <span className="text-danger">
                    <i className="bi bi-x-circle-fill me-1" />
                    {t('inconsistent', language)}
                  </span>
                )}
              </h3>
            </div>
          </div>
        </div>
      </div>

      {/* Consistency Warning */}
      {consistencyStatus === 'inconsistent' && (
        <div className="alert alert-danger mb-4" role="alert">
          <i className="bi bi-exclamation-triangle-fill me-2" />
          <strong>{t('warning', language)}:</strong> {t('inconsistencyDetected', language)}
          <Button variant="outline-danger" size="sm" className="ms-3" onClick={handleRefresh}>
            {t('runMigration', language)}
          </Button>
        </div>
      )}

      {/* Tabs */}
      <ul className="nav nav-tabs mb-3">
        <li className="nav-item">
          <button
            className={`nav-link ${activeTab === 'keys' ? 'active' : ''}`}
            onClick={() => setActiveTab('keys')}
          >
            <i className="bi bi-list-ul me-1" />
            {t('keysList', language)}
          </button>
        </li>
        <li className="nav-item">
          <button
            className={`nav-link ${activeTab === 'audit' ? 'active' : ''}`}
            onClick={() => setActiveTab('audit')}
          >
            <i className="bi bi-clock-history me-1" />
            {t('operationHistory', language)}
          </button>
        </li>
      </ul>

      {/* Tab Content */}
      {activeTab === 'keys' ? (
        <KeysListTab keys={keys} language={language} />
      ) : (
        <AuditLogTab language={language} />
      )}

      {/* Actions */}
      <div className="mt-4 d-flex gap-2">
        <Button variant="outline-info" onClick={() => setShowReEncryptModal(true)}>
          <i className="bi bi-arrow-repeat me-1" />
          {t('preCheckCiphertext', language)}
        </Button>
        <Button variant="outline-warning">
          <i className="bi bi-shield-check me-1" />
          {t('reEncryptData', language)}
        </Button>
      </div>

      {/* Security Tips */}
      <div className="mt-4">
        <div className="card bg-light">
          <div className="card-body">
            <h6 className="card-title">
              <i className="bi bi-info-circle me-1" />
              {t('securityTips', language)}
            </h6>
            <ul className="mb-0">
              <li>{t('rotateKeyTip1', language)}</li>
              <li>{t('rotateKeyTip2', language)}</li>
              <li>{t('rotateKeyTip3', language)}</li>
              <li>{t('rotateKeyTip4', language)}</li>
            </ul>
          </div>
        </div>
      </div>

      {/* Rotate Modal */}
      <RotateConfirmModal
        isOpen={showRotateModal}
        onClose={() => setShowRotateModal(false)}
        onConfirm={handleRotateKey}
        isLoading={rotateKeyMutation.isPending}
        currentVersion={configVersion}
      />

      {/* Sync Status Modal */}
      <Modal
        isOpen={showSyncStatusModal}
        onClose={() => setShowSyncStatusModal(false)}
        title={t('multiReplicaSyncStatus', language)}
        size="lg"
      >
        <div className="p-4">
          <div className="mb-4">
            <strong>{t('localConfigVersion', language)}:</strong> {syncStatus?.local_version}
          </div>

          {syncStatus?.remote_versions && Object.keys(syncStatus.remote_versions).length > 0 ? (
            <table className="table">
              <thead>
                <tr>
                  <th>{t('replicaName', language)}</th>
                  <th>{t('configVersion', language)}</th>
                  <th>{t('status', language)}</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(syncStatus.remote_versions).map(([name, version]) => (
                  <tr key={name}>
                    <td>{name}</td>
                    <td>{version ?? '-'}</td>
                    <td>
                      {version === syncStatus.local_version ? (
                        <Badge variant="success">
                          <i className="bi bi-check-circle-fill me-1" />
                          {t('synchronized', language)}
                        </Badge>
                      ) : version !== null ? (
                        <Badge variant="warning">
                          <i className="bi bi-exclamation-triangle-fill me-1" />
                          {t('lagging', language)}
                        </Badge>
                      ) : (
                        <Badge variant="secondary">
                          <i className="bi bi-question-circle me-1" />
                          {t('unavailable', language)}
                        </Badge>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-muted">{t('noReplicasConfigured', language)}</p>
          )}

          <div className="mt-4">
            <strong>{t('overallStatus', language)}:</strong>{' '}
            {syncStatus?.sync_status === 'synchronized' ? (
              <Badge variant="success">{t('allReplicasSynced', language)}</Badge>
            ) : syncStatus?.sync_status === 'diverged' ? (
              <Badge variant="warning">{t('someReplicasLagging', language)}</Badge>
            ) : (
              <Badge variant="secondary">{t('unknownStatus', language)}</Badge>
            )}
          </div>
        </div>
      </Modal>

      {/* Re-encrypt Modal */}
      <Modal
        isOpen={showReEncryptModal}
        onClose={() => setShowReEncryptModal(false)}
        title={t('reEncryptPreCheck', language)}
        size="lg"
      >
        <div className="p-4">
          {reEncryptPreCheckMutation.isPending ? (
            <Loading text={t('checkingCiphertext', language)} />
          ) : reEncryptPreCheckMutation.data ? (
            <>
              <div className="mb-4">
                <h6>{t('ciphertextStats', language)}</h6>
                <table className="table table-sm">
                  <tbody>
                    <tr>
                      <td>{t('totalCiphertexts', language)}</td>
                      <td>{reEncryptPreCheckMutation.data.ciphertext_stats.total}</td>
                    </tr>
                    <tr>
                      <td>{t('withKeyIdPrefix', language)}</td>
                      <td>{reEncryptPreCheckMutation.data.ciphertext_stats.with_key_id_prefix}</td>
                    </tr>
                    <tr>
                      <td>{t('legacyFormat', language)}</td>
                      <td>{reEncryptPreCheckMutation.data.ciphertext_stats.legacy_format}</td>
                    </tr>
                  </tbody>
                </table>
              </div>

              <div className="mb-4">
                <h6>{t('decryptionTest', language)}</h6>
                {reEncryptPreCheckMutation.data.decryption_test.all_decryptable ? (
                  <div className="alert alert-success">
                    <i className="bi bi-check-circle-fill me-2" />
                    {t('allCiphertextsDecryptable', language)}
                  </div>
                ) : (
                  <div className="alert alert-warning">
                    <i className="bi bi-exclamation-triangle-fill me-2" />
                    {t('someCiphertextsUndecryptable', language)}:{' '}
                    {reEncryptPreCheckMutation.data.decryption_test.failed_count}
                  </div>
                )}
              </div>

              {reEncryptPreCheckMutation.data.recommendations.length > 0 && (
                <div className="mb-4">
                  <h6>{t('recommendations', language)}</h6>
                  <ul>
                    {reEncryptPreCheckMutation.data.recommendations.map((rec, idx) => (
                      <li key={idx}>{rec}</li>
                    ))}
                  </ul>
                </div>
              )}
            </>
          ) : (
            <p className="text-muted">{t('clickToRunPreCheck', language)}</p>
          )}

          <div className="d-flex justify-content-end gap-2">
            <Button variant="secondary" onClick={() => setShowReEncryptModal(false)}>
              {t('close', language)}
            </Button>
            <Button
              variant="primary"
              onClick={() => reEncryptPreCheckMutation.mutate()}
              disabled={reEncryptPreCheckMutation.isPending}
            >
              <i className="bi bi-arrow-clockwise me-1" />
              {t('runPreCheck', language)}
            </Button>
          </div>
        </div>
      </Modal>
    </div>
  );
};
