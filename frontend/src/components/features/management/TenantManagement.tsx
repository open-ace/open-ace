/**
 * TenantManagement Component - Tenant management page
 *
 * Features:
 * - Tenant list with filters
 * - Create/Edit/Delete tenants
 * - Quota management
 * - Suspend/Activate tenants
 */

import React, { useState, useEffect, useMemo } from 'react';
import { cn } from '@/utils';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import {
  Card,
  StatCard,
  Button,
  Select,
  Loading,
  Error,
  EmptyState,
  Modal,
  TextInput,
  Badge,
} from '@/components/common';
import { tenantApi, type Tenant, type CreateTenantRequest, type UpdateTenantRequest } from '@/api';
import { formatDateTime } from '@/utils';

const STATUS_OPTIONS = [
  { value: '', label: 'All Statuses' },
  { value: 'active', label: 'Active' },
  { value: 'suspended', label: 'Suspended' },
  { value: 'trial', label: 'Trial' },
];

const PLAN_OPTIONS = [
  { value: '', label: 'All Plans' },
  { value: 'standard', label: 'Standard' },
  { value: 'premium', label: 'Premium' },
  { value: 'enterprise', label: 'Enterprise' },
];

export const TenantManagement: React.FC = () => {
  const language = useLanguage();
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [statusFilter, setStatusFilter] = useState('');
  const [planFilter, setPlanFilter] = useState('');

  const [showModal, setShowModal] = useState(false);
  const [showQuotaModal, setShowQuotaModal] = useState(false);
  const [editingTenant, setEditingTenant] = useState<Tenant | null>(null);
  const [formData, setFormData] = useState<CreateTenantRequest>({
    name: '',
    slug: '',
    plan: 'standard',
    contact_email: '',
    contact_name: '',
  });
  const [quotaData, setQuotaData] = useState({ monthly_tokens: 0, monthly_requests: 0 });

  // Fetch tenants
  const fetchTenants = React.useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      const result = await tenantApi.listTenants({
        status: statusFilter || undefined,
        plan: planFilter || undefined,
      });
      setTenants(result.tenants);
    } catch (err) {
      const errorMessage =
        err instanceof Error ? (err as Error).message : 'Failed to fetch tenants';
      setError(errorMessage);
    } finally {
      setIsLoading(false);
    }
  }, [statusFilter, planFilter]);

  useEffect(() => {
    fetchTenants();
  }, [fetchTenants]);

  // Statistics
  const stats = useMemo(() => {
    const total = tenants.length;
    const active = tenants.filter((t) => t.status === 'active').length;
    const suspended = tenants.filter((t) => t.status === 'suspended').length;
    const trial = tenants.filter((t) => t.status === 'trial').length;
    return { total, active, suspended, trial };
  }, [tenants]);

  // Handlers
  const handleOpenCreate = () => {
    setEditingTenant(null);
    setFormData({
      name: '',
      slug: '',
      plan: 'standard',
      contact_email: '',
      contact_name: '',
    });
    setShowModal(true);
  };

  const handleOpenEdit = (tenant: Tenant) => {
    setEditingTenant(tenant);
    setFormData({
      name: tenant.name,
      slug: tenant.slug,
      plan: tenant.plan,
      contact_email: tenant.contact_email ?? '',
      contact_name: tenant.contact_name ?? '',
    });
    setShowModal(true);
  };

  const handleOpenQuota = (tenant: Tenant) => {
    setEditingTenant(tenant);
    setQuotaData({
      monthly_tokens: tenant.quota?.monthly_tokens ?? 100000,
      monthly_requests: tenant.quota?.monthly_requests ?? 10000,
    });
    setShowQuotaModal(true);
  };

  const handleCloseModal = () => {
    setShowModal(false);
    setEditingTenant(null);
  };

  const handleCloseQuotaModal = () => {
    setShowQuotaModal(false);
    setEditingTenant(null);
  };

  const handleSubmit = async () => {
    try {
      if (editingTenant) {
        await tenantApi.updateTenant(editingTenant.id, formData as UpdateTenantRequest);
      } else {
        await tenantApi.createTenant(formData);
      }
      handleCloseModal();
      fetchTenants();
    } catch (err) {
      console.error('Failed to save tenant:', err);
    }
  };

  const handleSaveQuota = async () => {
    if (!editingTenant) return;
    try {
      await tenantApi.updateQuota(editingTenant.id, quotaData);
      handleCloseQuotaModal();
      fetchTenants();
    } catch (err) {
      console.error('Failed to save quota:', err);
    }
  };

  const handleSuspend = async (tenant: Tenant) => {
    if (!window.confirm(t('confirmSuspendTenant', language))) return;
    try {
      await tenantApi.suspendTenant(tenant.id);
      fetchTenants();
    } catch (err) {
      console.error('Failed to suspend tenant:', err);
    }
  };

  const handleActivate = async (tenant: Tenant) => {
    try {
      await tenantApi.activateTenant(tenant.id);
      fetchTenants();
    } catch (err) {
      console.error('Failed to activate tenant:', err);
    }
  };

  const handleDelete = async (tenant: Tenant) => {
    if (!window.confirm(t('confirmDeleteTenant', language))) return;
    try {
      await tenantApi.deleteTenant(tenant.id);
      fetchTenants();
    } catch (err) {
      console.error('Failed to delete tenant:', err);
    }
  };

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

  const getPlanVariant = (plan: string) => {
    switch (plan) {
      case 'enterprise':
        return 'primary';
      case 'premium':
        return 'info';
      default:
        return 'secondary';
    }
  };

  if (isLoading) {
    return <Loading size="lg" text={t('loading', language)} />;
  }

  if (error) {
    return <Error message={error} onRetry={fetchTenants} />;
  }

  return (
    <div className="tenant-management">
      {/* Header */}
      <div className="d-flex justify-content-between align-items-center mb-4">
        <h2>{t('tenantManagement', language)}</h2>
        <Button variant="primary" size="sm" onClick={handleOpenCreate}>
          <i className="bi bi-plus-lg me-1" />
          {t('addTenant', language)}
        </Button>
      </div>

      {/* Statistics */}
      <div className="row g-3 mb-4">
        <div className="col-md-3">
          <StatCard
            label={t('totalTenants', language)}
            value={stats.total.toString()}
            icon={<i className="bi bi-building fs-4" />}
            variant="primary"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('activeTenants', language)}
            value={stats.active.toString()}
            icon={<i className="bi bi-check-circle fs-4" />}
            variant="success"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('suspendedTenants', language)}
            value={stats.suspended.toString()}
            icon={<i className="bi bi-pause-circle fs-4" />}
            variant="danger"
          />
        </div>
        <div className="col-md-3">
          <StatCard
            label={t('trialTenants', language)}
            value={stats.trial.toString()}
            icon={<i className="bi bi-clock fs-4" />}
            variant="warning"
          />
        </div>
      </div>

      {/* Filters */}
      <Card className="mb-4">
        <div className="row g-3">
          <div className="col-md-3">
            <label className="form-label">{t('status', language)}</label>
            <Select options={STATUS_OPTIONS} value={statusFilter} onChange={setStatusFilter} />
          </div>
          <div className="col-md-3">
            <label className="form-label">{t('plan', language)}</label>
            <Select options={PLAN_OPTIONS} value={planFilter} onChange={setPlanFilter} />
          </div>
          <div className="col-md-3 d-flex align-items-end">
            <Button variant="secondary" size="sm" onClick={fetchTenants}>
              <i className="bi bi-arrow-clockwise me-1" />
              {t('refresh', language)}
            </Button>
          </div>
        </div>
      </Card>

      {/* Tenant List */}
      {tenants.length === 0 ? (
        <EmptyState icon="bi-building" title={t('noTenantsFound', language)} />
      ) : (
        <Card>
          <div className="table-responsive">
            <table className="table table-hover">
              <thead>
                <tr>
                  <th>{t('tenantName', language)}</th>
                  <th>{t('slug', language)}</th>
                  <th>{t('plan', language)}</th>
                  <th>{t('status', language)}</th>
                  <th>{t('quotaUsage', language)}</th>
                  <th>{t('createdAt', language)}</th>
                  <th>{t('tableActions', language)}</th>
                </tr>
              </thead>
              <tbody>
                {tenants.map((tenant) => (
                  <tr key={tenant.id}>
                    <td>
                      <strong>{tenant.name}</strong>
                      {tenant.contact_email && (
                        <small className="d-block text-muted">{tenant.contact_email}</small>
                      )}
                    </td>
                    <td>
                      <code>{tenant.slug}</code>
                    </td>
                    <td>
                      <Badge variant={getPlanVariant(tenant.plan)}>{tenant.plan}</Badge>
                    </td>
                    <td>
                      <Badge variant={getStatusVariant(tenant.status)}>{tenant.status}</Badge>
                    </td>
                    <td>
                      {tenant.quota ? (
                        <div>
                          <div className="progress" style={{ height: '6px' }}>
                            <div
                              className={cn(
                                'progress-bar',
                                (tenant.quota.used_tokens / tenant.quota.monthly_tokens) * 100 >= 90
                                  ? 'bg-danger'
                                  : (tenant.quota.used_tokens / tenant.quota.monthly_tokens) *
                                        100 >=
                                      70
                                    ? 'bg-warning'
                                    : 'bg-success'
                              )}
                              style={{
                                width: `${Math.min(100, (tenant.quota.used_tokens / tenant.quota.monthly_tokens) * 100)}%`,
                              }}
                            />
                          </div>
                          <small className="text-muted">
                            {tenant.quota.used_tokens.toLocaleString()} /{' '}
                            {tenant.quota.monthly_tokens.toLocaleString()}
                          </small>
                        </div>
                      ) : (
                        <span className="text-muted">-</span>
                      )}
                    </td>
                    <td>
                      <small>{formatDateTime(tenant.created_at)}</small>
                    </td>
                    <td>
                      <div className="btn-group btn-group-sm">
                        <Button
                          variant="outline-primary"
                          size="sm"
                          onClick={() => handleOpenEdit(tenant)}
                        >
                          <i className="bi bi-pencil" />
                        </Button>
                        <Button
                          variant="outline-secondary"
                          size="sm"
                          onClick={() => handleOpenQuota(tenant)}
                        >
                          <i className="bi bi-sliders" />
                        </Button>
                        {tenant.status === 'suspended' ? (
                          <Button
                            variant="outline-success"
                            size="sm"
                            onClick={() => handleActivate(tenant)}
                          >
                            <i className="bi bi-play" />
                          </Button>
                        ) : (
                          <Button
                            variant="outline-warning"
                            size="sm"
                            onClick={() => handleSuspend(tenant)}
                          >
                            <i className="bi bi-pause" />
                          </Button>
                        )}
                        <Button
                          variant="outline-danger"
                          size="sm"
                          onClick={() => handleDelete(tenant)}
                        >
                          <i className="bi bi-trash" />
                        </Button>
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}

      {/* Create/Edit Modal */}
      <Modal
        isOpen={showModal}
        onClose={handleCloseModal}
        title={editingTenant ? t('editTenant', language) : t('addTenant', language)}
        size="md"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseModal}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSubmit}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSubmit();
          }}
        >
          <div className="row g-3">
            <div className="col-12">
              <label className="form-label">{t('tenantName', language)} *</label>
              <TextInput
                value={formData.name}
                onChange={(value: string) => setFormData({ ...formData, name: value })}
                placeholder={t('enterTenantName', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('slug', language)}</label>
              <TextInput
                value={formData.slug ?? ''}
                onChange={(value: string) => setFormData({ ...formData, slug: value })}
                placeholder={t('enterSlug', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('plan', language)}</label>
              <Select
                options={[
                  { value: 'standard', label: 'Standard' },
                  { value: 'premium', label: 'Premium' },
                  { value: 'enterprise', label: 'Enterprise' },
                ]}
                value={formData.plan ?? 'standard'}
                onChange={(value) =>
                  setFormData({ ...formData, plan: value as 'standard' | 'premium' | 'enterprise' })
                }
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('contactEmail', language)}</label>
              <TextInput
                value={formData.contact_email ?? ''}
                onChange={(value: string) => setFormData({ ...formData, contact_email: value })}
                placeholder={t('enterEmail', language)}
              />
            </div>
            <div className="col-md-6">
              <label className="form-label">{t('contactName', language)}</label>
              <TextInput
                value={formData.contact_name ?? ''}
                onChange={(value: string) => setFormData({ ...formData, contact_name: value })}
                placeholder={t('enterContactName', language)}
              />
            </div>
          </div>
        </form>
      </Modal>

      {/* Quota Modal */}
      <Modal
        isOpen={showQuotaModal}
        onClose={handleCloseQuotaModal}
        title={t('editQuota', language)}
        size="sm"
        footer={
          <>
            <Button variant="secondary" onClick={handleCloseQuotaModal}>
              {t('cancel', language)}
            </Button>
            <Button variant="primary" onClick={handleSaveQuota}>
              {t('save', language)}
            </Button>
          </>
        }
      >
        <form
          onSubmit={(e) => {
            e.preventDefault();
            handleSaveQuota();
          }}
        >
          <div className="row g-3">
            <div className="col-12">
              <label className="form-label">{t('monthlyTokens', language)}</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.monthly_tokens}
                onChange={(e) =>
                  setQuotaData({ ...quotaData, monthly_tokens: parseInt(e.target.value) || 0 })
                }
              />
            </div>
            <div className="col-12">
              <label className="form-label">{t('monthlyRequests', language)}</label>
              <input
                type="number"
                className="form-control"
                value={quotaData.monthly_requests}
                onChange={(e) =>
                  setQuotaData({ ...quotaData, monthly_requests: parseInt(e.target.value) || 0 })
                }
              />
            </div>
          </div>
        </form>
      </Modal>
    </div>
  );
};
