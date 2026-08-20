import React, { useEffect, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { Badge, Button, Card, TextInput, useConfirm, useToast } from '@/components/common';
import { SmtpConfig } from '@/components/features/management/SmtpConfig';
import { notificationChannelsApi, type ChannelStatus } from '@/api/notificationChannels';
import { tenantApi, type Tenant } from '@/api/tenant';
import { useLanguage } from '@/store';
import { t } from '@/i18n';
import { FeishuConfig } from './FeishuConfig';

type Section = 'channels' | 'collaboration';

const statusColor = (status?: string) => {
  switch (status) {
    case 'configured':
    case 'enabled':
    case 'available':
      return '#639922';
    case 'no_configuration_required':
      return '#378ADD';
    case 'needs_configuration':
      return '#EF9F27';
    case 'disabled':
      return '#888780';
    case 'error':
      return '#A32D2D';
    default:
      return '#888780';
  }
};

const statusBadgeVariant = (status?: string) => {
  switch (status) {
    case 'configured':
    case 'enabled':
    case 'available':
    case 'no_configuration_required':
      return 'success';
    case 'needs_configuration':
      return 'warning';
    case 'disabled':
      return 'secondary';
    case 'error':
      return 'danger';
    default:
      return 'warning';
  }
};

export const NotificationIntegration: React.FC = () => {
  const language = useLanguage();
  const toast = useToast();
  const confirm = useConfirm();
  const [params, setParams] = useSearchParams();
  const section: Section = params.get('section') === 'collaboration' ? 'collaboration' : 'channels';
  const selected = params.get('channel') ?? params.get('platform') ?? 'email';
  const [status, setStatus] = useState<ChannelStatus>({});
  const [syncResult, setSyncResult] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [tenants, setTenants] = useState<Tenant[]>([]);
  const [webhookTestUrl, setWebhookTestUrl] = useState('');
  const [dingtalkTestUrl, setDingtalkTestUrl] = useState('');
  const [webhook, setWebhook] = useState({
    webhook_secret: '',
    allow_private_webhook_urls: false,
    enabled: true,
  });
  const [dingtalk, setDingtalk] = useState({
    app_key: '',
    app_secret: '',
    fallback_webhook_secret: '',
    sync_enabled: false,
    target_tenant_id: '',
    interval_minutes: 60,
    root_dept_id: '1',
    max_runtime_seconds: 1800,
    auto_recovery: false,
  });

  const refresh = () => notificationChannelsApi.status().then(setStatus);
  useEffect(() => {
    void refresh();
    void notificationChannelsApi
      .getWebhook()
      .then(
        (value) => value && setWebhook((current) => ({ ...current, ...value, webhook_secret: '' }))
      );
    void notificationChannelsApi.getDingTalk().then((value) => {
      if (!value) return;
      setDingtalk((current) => ({
        ...current,
        ...value,
        target_tenant_id: value.target_tenant_id ? String(value.target_tenant_id) : '',
        app_secret: '',
        fallback_webhook_secret: '',
      }));
    });
    void tenantApi
      .listTenants({ status: 'active', limit: 1000 })
      .then(({ tenants: values }) => setTenants(values))
      .catch(() => setTenants([]));
  }, []);

  const open = (nextSection: Section, value: string) =>
    setParams(
      nextSection === 'channels'
        ? { section: nextSection, channel: value }
        : { section: nextSection, platform: value }
    );

  const statusKey = (value?: string) => {
    const keys: Record<string, string> = {
      configured: 'integrationStatusConfigured',
      enabled: 'integrationStatusEnabled',
      available: 'integrationStatusAvailable',
      no_configuration_required: 'integrationStatusNoConfig',
      disabled: 'integrationStatusDisabled',
      needs_configuration: 'integrationStatusNeedsConfig',
    };
    return keys[value ?? ''] ?? 'loading';
  };

  const saveWebhook = async () => {
    const { webhook_secret, ...rest } = webhook;
    await notificationChannelsApi.saveWebhook(webhook_secret ? { ...rest, webhook_secret } : rest);
    toast.success(t('integrationSaved', language));
    await refresh();
  };

  const dingtalkPayload = () => {
    const { app_secret, fallback_webhook_secret, target_tenant_id, ...rest } = dingtalk;
    return {
      ...rest,
      target_tenant_id: target_tenant_id ? Number(target_tenant_id) : null,
      ...(app_secret ? { app_secret } : {}),
      ...(fallback_webhook_secret ? { fallback_webhook_secret } : {}),
    };
  };

  const saveDingTalk = async () => {
    await notificationChannelsApi.saveDingTalk(dingtalkPayload());
    toast.success(t('integrationSaved', language));
    await refresh();
  };

  const testDingTalk = async () => {
    setBusy('test');
    try {
      const result = await notificationChannelsApi.testDingTalk({
        app_key: dingtalk.app_key || undefined,
        app_secret: dingtalk.app_secret || undefined,
      });
      if (result.success) {
        toast.success(t('integrationTestSuccess', language), result.message);
      } else {
        toast.error(t('integrationTestFailed', language), result.message);
      }
    } finally {
      setBusy(null);
    }
  };

  const syncDingTalk = async () => {
    const tenantId = Number(dingtalk.target_tenant_id);
    if (!Number.isInteger(tenantId) || tenantId <= 0) {
      toast.error(t('validationError', language), t('integrationTenantRequired', language));
      return;
    }
    if (!(await confirm({ message: t('integrationSyncConfirm', language), variant: 'danger' }))) {
      return;
    }
    setBusy('sync');
    try {
      const response = await notificationChannelsApi.syncDingTalk(tenantId);
      setSyncResult(JSON.stringify(response.result));
      toast.success(t('integrationSyncSuccess', language));
    } finally {
      setBusy(null);
    }
  };

  const channelCards = [
    ['email', 'integrationEmail', 'integrationEmailDesc'],
    ['webhook', 'integrationWebhook', 'integrationWebhookDesc'],
    ['dingtalk_bot', 'integrationDingTalkBot', 'integrationDingTalkBotDesc'],
    ['feishu_bot', 'integrationFeishuBot', 'integrationFeishuBotDesc'],
  ] as const;

  const platformCards = [
    ['feishu', 'integrationFeishuApp', 'feishu_app'],
    ['dingtalk', 'integrationDingTalkApp', 'dingtalk_app'],
  ] as const;

  const statusPill = (key: string, titleKey: string) => {
    const st = status[key]?.status || 'needs_configuration';
    return (
      <span
        key={key}
        className="ni-status-pill"
        style={{
          display: 'inline-flex',
          alignItems: 'center',
          gap: 6,
          fontSize: 12.5,
          color: 'var(--text-secondary)',
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusColor(st) }} />
        {t(titleKey, language)} · {t(statusKey(st), language)}
      </span>
    );
  };

  return (
    <div className="notification-integration">
      <style>{`
        .notification-integration .ni-status-bar {
          display: flex;
          flex-wrap: wrap;
          align-items: center;
          gap: 14px;
          padding: 10px 0;
        }
        .notification-integration .ni-tabs {
          display: flex;
          gap: 8px;
        }
        .notification-integration .ni-tabs .btn {
          border-radius: 6px;
        }
        .notification-integration .ni-channel-card {
          background: var(--bg-primary);
          border: 0.5px solid var(--border-color);
          border-left-width: 3px;
          border-radius: 12px;
          padding: 16px 18px;
          cursor: pointer;
          transition: background 0.15s ease;
          height: 100%;
        }
        .notification-integration .ni-channel-card:hover,
        .notification-integration .ni-channel-card.ni-active {
          background: var(--bg-tertiary);
        }
        .notification-integration .ni-channel-card .ni-dot {
          display: inline-block;
          width: 8px;
          height: 8px;
          border-radius: 50%;
          margin-right: 6px;
          vertical-align: middle;
        }
        .notification-integration .ni-channel-card .ni-title {
          display: flex;
          align-items: center;
          font-size: 14px;
        }
        .notification-integration .ni-detail {
          border: 0.5px solid var(--border-color);
          border-left-width: 3px;
          border-radius: 12px;
          padding: 20px 22px;
          background: var(--bg-primary);
        }
        .notification-integration .ni-detail-header {
          display: flex;
          align-items: center;
          gap: 12px;
          padding-bottom: 14px;
          border-bottom: 0.5px solid var(--border-color);
          margin-bottom: 16px;
        }
        .notification-integration .ni-detail-header .ni-icon {
          width: 40px;
          height: 40px;
          border-radius: 10px;
          display: flex;
          align-items: center;
          justify-content: center;
          font-size: 18px;
          flex-shrink: 0;
          font-weight: 500;
        }
        .notification-integration .ni-detail-title {
          font-weight: 500;
          font-size: 14px;
          line-height: 1.4;
        }
        .notification-integration .ni-detail-subtitle {
          font-size: 12.5px;
          color: var(--text-secondary);
        }
        .notification-integration .ni-section {
          font-weight: 500;
          font-size: 13px;
          margin: 20px 0 10px;
          padding-left: 10px;
          border-left: 3px solid #378ADD;
        }
        .notification-integration .ni-metric {
          background: var(--bg-tertiary);
          border-radius: 8px;
          padding: 12px 16px;
        }
        .notification-integration .ni-metric .ni-metric-value {
          font-size: 22px;
          font-weight: 500;
          margin-top: 4px;
        }
        .notification-integration .ni-boundary {
          font-size: 12.5px;
          color: var(--text-secondary);
          line-height: 1.7;
        }
        .notification-integration .ni-hint {
          font-size: 12px;
          color: var(--text-tertiary);
        }
      `}</style>

      <h2>{t('notificationIntegration', language)}</h2>
      <p className="text-muted">{t('notificationIntegrationDesc', language)}</p>

      <div className="ni-status-bar mb-4">
        {channelCards.map(([key, titleKey]) => statusPill(key, titleKey))}
        <span className="ni-hint">{t('integrationStatusBarSource', language)}</span>
      </div>

      <div className="ni-tabs mb-4" role="tablist">
        <Button
          variant={section === 'channels' ? 'primary' : 'outline-secondary'}
          onClick={() => open('channels', 'email')}
        >
          {t('integrationChannels', language)}
        </Button>
        <Button
          variant={section === 'collaboration' ? 'primary' : 'outline-secondary'}
          onClick={() => open('collaboration', 'feishu')}
        >
          {t('integrationCollaboration', language)}
        </Button>
      </div>

      {section === 'channels' ? (
        <>
          <div className="row g-3 mb-4">
            {channelCards.map(([key, titleKey, descriptionKey]) => {
              const channelKey = key.replace('_bot', '');
              const active = selected === channelKey;
              const st = status[key]?.status;
              return (
                <div className="col-md-3" key={key}>
                  <div
                    className={`ni-channel-card ${active ? 'ni-active' : ''}`}
                    style={{ borderLeftColor: statusColor(st) }}
                    onClick={() => open('channels', channelKey)}
                  >
                    <div className="d-flex justify-content-between align-items-start">
                      <div className="ni-title">
                        <span className="ni-dot" style={{ background: statusColor(st) }} />
                        <strong>{t(titleKey, language)}</strong>
                      </div>
                      <Badge variant={statusBadgeVariant(st)}>{t(statusKey(st), language)}</Badge>
                    </div>
                    <small className="text-muted d-block mt-2">{t(descriptionKey, language)}</small>
                  </div>
                </div>
              );
            })}
          </div>

          {selected === 'email' && (
            <div
              className="ni-detail"
              style={{ borderLeftColor: statusColor(status.email?.status) }}
            >
              <div className="ni-detail-header">
                <div className="ni-icon" style={{ background: '#E6F4EA', color: '#1E6F28' }}>
                  ✉
                </div>
                <div>
                  <div className="ni-detail-title">{t('smtpConfiguration', language)}</div>
                  <div className="ni-detail-subtitle">{t('integrationSmtpSubtitle', language)}</div>
                </div>
              </div>
              <SmtpConfig compact />
            </div>
          )}

          {selected === 'webhook' && (
            <Card
              className="ni-detail"
              style={{ borderLeftColor: statusColor(status.webhook?.status) }}
            >
              <div className="ni-detail-header">
                <div className="ni-icon" style={{ background: '#FAEEDA', color: '#854F0B' }}>
                  ↗
                </div>
                <div>
                  <div className="ni-detail-title">{t('integrationWebhookTitle', language)}</div>
                  <div className="ni-detail-subtitle">
                    {t('integrationWebhookSubtitle', language)}
                  </div>
                </div>
              </div>

              <div className="row g-3">
                <div className="col-md-4">
                  <label className="form-label">{t('integrationSigningSecret', language)}</label>
                  <TextInput
                    type="password"
                    value={webhook.webhook_secret}
                    onChange={(value) => setWebhook({ ...webhook, webhook_secret: value })}
                    placeholder={t('integrationSecretKeepHint', language)}
                  />
                </div>
                <div className="col-md-4">
                  <label className="form-label">{t('integrationAllowPrivate', language)}</label>
                  <select
                    className="form-select"
                    value={webhook.allow_private_webhook_urls ? '1' : '0'}
                    onChange={(event) =>
                      setWebhook({
                        ...webhook,
                        allow_private_webhook_urls: event.target.value === '1',
                      })
                    }
                  >
                    <option value="0">{t('integrationWebhookPrivateOff', language)}</option>
                    <option value="1">{t('integrationWebhookPrivateOn', language)}</option>
                  </select>
                </div>
                <div className="col-md-4">
                  <label className="form-label">{t('integrationDeliveryEnabled', language)}</label>
                  <select
                    className="form-select"
                    value={webhook.enabled ? '1' : '0'}
                    onChange={(event) =>
                      setWebhook({ ...webhook, enabled: event.target.value === '1' })
                    }
                  >
                    <option value="1">{t('integrationWebhookStateEnabled', language)}</option>
                    <option value="0">{t('integrationWebhookStateDisabled', language)}</option>
                  </select>
                </div>
              </div>

              <div className="alert alert-warning mt-3 mb-0">
                {t('integrationWebhookSsrfWarning', language)}
              </div>

              <div className="d-flex gap-2 align-items-center mt-3 flex-wrap">
                <Button variant="primary" onClick={saveWebhook}>
                  {t('save', language)}
                </Button>
                <Button
                  variant="outline-secondary"
                  onClick={() => {
                    if (!webhookTestUrl.trim()) {
                      toast.error(
                        t('validationError', language),
                        t('integrationTestEnterUrl', language)
                      );
                      return;
                    }
                    toast.info(
                      t('integrationTestSend', language),
                      `→ ${webhookTestUrl} (${t('integrationComingSoon', language)})`
                    );
                  }}
                  title={t('integrationComingSoon', language)}
                >
                  {t('integrationTestSend', language)} ({t('integrationComingSoon', language)})
                </Button>
                <div className="flex-grow-1" style={{ maxWidth: 360 }}>
                  <TextInput
                    value={webhookTestUrl}
                    onChange={setWebhookTestUrl}
                    placeholder={t('integrationTestUrlPlaceholder', language)}
                  />
                </div>
              </div>
              <div className="ni-hint mt-2">{t('integrationWebhookSecretHint', language)}</div>

              {/* TODO: 对接后端投递日志 API，当前为占位空状态 */}
              <div className="ni-section">{t('integrationDeliveryLog', language)}</div>
              <table className="table table-sm" style={{ fontSize: 12.5 }}>
                <thead>
                  <tr>
                    <th>{t('integrationDeliveryLogTime', language)}</th>
                    <th>{t('integrationDeliveryLogType', language)}</th>
                    <th>{t('integrationDeliveryLogTarget', language)}</th>
                    <th>{t('integrationDeliveryLogStatus', language)}</th>
                    <th>{t('integrationDeliveryLogRetry', language)}</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td colSpan={5} className="text-center text-muted py-3">
                      {t('integrationComingSoon', language)} — API pending
                    </td>
                  </tr>
                </tbody>
              </table>
              <div className="ni-hint">{t('integrationDeliveryLogHashHint', language)}</div>
            </Card>
          )}

          {selected === 'dingtalk' && (
            <Card
              className="ni-detail"
              style={{ borderLeftColor: statusColor(status.dingtalk_bot?.status) }}
            >
              <div className="ni-detail-header">
                <div className="ni-icon" style={{ background: '#E1F5EE', color: '#0F6E56' }}>
                  钉
                </div>
                <div>
                  <div className="ni-detail-title">
                    {t('integrationDingTalkBotTitle', language)}
                  </div>
                  <div className="ni-detail-subtitle">
                    {t('integrationDingTalkBotSubtitle', language)}
                  </div>
                </div>
              </div>

              <label className="form-label">{t('integrationFallbackSecret', language)}</label>
              <div style={{ maxWidth: 340 }}>
                <TextInput
                  type="password"
                  value={dingtalk.fallback_webhook_secret}
                  onChange={(value) => setDingtalk({ ...dingtalk, fallback_webhook_secret: value })}
                  placeholder={t('integrationSecretKeepHint', language)}
                />
              </div>
              <div className="alert alert-info mt-3 mb-0">
                {t('integrationDingTalkBotFallbackHint', language)}
              </div>

              <div className="d-flex gap-2 align-items-center mt-3 flex-wrap">
                <Button variant="primary" onClick={saveDingTalk}>
                  {t('save', language)}
                </Button>
                <Button
                  variant="outline-secondary"
                  onClick={() => {
                    if (!dingtalkTestUrl.trim()) {
                      toast.error(
                        t('validationError', language),
                        t('integrationTestEnterUrl', language)
                      );
                      return;
                    }
                    toast.info(
                      t('integrationTestSend', language),
                      `→ ${dingtalkTestUrl} (${t('integrationComingSoon', language)})`
                    );
                  }}
                  title={t('integrationComingSoon', language)}
                >
                  {t('integrationTestSend', language)} ({t('integrationComingSoon', language)})
                </Button>
                <div className="flex-grow-1" style={{ maxWidth: 360 }}>
                  <TextInput
                    value={dingtalkTestUrl}
                    onChange={setDingtalkTestUrl}
                    placeholder={t('integrationTestUrlPlaceholder', language)}
                  />
                </div>
              </div>
              <div className="ni-hint mt-2">{t('integrationTestSendHint', language)}</div>

              <div className="d-flex justify-content-between align-items-center flex-wrap gap-2 mt-4 p-3 rounded-2 ni-metric">
                <span>{t('integrationConfigureAlertTarget', language)}</span>
                <Link className="btn btn-outline-secondary" to="/manage/quota">
                  {t('integrationGoToNotifications', language)}
                </Link>
              </div>
            </Card>
          )}

          {selected === 'feishu' && (
            <Card
              className="ni-detail"
              style={{ borderLeftColor: statusColor(status.feishu_bot?.status) }}
            >
              <div className="ni-detail-header">
                <div className="ni-icon" style={{ background: '#E6F1FB', color: '#185FA5' }}>
                  飞
                </div>
                <div>
                  <div className="ni-detail-title">{t('integrationFeishuBotTitle', language)}</div>
                  <div className="ni-detail-subtitle">
                    {t('integrationFeishuBotSubtitle', language)}
                  </div>
                </div>
              </div>

              <div className="alert alert-info mb-0">
                {t('integrationFeishuBotNoConfigDetail', language)}
              </div>

              <div className="d-flex justify-content-between align-items-center flex-wrap gap-2 mt-4 p-3 rounded-2 ni-metric">
                <span>{t('integrationConfigureAlertTarget', language)}</span>
                <Link className="btn btn-primary" to="/manage/quota">
                  {t('integrationGoToNotifications', language)}
                </Link>
              </div>
            </Card>
          )}
        </>
      ) : (
        <>
          <div className="row g-3 mb-4">
            {platformCards.map(([key, titleKey, statusName]) => {
              const active = selected === key;
              const st = status[statusName]?.status;
              return (
                <div className="col-md-6" key={key}>
                  <div
                    className={`ni-channel-card ${active ? 'ni-active' : ''}`}
                    style={{ borderLeftColor: statusColor(st) }}
                    onClick={() => open('collaboration', key)}
                  >
                    <div className="d-flex justify-content-between align-items-start">
                      <div className="ni-title">
                        <span className="ni-dot" style={{ background: statusColor(st) }} />
                        <strong>{t(titleKey, language)}</strong>
                      </div>
                      <Badge variant={statusBadgeVariant(st)}>{t(statusKey(st), language)}</Badge>
                    </div>
                    <small className="text-muted d-block mt-2">
                      {key === 'feishu'
                        ? t('integrationFeishuAppDesc', language)
                        : t('integrationDingTalkAppDesc', language)}
                    </small>
                  </div>
                </div>
              );
            })}
          </div>

          {selected === 'feishu' && (
            <Card className="ni-detail" style={{ borderLeftColor: '#378ADD' }}>
              <div className="ni-detail-header">
                <div className="ni-icon" style={{ background: '#E6F1FB', color: '#185FA5' }}>
                  飞
                </div>
                <div>
                  <div className="ni-detail-title">{t('integrationFeishuApp', language)}</div>
                  <div className="ni-detail-subtitle">
                    {t('integrationFeishuAppSubtitle', language)}
                  </div>
                </div>
              </div>
              <FeishuConfig compact />
            </Card>
          )}

          {selected === 'dingtalk' && (
            <Card className="ni-detail" style={{ borderLeftColor: '#0F6E56' }}>
              <div className="ni-detail-header">
                <div className="ni-icon" style={{ background: '#E1F5EE', color: '#0F6E56' }}>
                  钉
                </div>
                <div>
                  <div className="ni-detail-title">{t('integrationDingTalkApp', language)}</div>
                  <div className="ni-detail-subtitle">
                    {t('integrationDingTalkAppSubtitle', language)}
                  </div>
                </div>
              </div>

              <div className="ni-section">{t('integrationConnectionConfig', language)}</div>
              <div className="row g-3">
                <Field label="AppKey">
                  <TextInput
                    value={dingtalk.app_key}
                    onChange={(value) => setDingtalk({ ...dingtalk, app_key: value })}
                  />
                </Field>
                <Field label="AppSecret">
                  <TextInput
                    type="password"
                    value={dingtalk.app_secret}
                    onChange={(value) => setDingtalk({ ...dingtalk, app_secret: value })}
                    placeholder={t('integrationSecretKeepHint', language)}
                  />
                </Field>
              </div>
              <div className="mt-3">
                <Button variant="primary" onClick={saveDingTalk}>
                  {t('save', language)}
                </Button>{' '}
                <Button
                  variant="outline-secondary"
                  onClick={testDingTalk}
                  loading={busy === 'test'}
                >
                  {t('testConnection', language)}
                </Button>
              </div>

              <div className="ni-section">{t('integrationSyncSettings', language)}</div>
              <div className="row g-3">
                <div className="col-md-6">
                  <Switch
                    id="dingtalk-sync"
                    label={t('integrationAutoSync', language)}
                    checked={dingtalk.sync_enabled}
                    onChange={(checked) => setDingtalk({ ...dingtalk, sync_enabled: checked })}
                  />
                </div>
                <Field label={t('integrationTargetTenant', language)}>
                  <select
                    className="form-select"
                    value={dingtalk.target_tenant_id}
                    onChange={(event) =>
                      setDingtalk({ ...dingtalk, target_tenant_id: event.target.value })
                    }
                  >
                    <option value="">{t('integrationSelectTenant', language)}</option>
                    {tenants.map((tenant) => (
                      <option key={tenant.id} value={tenant.id}>
                        {tenant.name}
                      </option>
                    ))}
                  </select>
                </Field>
                <Field label={t('integrationRootDepartment', language)}>
                  <TextInput
                    value={dingtalk.root_dept_id}
                    onChange={(value) => setDingtalk({ ...dingtalk, root_dept_id: value })}
                  />
                </Field>
                <Field label={t('integrationInterval', language)}>
                  <TextInput
                    type="number"
                    value={String(dingtalk.interval_minutes)}
                    onChange={(value) =>
                      setDingtalk({ ...dingtalk, interval_minutes: Number(value) })
                    }
                  />
                </Field>
                <Field label={t('integrationMaxRuntime', language)}>
                  <TextInput
                    type="number"
                    value={String(dingtalk.max_runtime_seconds)}
                    onChange={(value) =>
                      setDingtalk({ ...dingtalk, max_runtime_seconds: Number(value) })
                    }
                  />
                </Field>
                <div className="col-md-6">
                  <Switch
                    id="dingtalk-recovery"
                    label={t('integrationAutoRecovery', language)}
                    checked={dingtalk.auto_recovery}
                    onChange={(checked) => setDingtalk({ ...dingtalk, auto_recovery: checked })}
                  />
                </div>
              </div>

              {/* TODO: 对接后端同步状态 API，当前为占位空状态 */}
              <div className="ni-section">{t('integrationSyncStatusSection', language)}</div>
              <div className="row g-3">
                <div className="col-md-4">
                  <div className="ni-metric">
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      {t('integrationLastSync', language)}
                    </div>
                    <div className="ni-metric-value" style={{ fontSize: 16 }}>
                      —
                    </div>
                  </div>
                </div>
                <div className="col-md-4">
                  <div className="ni-metric">
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      {t('integrationSyncResultLabel', language)}
                    </div>
                    <div className="ni-metric-value" style={{ fontSize: 16 }}>
                      —
                    </div>
                  </div>
                </div>
                <div className="col-md-4">
                  <div className="ni-metric">
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      {t('integrationSyncLock', language)}
                    </div>
                    <div className="ni-metric-value" style={{ fontSize: 16 }}>
                      {t('integrationSyncIdle', language)}
                    </div>
                  </div>
                </div>
              </div>

              <div className="d-flex gap-2 align-items-center mt-3 flex-wrap">
                <Button variant="outline-warning" onClick={syncDingTalk} loading={busy === 'sync'}>
                  {t('integrationSyncNow', language)}
                </Button>
                <span className="ni-hint">{t('integrationSyncMainEntry', language)}</span>
              </div>
              {syncResult && (
                <div className="alert alert-success mt-3 mb-0">
                  <strong>{t('integrationSyncResult', language)}:</strong> {syncResult}
                </div>
              )}
            </Card>
          )}
        </>
      )}

      <div className="ni-boundary alert alert-light border mt-4">
        <div style={{ fontWeight: 500, color: 'var(--text-primary)', marginBottom: 6 }}>
          {t('integrationBoundaryTitle', language)}
        </div>
        {t('integrationBoundaryBody', language)}
        <br />
        {t('integrationBoundaryRecipients', language)}
        <br />
        {t('integrationBoundarySync', language)}
      </div>
    </div>
  );
};

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="col-md-6">
    <label className="form-label">{label}</label>
    {children}
  </div>
);

const Switch: React.FC<{
  id: string;
  label: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}> = ({ id, label, checked, onChange }) => (
  <div className="form-check form-switch">
    <input
      id={id}
      className="form-check-input"
      type="checkbox"
      checked={checked}
      onChange={(event) => onChange(event.target.checked)}
    />
    <label className="form-check-label" htmlFor={id}>
      {label}
    </label>
  </div>
);
