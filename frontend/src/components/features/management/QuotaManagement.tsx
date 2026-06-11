/**
 * QuotaManagement Component - User quota management
 */

import React, { useState } from 'react';
import { useQuotaUsage, useUpdateQuota, usePageRefresh } from '@/hooks';
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
  useToast,
  PageRefreshControl,
} from '@/components/common';
import { formatTokens, createMatcherConfig } from '@/utils';
import {
  QuotaType,
  TOKEN_QUOTA_MULTIPLIER,
} from '@/constants/quota';
import {
  parseAndValidateQuota,
  formatQuotaForDisplay,
  getMaxQuotaDisplay,
} from '@/utils/quotaFormatter';
import type { QuotaUsage, UpdateQuotaRequest } from '@/api';

export const QuotaManagement: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const { data: quotaData, isLoading, isFetching, isError, error, refetch } = useQuotaUsage();
  const updateQuota = useUpdateQuota();

  // Page refresh control - manages manual refresh for quota data
  const pageRefresh = usePageRefresh({
    page: '/manage/quota',
    refreshKey: createMatcherConfig([['quota']], 'prefix'),
    interval: 0, // Manual refresh only for configuration data
    enabled: false,
  });

  const [showModal, setShowModal] = useState(false);
  const [editingUser, setEditingUser] = useState<QuotaUsage | null>(null);
  const [formData, setFormData] = useState<UpdateQuotaRequest>({});
  const [quotaErrors, setQuotaErrors] = useState<{
    daily_token_quota?: string;
    monthly_token_quota?: string;
    daily_request_quota?: string;
    monthly_request_quota?: string;
  }>({});

  const handleOpenEdit = (user: QuotaUsage) => {
    setEditingUser(user);
    // Token quotas are stored in M (millions) units directly
    setFormData({
      daily_token_quota: user.daily_token_quota ?? undefined,
      monthly_token_quota: user.monthly_token_quota ?? undefined,
      daily_request_quota: user.daily_request_quota,
      monthly_request_quota: user.monthly_request_quota,
    });
    setQuotaErrors({});
    setShowModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingUser(null);
    setQuotaErrors({});
  };

  // Handle quota input change with validation
  const handleQuotaInputChange = (
    value: string,
    quotaType: QuotaType,
    field: keyof UpdateQuotaRequest
  ) => {
    const result = parseAndValidateQuota(value, quotaType);

    if (value.trim() === '') {
      // Empty input means unlimited
      setFormData({ ...formData, [field]: undefined });
      setQuotaErrors({ ...quotaErrors, [field]: undefined });
    } else if (result.value !== null) {
      // Update form data with parsed value
      setFormData({ ...formData, [field]: result.value });

      // Update error state
      if (!result.validation.isValid) {
        setQuotaErrors({
          ...quotaErrors,
          [field]: result.validation.error,
        });
      } else if (result.validation.warning) {
        // Show warning but allow the value
        setQuotaErrors({
          ...quotaErrors,
          [field]: result.validation.warning,
        });
      } else {
        // Clear error
        setQuotaErrors({ ...quotaErrors, [field]: undefined });
      }
    } else {
      // Invalid input
      setQuotaErrors({
        ...quotaErrors,
        [field]: 'Invalid number format',
      });
    }
  };

  const handleSubmit = async () => {
    if (!editingUser) return;

    // Validate all quota values before submission
    const errors: typeof quotaErrors = {};

    // Validate daily token quota
    if (formData.daily_token_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.daily_token_quota.toString(),
        QuotaType.DAILY_TOKEN
      );
      if (!validation.validation.isValid) {
        errors.daily_token_quota = validation.validation.error || 'Invalid value';
      }
    }

    // Validate monthly token quota
    if (formData.monthly_token_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.monthly_token_quota.toString(),
        QuotaType.MONTHLY_TOKEN
      );
      if (!validation.validation.isValid) {
        errors.monthly_token_quota = validation.validation.error || 'Invalid value';
      }
    }

    // Validate daily request quota
    if (formData.daily_request_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.daily_request_quota.toString(),
        QuotaType.DAILY_REQUEST
      );
      if (!validation.validation.isValid) {
        errors.daily_request_quota = validation.validation.error || 'Invalid value';
      }
    }

    // Validate monthly request quota
    if (formData.monthly_request_quota !== undefined) {
      const validation = parseAndValidateQuota(
        formData.monthly_request_quota.toString(),
        QuotaType.MONTHLY_REQUEST
      );
      if (!validation.validation.isValid) {
        errors.monthly_request_quota = validation.validation.error || 'Invalid value';
      }
    }

    // If there are errors, don't submit and show errors
    if (Object.keys(errors).length > 0) {
      setQuotaErrors(errors);
      toast.error(t('validationError', language), t('quotaValidationFailed', language));
      return;
    }

    // Clear errors
    setQuotaErrors({});

    try {
      // Token quotas are stored in M (millions) units directly
      const submitData: UpdateQuotaRequest = {
        daily_token_quota: formData.daily_token_quota ?? undefined,
        monthly_token_quota: formData.monthly_token_quota ?? undefined,
        daily_request_quota: formData.daily_request_quota,
        monthly_request_quota: formData.monthly_request_quota,
      };
      await updateQuota.mutateAsync({ userId: editingUser.id, data: submitData });
      toast.success(t('quotaUpdated', language), t('quotaUpdatedDesc', language));
      handleCloseModal();
    } catch (err) {
      console.error('Failed to update quota:', err);
      const errorMessage =
        err && typeof err === 'object' && 'message' in err
          ? String((err as { message: string }).message)
          : t('error', language);
      toast.error(t('error', language), errorMessage);
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
        <PageRefreshControl
          refresh={pageRefresh}
          showAutoRefreshToggle={false}
          showIntervalSelector={false}
          compact={true}
          showLastRefreshTime={true}
        />
      </div>

      {/* Quota Cards */}
      {!quotaData || quotaData.length === 0 ? (
        <EmptyState icon="bi-sliders" title={t('noQuotaData', language)} />
      ) : (
        <div className="row g-3">
          {quotaData.map((user) => {
            const dailyTokenPercentage = getUsagePercentage(
              user.tokens_used_today,
              user.daily_token_quota ? user.daily_token_quota * TOKEN_QUOTA_MULTIPLIER : undefined
            );
            const monthlyTokenPercentage = getUsagePercentage(
              user.tokens_used_month,
              user.monthly_token_quota
                ? user.monthly_token_quota * TOKEN_QUOTA_MULTIPLIER
                : undefined
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
                        {formatTokens(user.tokens_used_today ?? 0)} /{' '}
                        {formatQuotaForDisplay(user.daily_token_quota, true)}
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
                        {formatTokens(user.tokens_used_month ?? 0)} /{' '}
                        {formatQuotaForDisplay(user.monthly_token_quota, true)}
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
                      {t('dailyRequests', language)}: {user.requests_today ?? 0}
                    </span>
                    <span>
                      <i className="bi bi-calendar me-1" />
                      {t('monthlyRequests', language)}: {user.requests_month ?? 0}
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
          <form
            onSubmit={(e) => {
              e.preventDefault();
              handleSubmit();
            }}
          >
            <div className="row g-3">
              <div className="col-12">
                <p className="mb-3">
                  <strong>{t('user', language)}:</strong> {editingUser.username}
                </p>
              </div>
              <div className="col-md-6">
                <label className="form-label">
                  {t('dailyTokenQuota', language)} (M)
                  <small className="text-muted ms-1">({getMaxQuotaDisplay(QuotaType.DAILY_TOKEN)})</small>
                </label>
                <TextInput
                  type="text"
                  value={formData.daily_token_quota?.toString() ?? ''}
                  onChange={(value: string) =>
                    handleQuotaInputChange(value, QuotaType.DAILY_TOKEN, 'daily_token_quota')
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      handleSubmit();
                    }
                  }}
                  placeholder={t('unlimited', language)}
                  error={quotaErrors.daily_token_quota}
                />
                {quotaErrors.daily_token_quota && (
                  <small className="text-danger">{quotaErrors.daily_token_quota}</small>
                )}
              </div>
              <div className="col-md-6">
                <label className="form-label">
                  {t('monthlyTokenQuota', language)} (M)
                  <small className="text-muted ms-1">({getMaxQuotaDisplay(QuotaType.MONTHLY_TOKEN)})</small>
                </label>
                <TextInput
                  type="text"
                  value={formData.monthly_token_quota?.toString() ?? ''}
                  onChange={(value: string) =>
                    handleQuotaInputChange(value, QuotaType.MONTHLY_TOKEN, 'monthly_token_quota')
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      handleSubmit();
                    }
                  }}
                  placeholder={t('unlimited', language)}
                  error={quotaErrors.monthly_token_quota}
                />
                {quotaErrors.monthly_token_quota && (
                  <small className="text-danger">{quotaErrors.monthly_token_quota}</small>
                )}
              </div>
              <div className="col-md-6">
                <label className="form-label">
                  {t('dailyRequestQuota', language)}
                  <small className="text-muted ms-1">({getMaxQuotaDisplay(QuotaType.DAILY_REQUEST)})</small>
                </label>
                <TextInput
                  type="text"
                  value={formData.daily_request_quota?.toString() ?? ''}
                  onChange={(value: string) =>
                    handleQuotaInputChange(value, QuotaType.DAILY_REQUEST, 'daily_request_quota')
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      handleSubmit();
                    }
                  }}
                  placeholder={t('unlimited', language)}
                  error={quotaErrors.daily_request_quota}
                />
                {quotaErrors.daily_request_quota && (
                  <small className="text-danger">{quotaErrors.daily_request_quota}</small>
                )}
              </div>
              <div className="col-md-6">
                <label className="form-label">
                  {t('monthlyRequestQuota', language)}
                  <small className="text-muted ms-1">({getMaxQuotaDisplay(QuotaType.MONTHLY_REQUEST)})</small>
                </label>
                <TextInput
                  type="text"
                  value={formData.monthly_request_quota?.toString() ?? ''}
                  onChange={(value: string) =>
                    handleQuotaInputChange(value, QuotaType.MONTHLY_REQUEST, 'monthly_request_quota')
                  }
                  onKeyDown={(e) => {
                    if (e.key === 'Enter') {
                      e.preventDefault();
                      handleSubmit();
                    }
                  }}
                  placeholder={t('unlimited', language)}
                  error={quotaErrors.monthly_request_quota}
                />
                {quotaErrors.monthly_request_quota && (
                  <small className="text-danger">{quotaErrors.monthly_request_quota}</small>
                )}
              </div>
            </div>
          </form>
        )}
      </Modal>
    </div>
  );
};
