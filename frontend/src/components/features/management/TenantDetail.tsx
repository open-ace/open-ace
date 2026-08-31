/**
 * TenantDetail Component - Tenant detail page
 *
 * Issue #3197: Displays tenant details including usage statistics and charts
 */

import React, { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useLanguage } from '@/store';
import { t, type Language } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Loading,
  Error as ErrorComponent,
  Badge,
} from '@/components/common';
import { tenantApi, type Tenant, type TenantStats, type TenantUsage } from '@/api';
import { formatDateTime } from '@/utils';
import { TOKEN_QUOTA_MULTIPLIER } from '@/constants/quota';
import { formatQuotaForDisplay } from '@/utils/quotaFormatter';
import { TenantUsageChart } from './TenantUsageChart';

// Status badge variants
const getStatusVariant = (status: string) => {
  switch (status) {
    case 'active':
      return 'success';
    case 'suspended':
      return 'danger';
    case 'trial':
      return 'warning';
    default:
      return 'secondary';
  }
};

// Plan badge variants
const getPlanVariant = (plan: string) => {
  switch (plan) {
    case 'free':
      return 'secondary';
    case 'enterprise':
      return 'primary';
    case 'premium':
      return 'info';
    default:
      return 'secondary';
  }
};

// Type-safe label mappings
const PLAN_LABELS: Record<string, string> = {
  free: 'tenantPlanFree',
  standard: 'tenantPlanStandard',
  premium: 'tenantPlanPremium',
  enterprise: 'tenantPlanEnterprise',
};

const STATUS_LABELS: Record<string, string> = {
  active: 'tenantStatusActive',
  suspended: 'tenantStatusSuspended',
  trial: 'tenantStatusTrial',
};

const getPlanLabel = (plan: string, language: Language): string => {
  const key = PLAN_LABELS[plan];
  return key ? t(key, language) : plan;
};

const getStatusLabel = (status: string, language: Language): string => {
  const key = STATUS_LABELS[status];
  return key ? t(key, language) : status;
};

