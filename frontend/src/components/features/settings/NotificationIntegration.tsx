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
          color: 'var(--color-text-secondary, #444441)',
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
          background: var(--color-background-primary, #fff);
          border: 0.5px solid var(--color-border-tertiary, #d3d1c7);
          border-left-width: 3px;
          border-radius: 12px;
          padding: 16px 18px;
          cursor: pointer;
          transition: background 0.15s ease;
          height: 100%;
        }
        .notification-integration .ni-channel-card:hover,
        .notification-integration .ni-channel-card.ni-active {
          background: var(--color-background-secondary, #f1efe8);
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
          border: 0.5px solid var(--color-border-tertiary, #d3d1c7);
          border-left-width: 3px;
          border-radius: 12px;
          padding: 20px 22px;
          background: var(--color-background-primary, #fff);
        }
        .notification-integration .ni-detail-header {
          display: flex;
          align-items: center;
          gap: 12px;
          padding-bottom: 14px;
          border-bottom: 0.5px solid var(--color-border-tertiary, #d3d1c7);
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
          color: var(--color-text-secondary, #5f5e5a);
        }
        .notification-integration .ni-section {
          font-weight: 500;
          font-size: 13px;
          margin: 20px 0 10px;
          padding-left: 10px;
          border-left: 3px solid #378ADD;
        }
        .notification-integration .ni-metric {
          background: var(--color-background-secondary, #f1efe8);
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
          color: var(--color-text-secondary, #5f5e5a);
          line-height: 1.7;
        }
        .notification-integration .ni-hint {
          font-size: 12px;
          color: var(--color-text-tertiary, #888780);
        }
      `}</style>

      <h2>{t('notificationIntegration', language)}</h2>
      <p className="text-muted">{t('notificationIntegrationDesc', language)}</p>

      <div className="ni-status-bar mb-4">
        {channelCards.map(([key, titleKey]) => statusPill(key, titleKey))}
        <span className="ni-hint">来自 /api/management/notification-channels/status</span>
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
                  <div className="ni-detail-subtitle">SMTP 服务器连接</div>
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
                    保存至 webhook_settings（数据库），多实例统一读取
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
                    <option value="0">关闭（推荐）</option>
                    <option value="1">开启</option>
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
                    <option value="1">已启用</option>
                    <option value="0">已停用</option>
                  </select>
                </div>
              </div>

              <div className="alert alert-warning mt-3 mb-0">
                开启「私网地址放行」存在 SSRF 风险，将写入审计日志，仅建议在可信内网环境使用
              </div>

              <div className="d-flex gap-2 align-items-center mt-3 flex-wrap">
                <Button variant="primary" onClick={saveWebhook}>
                  {t('save', language)}
                </Button>
                <Button
                  variant="outline-secondary"
                  onClick={() => {
                    if (!webhookTestUrl.trim()) {
                      toast.error(t('validationError', language), '请输入临时测试 URL');
                      return;
                    }
                    toast.info('测试发送', `将向 ${webhookTestUrl} 发送一条测试告警（演示占位）`);
                  }}
                >
                  测试发送
                </Button>
                <div className="flex-grow-1" style={{ maxWidth: 360 }}>
                  <TextInput
                    value={webhookTestUrl}
                    onChange={setWebhookTestUrl}
                    placeholder="临时输入测试 URL，仅本次使用、不保存"
                  />
                </div>
              </div>
              <div className="ni-hint mt-2">
                签名密钥留空 = 保持不变；清除需二次确认。API 永不回显完整密钥（仅返回已配置
                true/false）
              </div>

              <div className="ni-section">投递日志</div>
              <table className="table table-sm" style={{ fontSize: 12.5 }}>
                <thead>
                  <tr>
                    <th>时间</th>
                    <th>类型</th>
                    <th>目标（哈希）</th>
                    <th>状态</th>
                    <th>重试</th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>08-14 14:32</td>
                    <td>配额告警</td>
                    <td>d41d8cd9…</td>
                    <td className="text-success">成功</td>
                    <td>0</td>
                  </tr>
                  <tr>
                    <td>08-14 13:05</td>
                    <td>配额告警</td>
                    <td>a94a8fe5…</td>
                    <td className="text-danger">失败</td>
                    <td>3</td>
                  </tr>
                  <tr>
                    <td>08-14 11:47</td>
                    <td>系统通知</td>
                    <td>0cc175b9…</td>
                    <td className="text-success">成功</td>
                    <td>0</td>
                  </tr>
                </tbody>
              </table>
              <div className="ni-hint">
                URL 仅显示哈希，不明文展示；日志访问按管理员权限与租户作用域过滤
              </div>
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
                    系统设置页不保存目标 URL；目标由用户/租户在「配额与告警 → 通知设置」指定
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
                推荐按用户 /
                租户配置密钥；系统级密钥仅作为旧配置的兼容回退，新配置请优先使用租户级密钥
              </div>

              <div className="d-flex gap-2 align-items-center mt-3 flex-wrap">
                <Button variant="primary" onClick={saveDingTalk}>
                  {t('save', language)}
                </Button>
                <Button
                  variant="outline-secondary"
                  onClick={() => {
                    if (!dingtalkTestUrl.trim()) {
                      toast.error(t('validationError', language), '请输入临时测试 URL');
                      return;
                    }
                    toast.info('测试发送', `将向 ${dingtalkTestUrl} 发送一条测试告警（演示占位）`);
                  }}
                >
                  测试发送
                </Button>
                <div className="flex-grow-1" style={{ maxWidth: 360 }}>
                  <TextInput
                    value={dingtalkTestUrl}
                    onChange={setDingtalkTestUrl}
                    placeholder="临时输入测试 URL，仅本次使用、不保存"
                  />
                </div>
              </div>
              <div className="ni-hint mt-2">
                测试发送：临时输入 URL，仅本次使用、不保存、不改变用户通知偏好，操作写审计
              </div>

              <div className="d-flex justify-content-between align-items-center flex-wrap gap-2 mt-4 p-3 rounded-2 ni-metric">
                <span>配置你的告警目标与个人偏好</span>
                <Link className="btn btn-outline-secondary" to="/manage/quota">
                  {t('integrationGoToNotifications', language)}（配额与告警）
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
                    飞书机器人无系统级必填参数，目标地址按用户 / 租户设置
                  </div>
                </div>
              </div>

              <div className="alert alert-info mb-0">
                无需系统配置。每个用户 / 租户可在「配额与告警 → 通知设置」中配置自己的飞书机器人
                Webhook 地址与密钥
              </div>

              <div className="d-flex justify-content-between align-items-center flex-wrap gap-2 mt-4 p-3 rounded-2 ni-metric">
                <span>配置你的告警目标与个人偏好</span>
                <Link className="btn btn-primary" to="/manage/quota">
                  {t('integrationGoToNotifications', language)}（配额与告警）
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
                      {key === 'feishu' ? '飞书应用凭证与组织同步' : '钉钉应用凭证与组织同步'}
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
                    保存至 feishu_settings（数据库，id=1 系统级单记录）
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
                    保存至 dingtalk_settings（数据库，id=1 系统级单记录）
                  </div>
                </div>
              </div>

              <div className="ni-section">连接配置</div>
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

              <div className="ni-section">同步设置</div>
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

              <div className="ni-section">同步状态</div>
              <div className="row g-3">
                <div className="col-md-4">
                  <div className="ni-metric">
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      最近同步
                    </div>
                    <div className="ni-metric-value" style={{ fontSize: 16 }}>
                      08-14 14:00
                    </div>
                  </div>
                </div>
                <div className="col-md-4">
                  <div className="ni-metric">
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      结果
                    </div>
                    <div className="ni-metric-value text-success" style={{ fontSize: 16 }}>
                      成功 · 8 人
                    </div>
                  </div>
                </div>
                <div className="col-md-4">
                  <div className="ni-metric">
                    <div className="text-muted" style={{ fontSize: 12 }}>
                      同步锁
                    </div>
                    <div className="ni-metric-value" style={{ fontSize: 16 }}>
                      空闲
                    </div>
                  </div>
                </div>
              </div>

              <div className="d-flex gap-2 align-items-center mt-3 flex-wrap">
                <Button variant="outline-warning" onClick={syncDingTalk} loading={busy === 'sync'}>
                  {t('integrationSyncNow', language)}
                </Button>
                <span className="ni-hint">
                  主入口在「租户管理」；此处执行需选择目标租户并二次确认
                </span>
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
        <div style={{ fontWeight: 500, color: 'var(--color-text-primary)', marginBottom: 6 }}>
          职责边界（本期范围）
        </div>
        本页只管系统级「连接能力」：SMTP、Webhook 全局安全配置、飞书/钉钉应用凭证与同步计划。
        <br />
        用户 / 租户的告警目标（邮箱、Webhook URL、机器人地址与密钥）仍在「配额与告警 → 通知设置」；
        <br />
        针对具体租户的立即同步与同步结果处理以「租户管理」为主入口。
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
