import React, { useEffect, useState } from 'react';
import { useSearchParams, Link } from 'react-router-dom';
import { Card, Badge, Button, TextInput, useToast } from '@/components/common';
import { SmtpConfig } from '@/components/features/management/SmtpConfig';
import { FeishuConfig } from './FeishuConfig';
import { notificationChannelsApi, type ChannelStatus } from '@/api/notificationChannels';

type Section = 'channels' | 'collaboration';

export const NotificationIntegration: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const section = (
    params.get('section') === 'collaboration' ? 'collaboration' : 'channels'
  ) as Section;
  const selected = params.get('channel') ?? params.get('platform') ?? 'email';
  const [status, setStatus] = useState<ChannelStatus>({});
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
    interval_minutes: 60,
    root_dept_id: '1',
  });
  const toast = useToast();

  const refresh = () => notificationChannelsApi.status().then(setStatus);
  useEffect(() => {
    refresh();
    notificationChannelsApi
      .getWebhook()
      .then((v) => v && setWebhook((x) => ({ ...x, ...v, webhook_secret: '' })));
    notificationChannelsApi
      .getDingTalk()
      .then(
        (v) =>
          v && setDingtalk((x) => ({ ...x, ...v, app_secret: '', fallback_webhook_secret: '' }))
      );
  }, []);

  const open = (nextSection: Section, value: string) =>
    setParams(
      nextSection === 'channels'
        ? { section: nextSection, channel: value }
        : { section: nextSection, platform: value }
    );
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
      {status[key]?.status?.replace(/_/g, ' ') || 'loading'}
    </Badge>
  );

  return (
    <div>
      <h2>通知与集成</h2>
      <p className="text-muted">
        管理系统通知渠道和企业协作平台连接。告警接收对象、通知目标及告警等级仍在“配额与告警 →
        通知设置”中配置。
      </p>
      <div className="btn-group mb-4" role="tablist">
        <Button
          variant={section === 'channels' ? 'primary' : 'outline-secondary'}
          onClick={() => open('channels', 'email')}
        >
          通知渠道
        </Button>
        <Button
          variant={section === 'collaboration' ? 'primary' : 'outline-secondary'}
          onClick={() => open('collaboration', 'feishu')}
        >
          协作平台
        </Button>
      </div>
      {section === 'channels' ? (
        <>
          <div className="row g-3 mb-4">
            {[
              ['email', '邮件', 'SMTP 服务器连接'],
              ['webhook', '通用 Webhook', '全局签名与安全策略'],
              ['dingtalk_bot', '钉钉机器人', '平台投递能力'],
              ['feishu_bot', '飞书机器人', '目标地址按用户/租户设置'],
            ].map(([key, title, desc]) => (
              <div className="col-md-3" key={key}>
                <Card className="h-100">
                  <button
                    className="btn text-start w-100"
                    onClick={() => open('channels', key.replace('_bot', ''))}
                  >
                    <div className="d-flex justify-content-between">
                      <strong>{title}</strong>
                      {badge(key)}
                    </div>
                    <small className="text-muted">{desc}</small>
                  </button>
                </Card>
              </div>
            ))}
          </div>
          {selected === 'email' && <SmtpConfig />}
          {selected === 'webhook' && (
            <Card title="通用 Webhook · 全局安全配置">
              <div className="row g-3">
                <div className="col-md-8">
                  <label className="form-label">签名密钥</label>
                  <TextInput
                    type="password"
                    value={webhook.webhook_secret}
                    onChange={(v) => setWebhook({ ...webhook, webhook_secret: v })}
                    placeholder="留空表示保持不变"
                  />
                </div>
                <div className="col-12 form-check form-switch ms-2">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={webhook.enabled}
                    onChange={(e) => setWebhook({ ...webhook, enabled: e.target.checked })}
                  />
                  <label className="form-check-label">启用投递能力</label>
                </div>
                <div className="col-12 form-check form-switch ms-2">
                  <input
                    className="form-check-input"
                    type="checkbox"
                    checked={webhook.allow_private_webhook_urls}
                    onChange={(e) =>
                      setWebhook({ ...webhook, allow_private_webhook_urls: e.target.checked })
                    }
                  />
                  <label className="form-check-label">允许私网地址（存在 SSRF 风险）</label>
                </div>
                <div>
                  <Button
                    variant="primary"
                    onClick={async () => {
                      await notificationChannelsApi.saveWebhook({
                        ...webhook,
                        ...(webhook.webhook_secret ? {} : { webhook_secret: undefined }),
                      });
                      toast.success('保存成功');
                      refresh();
                    }}
                  >
                    保存
                  </Button>
                </div>
              </div>
            </Card>
          )}
          {selected === 'dingtalk' && (
            <Card title="钉钉机器人 · 平台投递能力">
              <p>
                系统设置页不保存目标 URL。推荐按用户/租户配置密钥；系统级密钥仅作为旧配置兼容回退。
              </p>
              <label className="form-label">兼容回退签名密钥</label>
              <TextInput
                type="password"
                value={dingtalk.fallback_webhook_secret}
                onChange={(v) => setDingtalk({ ...dingtalk, fallback_webhook_secret: v })}
                placeholder="留空表示保持不变"
              />
              <div className="mt-3">
                <Button
                  variant="primary"
                  onClick={async () => {
                    await notificationChannelsApi.saveDingTalk({
                      ...dingtalk,
                      ...(dingtalk.fallback_webhook_secret
                        ? {}
                        : { fallback_webhook_secret: undefined }),
                      ...(dingtalk.app_secret ? {} : { app_secret: undefined }),
                    });
                    toast.success('保存成功');
                    refresh();
                  }}
                >
                  保存
                </Button>{' '}
                <Link className="btn btn-outline-secondary" to="/manage/quota">
                  前往通知设置
                </Link>
              </div>
            </Card>
          )}
          {selected === 'feishu' && (
            <Card title="飞书机器人 · 平台投递能力">
              <p>无需系统配置。每个用户或租户在通知设置中配置自己的机器人 Webhook 地址与密钥。</p>
              <Link className="btn btn-primary" to="/manage/quota">
                前往通知设置
              </Link>
            </Card>
          )}
        </>
      ) : (
        <>
          <div className="row g-3 mb-4">
            {[
              ['feishu', '飞书应用', 'feishu_app'],
              ['dingtalk', '钉钉应用', 'dingtalk_app'],
            ].map(([key, title, statusKey]) => (
              <div className="col-md-6" key={key}>
                <Card>
                  <button
                    className="btn text-start w-100"
                    onClick={() => open('collaboration', key)}
                  >
                    <div className="d-flex justify-content-between">
                      <strong>{title} · 组织同步</strong>
                      {badge(statusKey)}
                    </div>
                  </button>
                </Card>
              </div>
            ))}
          </div>
          {selected === 'feishu' && <FeishuConfig />}
          {selected === 'dingtalk' && (
            <Card title="钉钉应用 · 组织同步">
              <div className="row g-3">
                <div className="col-md-6">
                  <label className="form-label">AppKey</label>
                  <TextInput
                    value={dingtalk.app_key}
                    onChange={(v) => setDingtalk({ ...dingtalk, app_key: v })}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">AppSecret</label>
                  <TextInput
                    type="password"
                    value={dingtalk.app_secret}
                    onChange={(v) => setDingtalk({ ...dingtalk, app_secret: v })}
                    placeholder="留空表示保持不变"
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">同步间隔（分钟）</label>
                  <TextInput
                    type="number"
                    value={String(dingtalk.interval_minutes)}
                    onChange={(v) => setDingtalk({ ...dingtalk, interval_minutes: Number(v) })}
                  />
                </div>
                <div className="col-md-6">
                  <label className="form-label">根部门 ID</label>
                  <TextInput
                    value={dingtalk.root_dept_id}
                    onChange={(v) => setDingtalk({ ...dingtalk, root_dept_id: v })}
                  />
                </div>
                <div>
                  <Button
                    variant="primary"
                    onClick={async () => {
                      await notificationChannelsApi.saveDingTalk({
                        ...dingtalk,
                        ...(dingtalk.app_secret ? {} : { app_secret: undefined }),
                        ...(dingtalk.fallback_webhook_secret
                          ? {}
                          : { fallback_webhook_secret: undefined }),
                      });
                      toast.success('保存成功');
                      refresh();
                    }}
                  >
                    保存
                  </Button>
                </div>
              </div>
            </Card>
          )}
        </>
      )}
    </div>
  );
};