export const TenantDetail: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const language = useLanguage();

  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [stats, setStats] = useState<TenantStats | null>(null);
  const [usage, setUsage] = useState<TenantUsage[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const fetchData = async () => {
      if (!id) return;

      setIsLoading(true);
      setError(null);

      try {
        const tenantId = parseInt(id, 10);

        // Fetch data in parallel using Promise.allSettled for resilience
        const results = await Promise.allSettled([
          tenantApi.getTenant(tenantId),
          tenantApi.getTenantStats(tenantId),
          tenantApi.getTenantUsage(tenantId, 30),
        ]);

        // Handle tenant data
        if (results[0].status === 'fulfilled') {
          setTenant(results[0].value);
        } else {
          throw new Error('Failed to load tenant');
        }

        // Handle stats data
        if (results[1].status === 'fulfilled') {
          setStats(results[1].value);
        } else {
          console.error('Failed to load stats:', results[1].reason);
        }

        // Handle usage data
        if (results[2].status === 'fulfilled') {
          setUsage(results[2].value);
        } else {
          console.error('Failed to load usage:', results[2].reason);
        }
      } catch (err: unknown) {
        let errorMessage: string;
        if (err && typeof err === 'object' && 'message' in err) {
          errorMessage = String((err as { message: string }).message);
        } else if (err instanceof Error) {
          errorMessage = err.message;
        } else {
          errorMessage = 'Failed to load tenant details';
        }
        setError(errorMessage);
      } finally {
        setIsLoading(false);
      }
    };

    fetchData();
  }, [id]);

  const handleBack = () => {
    navigate('/manage/tenants');
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <ErrorComponent message={error} onRetry={() => window.location.reload()} />;
  }

  if (!tenant) {
    return <ErrorComponent message={t('tenantNotFound', language)} onRetry={handleBack} />;
  }

  return (
    <div className="tenant-detail">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <div className="d-flex align-items-center gap-3">
          <Button variant="outline-secondary" size="sm" onClick={handleBack}>
            <i className="bi bi-arrow-left me-1" />
            {t('back', language)}
          </Button>
          <h2 className="mb-0">{t('tenantDetailTitle', language)}</h2>
        </div>
        <Badge variant={getStatusVariant(tenant.status)}>
          {getStatusLabel(tenant.status, language)}
        </Badge>
      </div>

      {/* Basic Info Card */}
      <Card className="mb-4">
        <h5 className="mb-3">{t('tenantName', language)}</h5>
        <div className="row g-3">
          <div className="col-md-4">
            <label className="form-label text-muted">{t('tenantName', language)}</label>
            <div className="fw-medium">{tenant.name}</div>
          </div>
          <div className="col-md-4">
            <label className="form-label text-muted">{t('slug', language)}</label>
            <div>
              <code>{tenant.slug}</code>
            </div>
          </div>
          <div className="col-md-4">
            <label className="form-label text-muted">{t('plan', language)}</label>
            <div>
              <Badge variant={getPlanVariant(tenant.plan)}>
                {getPlanLabel(tenant.plan, language)}
              </Badge>
            </div>
          </div>
          <div className="col-md-4">
            <label className="form-label text-muted">{t('contactEmail', language)}</label>
            <div>{tenant.contact_email ?? '-'}</div>
          </div>
          <div className="col-md-4">
            <label className="form-label text-muted">{t('contactName', language)}</label>
            <div>{tenant.contact_name ?? '-'}</div>
          </div>
          <div className="col-md-4">
            <label className="form-label text-muted">{t('createdAt', language)}</label>
            <div>{formatDateTime(tenant.created_at)}</div>
          </div>
        </div>
      </Card>

      {/* User Statistics */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('tenantUserCount', language)}
            value={`${tenant.user_count ?? 0} / ${tenant.quota?.max_users ?? 0}`}
            icon={<i className="bi bi-people fs-4" />}
            variant="info"
          />
        </div>
      </div>

      {/* Quota Usage Card */}
      <Card className="mb-4">
        <h5 className="mb-3">{t('quota', language)}</h5>
        {stats ? (
          <div className="row g-4">
            {/* Daily Usage */}
            <div className="col-md-6">
              <h6 className="text-muted mb-3">{t('tenantDailyUsage', language)}</h6>
              <div className="row g-3">
                <div className="col-6">
                  <div className="border rounded p-3">
                    <div className="text-muted small mb-1">{t('tenantTokenUsage', language)}</div>
                    <div className="fw-medium">
                      {formatQuotaForDisplay(
                        stats.quota_usage.daily_tokens.used / TOKEN_QUOTA_MULTIPLIER,
                        true
                      )}
                    </div>
                    <div className="text-muted small">
                      {t('max', language)}:{' '}
                      {formatQuotaForDisplay(
                        stats.quota_usage.daily_tokens.limit / TOKEN_QUOTA_MULTIPLIER,
                        true
                      )}
                    </div>
                    <div className="progress mt-2" style={{ height: '4px' }}>
                      <div
                        className={`progress-bar ${
                          stats.quota_usage.daily_tokens.percentage >= 90
                            ? 'bg-danger'
                            : stats.quota_usage.daily_tokens.percentage >= 70
                              ? 'bg-warning'
                              : 'bg-success'
                        }`}
                        style={{
                          width: `${Math.min(100, stats.quota_usage.daily_tokens.percentage)}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
                <div className="col-6">
                  <div className="border rounded p-3">
                    <div className="text-muted small mb-1">{t('tenantRequestUsage', language)}</div>
                    <div className="fw-medium">
                      {stats.quota_usage.daily_requests.used.toLocaleString()}
                    </div>
                    <div className="text-muted small">
                      {t('max', language)}:{' '}
                      {stats.quota_usage.daily_requests.limit.toLocaleString()}
                    </div>
                    <div className="progress mt-2" style={{ height: '4px' }}>
                      <div
                        className={`progress-bar ${
                          stats.quota_usage.daily_requests.percentage >= 90
                            ? 'bg-danger'
                            : stats.quota_usage.daily_requests.percentage >= 70
                              ? 'bg-warning'
                              : 'bg-success'
                        }`}
                        style={{
                          width: `${Math.min(100, stats.quota_usage.daily_requests.percentage)}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* Monthly Usage */}
            <div className="col-md-6">
              <h6 className="text-muted mb-3">
                {stats.billing_cycle?.valid
                  ? t('tenantMonthlyUsage', language)
                  : t('tenantLast30Days', language)}
              </h6>
              <div className="row g-3">
                <div className="col-6">
                  <div className="border rounded p-3">
                    <div className="text-muted small mb-1">{t('tenantTokenUsage', language)}</div>
                    <div className="fw-medium">
                      {formatQuotaForDisplay(
                        stats.quota_usage.monthly_tokens.used / TOKEN_QUOTA_MULTIPLIER,
                        true
                      )}
                    </div>
                    <div className="text-muted small">
                      {t('max', language)}:{' '}
                      {formatQuotaForDisplay(
                        stats.quota_usage.monthly_tokens.limit / TOKEN_QUOTA_MULTIPLIER,
                        true
                      )}
                    </div>
                    <div className="progress mt-2" style={{ height: '4px' }}>
                      <div
                        className={`progress-bar ${
                          stats.quota_usage.monthly_tokens.percentage >= 90
                            ? 'bg-danger'
                            : stats.quota_usage.monthly_tokens.percentage >= 70
                              ? 'bg-warning'
                              : 'bg-success'
                        }`}
                        style={{
                          width: `${Math.min(100, stats.quota_usage.monthly_tokens.percentage)}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
                <div className="col-6">
                  <div className="border rounded p-3">
                    <div className="text-muted small mb-1">{t('tenantRequestUsage', language)}</div>
                    <div className="fw-medium">
                      {stats.quota_usage.monthly_requests.used.toLocaleString()}
                    </div>
                    <div className="text-muted small">
                      {t('max', language)}:{' '}
                      {stats.quota_usage.monthly_requests.limit.toLocaleString()}
                    </div>
                    <div className="progress mt-2" style={{ height: '4px' }}>
                      <div
                        className={`progress-bar ${
                          stats.quota_usage.monthly_requests.percentage >= 90
                            ? 'bg-danger'
                            : stats.quota_usage.monthly_requests.percentage >= 70
                              ? 'bg-warning'
                              : 'bg-success'
                        }`}
                        style={{
                          width: `${Math.min(100, stats.quota_usage.monthly_requests.percentage)}%`,
                        }}
                      />
                    </div>
                  </div>
                </div>
              </div>
              {!stats.billing_cycle?.valid && (
                <small className="text-muted mt-2 d-block">
                  <i className="bi bi-info-circle me-1" />
                  {stats.billing_cycle?.start
                    ? t('tenantBillingCyclePending', language)
                    : t('tenantLast30Days', language)}
                </small>
              )}
            </div>
          </div>
        ) : (
          <div className="text-muted">{t('tenantNoData', language)}</div>
        )}
      </Card>

      {/* Usage Trend Chart */}
      <Card>
        <h5 className="mb-3">{t('tenantUsageTrend', language)}</h5>
        <TenantUsageChart usage={usage} />
      </Card>
    </div>
  );
};
