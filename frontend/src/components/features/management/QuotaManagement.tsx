/**
 * QuotaManagement Component - User quota management
 */

import React, { useState } from 'react';
import { useQuotaUsage, useUpdateQuota } from '@/hooks';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  Button,
  Modal,
  TextInput,
  Loading,
  Error,
  EmptyState,
  Progress,
} from '@/components/common';
import { formatTokens } from '@/utils';
import type { QuotaUsage, UpdateQuotaRequest } from '@/api';

export const QuotaManagement: React.FC = () => {
  const language = useLanguage();
  const { data: quotaData, isLoading, isFetching, isError, error, refetch } = useQuotaUsage();
  const updateQuota = useUpdateQuota();

  const [showModal, setShowModal] = useState(false);
  const [editingUser, setEditingUser] = useState<QuotaUsage | null>(null);
  const [formData, setFormData] = useState<UpdateQuotaRequest>({});

  const handleOpenEdit = (user: QuotaUsage) => {
    setEditingUser(user);
    setFormData({
      daily_token_quota: user.daily_token_quota,
      monthly_token_quota: user.monthly_token_quota,
      daily_request_quota: user.daily_request_quota,
      monthly_request_quota: user.monthly_request_quota,
    });
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingUser(null);
  };

  const handleSubmit = async () => {
    if (!editingUser) return;

    try {
      await updateQuota.mutateAsync({ userId: editingUser.id, data: formData });
      handleCloseModal();
    } catch (err) {
      console.error('Failed to update quota:', err);
    }
  };

  const getUsagePercentage = (used?: number, limit?: number) => {
    if (!used || !limit || limit === 0) return 0;
    return Math.min((used / limit) * 100, 100);
  };

  const getUsageVariant = (percentage: number) => {
    if (percentage >= 95) return 'danger';
    if (percentage >= 80) return 'warning';
    return 'success';
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (isError) {
    return <Error message={error?.message || t('error', language)} onRetry={() => refetch()} />;
  }

  return (
    <div className="quota-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-3">
        <h5>{t('quotaUsage', language)}</h5>
        <Button variant="primary" size="sm" onClick={() => refetch()} loading={isFetching}>
          {isFetching ? null : <i className="bi bi-arrow-clockwise me-1" />}
          {t('refresh', language)}
        </Button>
      </div>

      {/* Quota Cards */}
      {!quotaData || quotaData.length === 0 ? (
        <EmptyState icon="bi-sliders" title={t('noQuotaData', language)} />
      ) : (
        <div className="row g-3">
          {quotaData.map((user) => {
            const dailyTokenPercentage = getUsagePercentage(
              user.tokens_used_today,
              user.daily_token_quota
            );
            const monthlyTokenPercentage = getUsagePercentage(
              user.tokens_used_month,
              user.monthly_token_quota
            );

            return (
              <div key={user.id} className="col-md-6 col-lg-4">
                <Card className="h-100">
                  <div className="d-flex justify-content-between align-items-start mb-3">
                    <div>
                      <h6 className="mb-1">{user.username}</h6>
                      <small className="text-muted">{user.email}</small>
                    </div>
                    <Button
                      variant="outline-primary"
                      size="sm"
                      onClick={() => handleOpenEdit(user)}
                    >
                      <i className="bi bi-pencil" />
                    </Button>
                  </div>

                  {/* Daily Token Quota */}
                  <div className="mb-3">
                    <div className="d-flex justify-content-between mb-1">
                      <small>{t('dailyTokenQuota', language)}</small>
                      <small>
                        {formatTokens(user.tokens_used_today || 0)} /{' '}
                        {user.daily_token_quota ? formatTokens(user.daily_token_quota) : '∞'}
                      </small>
                    </div>
                    <Progress
                      value={dailyTokenPercentage}
                      variant={getUsageVariant(dailyTokenPercentage)}
                      size="sm"
                    />
                  </div>

                  {/* Monthly Token Quota */}
                  <div className="mb-3">
                    <div className="d-flex justify-content-between mb-1">
                      <small>{t('monthlyTokenQuota', language)}</small>
                      <small>
                        {formatTokens(user.tokens_used_month || 0)} /{' '}
                        {user.monthly_token_quota ? formatTokens(user.monthly_token_quota) : '∞'}
                      </small>
                    </div>
                    <Progress
                      value={monthlyTokenPercentage}
                      variant={getUsageVariant(monthlyTokenPercentage)}
                      size="sm"
                    />
                  </div>

                  {/* Request Stats */}
                  <div className="d-flex gap-3 text-muted small">
                    <span>
                      <i className="bi bi-arrow-repeat me-1" />
                      {t('dailyRequests', language)}: {user.requests_today || 0}
                    </span>
                    <span>
                      <i className="bi bi-calendar me-1" />
                      {t('monthlyRequests', language)}: {user.requests_month || 0}
                    </span>
                  </div>
                </Card>
              </div>
            );
          })}
        </div>
      )}

      {/* Edit Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModal}
        title={t('editQuota', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSubmit} loading={updateQuota.isPending}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        {editingUser && (
          <div className="row g-3">
            <div className="col-12">
              <p className="mb-3">
                <strong>{t('user', language)}:</strong> {editingUser.username}
              </p>
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('dailyTokenQuota', language)}</label>
              <TextInput
                type="number"
                value={formData.daily_token_quota?.toString() || ''}
                onChange={(value: string) =>
                  setFormData({
                    ...formData,
                    daily_token_quota: value ? parseInt(value) : undefined,
                  })
                }
                placeholder={t('unlimited', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('monthlyTokenQuota', language)}</label>
              <TextInput
                type="number"
                value={formData.monthly_token_quota?.toString() || ''}
                onChange={(value: string) =>
                  setFormData({
                    ...formData,
                    monthly_token_quota: value ? parseInt(value) : undefined,
                  })
                }
                placeholder={t('unlimited', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('dailyRequestQuota', language)}</label>
              <TextInput
                type="number"
                value={formData.daily_request_quota?.toString() || ''}
                onChange={(value: string) =>
                  setFormData({
                    ...formData,
                    daily_request_quota: value ? parseInt(value) : undefined,
                  })
                }
                placeholder={t('unlimited', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('monthlyRequestQuota', language)}</label>
              <TextInput
                type="number"
                value={formData.monthly_request_quota?.toString() || ''}
                onChange={(value: string) =>
                  setFormData({
                    ...formData,
                    monthly_request_quota: value ? parseInt(value) : undefined,
                  })
                }
                placeholder={t('unlimited', language)}
              />
            </div>
          </div>
        )}
      </Modal>
    </div>
  );
};
