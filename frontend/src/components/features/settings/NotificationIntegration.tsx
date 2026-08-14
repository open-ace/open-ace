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
  const badge = (key: string) => (
    <Badge
      variant={
        ['configured', 'enabled', 'available', 'no_configuration_required'].includes(
          status[key]?.status
        )
          ? 'success'
          : 'warning'
      }
    >
      {t(statusKey(status[key]?.status), language)}
    </Badge>
  );

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

  const cards = [
    ['email', 'integrationEmail', 'integrationEmailDesc'],
    ['webhook', 'integrationWebhook', 'integrationWebhookDesc'],
    ['dingtalk_bot', 'integrationDingTalkBot', 'integrationDingTalkBotDesc'],
    ['feishu_bot', 'integrationFeishuBot', 'integrationFeishuBotDesc'],
  ];

  return (
    <div>
      <h2>{t('notificationIntegration', language)}</h2>
      <p className="text-muted">{t('notificationIntegrationDesc', language)}</p>
      <div className="btn-group mb-4" role="tablist">
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
            {cards.map(([key, title, description]) => (
              <div className="col-md-3" key={key}>
                <Card className="h-100">
                  <button
                    className="btn text-start w-100"
                    onClick={() => open('channels', key.replace('_bot', ''))}
                  >
                    <div className="d-flex justify-content-between">
                      <strong>{t(title, language)}</strong>
                      {badge(key)}
                    </div>
                    <small className="text-muted">{t(description, language)}</small>
                  </button>
                </Card>
              </div>
            ))}
          </div>
          {selected === 'email' && <SmtpConfig />}
          {selected === 'webhook' && (
            <Card title={t('integrationWebhookTitle', language)}>
              <div className="row g-3">
                <div className="col-md-8">
                  <label className="form-label">{t('integrationSigningSecret', language)}</label>
                  <TextInput
                    type="password"
                    value={webhook.webhook_secret}
                    onChange={(value) => setWebhook({ ...webhook, webhook_secret: value })}
                    placeholder={t('integrationSecretKeepHint', language)}
                  />
                </div>
                <Switch
                  id="webhook-enabled"
                  label={t('integrationDeliveryEnabled', language)}
                  checked={webhook.enabled}
                  onChange={(checked) => setWebhook({ ...webhook, enabled: checked })}
                />
                <Switch
                  id="webhook-private"
                  label={t('integrationAllowPrivate', language)}
                  checked={webhook.allow_private_webhook_urls}
                  onChange={(checked) =>
                    setWebhook({ ...webhook, allow_private_webhook_urls: checked })
                  }
                />
                <div>
                  <Button variant="primary" onClick={saveWebhook}>
                    {t('save', language)}
                  </Button>
                </div>
              </div>
            </Card>
          )}
          {selected === 'dingtalk' && (
            <Card title={t('integrationDingTalkBotTitle', language)}>
              <p>{t('integrationBotBoundary', language)}</p>
              <label className="form-label">{t('integrationFallbackSecret', language)}</label>
              <TextInput
                type="password"
                value={dingtalk.fallback_webhook_secret}
                onChange={(value) => setDingtalk({ ...dingtalk, fallback_webhook_secret: value })}
                placeholder={t('integrationSecretKeepHint', language)}
              />
              <div className="mt-3">
                <Button variant="primary" onClick={saveDingTalk}>
                  {t('save', language)}
                </Button>{' '}
                <Link className="btn btn-outline-secondary" to="/manage/quota">
                  {t('integrationGoToNotifications', language)}
                </Link>
              </div>
            </Card>
          )}
          {selected === 'feishu' && (
            <Card title={t('integrationFeishuBotTitle', language)}>
              <p>{t('integrationFeishuNoConfig', language)}</p>
              <Link className="btn btn-primary" to="/manage/quota">
                {t('integrationGoToNotifications', language)}
              </Link>
            </Card>
          )}
        </>
      ) : (
        <>
          <div className="row g-3 mb-4">
            {[
              ['feishu', 'integrationFeishuApp', 'feishu_app'],
              ['dingtalk', 'integrationDingTalkApp', 'dingtalk_app'],
            ].map(([key, title, statusName]) => (
              <div className="col-md-6" key={key}>
                <Card>
                  <button
                    className="btn text-start w-100"
                    onClick={() => open('collaboration', key)}
                  >
                    <div className="d-flex justify-content-between">
                      <strong>{t(title, language)}</strong>
                      {badge(statusName)}
                    </div>
                  </button>
                </Card>
              </div>
            ))}
          </div>
          {selected === 'feishu' && <FeishuConfig />}
          {selected === 'dingtalk' && (
            <Card title={t('integrationDingTalkApp', language)}>
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
                <Switch
                  id="dingtalk-sync"
                  label={t('integrationAutoSync', language)}
                  checked={dingtalk.sync_enabled}
                  onChange={(checked) => setDingtalk({ ...dingtalk, sync_enabled: checked })}
                />
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
                <Field label={t('integrationInterval', language)}>
                  <TextInput
                    type="number"
                    value={String(dingtalk.interval_minutes)}
                    onChange={(value) =>
                      setDingtalk({ ...dingtalk, interval_minutes: Number(value) })
                    }
                  />
                </Field>
                <Field label={t('integrationRootDepartment', language)}>
                  <TextInput
                    value={dingtalk.root_dept_id}
                    onChange={(value) => setDingtalk({ ...dingtalk, root_dept_id: value })}
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
                <Switch
                  id="dingtalk-recovery"
                  label={t('integrationAutoRecovery', language)}
                  checked={dingtalk.auto_recovery}
                  onChange={(checked) => setDingtalk({ ...dingtalk, auto_recovery: checked })}
                />
                <div className="col-12 d-flex gap-2">
                  <Button variant="primary" onClick={saveDingTalk}>
                    {t('save', language)}
                  </Button>
                  <Button
                    variant="outline-secondary"
                    onClick={testDingTalk}
                    loading={busy === 'test'}
                  >
                    {t('testConnection', language)}
                  </Button>
                  <Button
                    variant="outline-warning"
                    onClick={syncDingTalk}
                    loading={busy === 'sync'}
                  >
                    {t('integrationSyncNow', language)}
                  </Button>
                </div>
                {syncResult && (
                  <div className="col-12">
                    <div className="alert alert-success">
                      <strong>{t('integrationSyncResult', language)}:</strong> {syncResult}
                    </div>
                  </div>
                )}
              </div>
            </Card>
          )}
        </>
      )}
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
  <div className="col-md-6 form-check form-switch ps-5 pt-4">
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
