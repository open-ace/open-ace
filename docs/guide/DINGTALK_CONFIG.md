# DingTalk Integration Configuration — 钉钉集成配置

[English](#english) | [中文](#中文)

---

## English

This guide explains how to configure DingTalk integration for:

- imported-session user and group name resolution
- local org sync of DingTalk departments and users into Open ACE teams and users
- alert delivery to DingTalk custom robot webhooks

## Overview

Open ACE integrates with DingTalk to:

- Display real DingTalk user names instead of raw `userId` values
- Display DingTalk group names instead of raw `chatId` metadata when available
- Sync DingTalk departments into Open ACE collaboration teams
- Sync DingTalk users into local users + team memberships
- Send alert center notifications to DingTalk group robots

Current scope:

- OpenClaw message import path
- DingTalk user name resolution
- DingTalk group name resolution when session metadata contains a DingTalk `chatId`
- Manual and optionally scheduled DingTalk organization sync
- DingTalk custom robot webhook notification payloads

## Prerequisites

1. A DingTalk developer account
2. An internal enterprise application with API access
3. An AppKey and AppSecret for that application
4. Optional: a DingTalk custom robot webhook URL for alert delivery

## Setup

### 1. Create a DingTalk app

1. Visit the DingTalk Open Platform
2. Create an internal enterprise application
3. Save the **AppKey** and **AppSecret**

### 2. Configure Open ACE

Edit `~/.open-ace/config.json`:

```json
{
  "dingtalk": {
    "app_key": "dingxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here",
    "org_sync_enabled": true,
    "org_sync_tenant_id": 1,
    "org_sync_interval_minutes": 60,
    "org_sync_root_dept_id": "1"
  }
}
```

### 3. Test name-resolution configuration

```bash
# Test user name lookup
python3 scripts/shared/dingtalk_user_cache.py test manager123 <app_key> <app_secret>

# Test group name lookup
python3 scripts/shared/dingtalk_group_cache.py test chatabcd1234 <app_key> <app_secret>
```

### 4. Sync the organization structure

After saving the config:

1. Restart Open ACE if you changed `config.json`
2. Call `POST /api/admin/dingtalk/sync` as an administrator, optionally with `{"tenant_id": 1}`

When `dingtalk.org_sync_enabled=true`, the background data-fetch scheduler also performs periodic sync based on `dingtalk.org_sync_interval_minutes`.

Current sync behavior:

- departments are mirrored into collaboration `teams`
- users are provisioned into local `users`
- DingTalk identities are linked through `sso_identities`
- team memberships are reconciled for DingTalk-managed teams

Current non-goals:

- disabling or deleting local users when they disappear from DingTalk
- removing DingTalk-managed teams automatically when departments disappear
- DingTalk SSO login flow
- inbound DingTalk chatbot commands

## config.json vs the `dingtalk_settings` Table

There are two configuration channels; know which one you are using:

| | `config.json` (`dingtalk` section) | `dingtalk_settings` table |
|---|---|---|
| **Managed by** | Server operators, by editing the file | System administrators, via the admin UI backed by `/api/management/dingtalk-config` (admin-only GET/PUT/DELETE plus `POST .../test`) |
| **Secret storage** | Plaintext `app_secret` (and `alerts.dingtalk_webhook_secret`) in the file | Encrypted at rest (`app_secret_enc`, `fallback_webhook_secret_enc`, Fernet); the API only returns masked "configured" indicators, never the secrets |
| **Change flow** | Edit file + restart | UI save + test connection; no restart needed |
| **Fields** | `app_key`, `app_secret`, `org_sync_*`, `org_sync_root_dept_id` | `app_key`, `app_secret`, `fallback_webhook_secret`, `sync_enabled`, `target_tenant_id`, `interval_minutes`, `root_dept_id`, `max_runtime_seconds`, `auto_recovery` |

Behavior at runtime (verified against `app/services/dingtalk_org_sync.py` and `app/repositories/notification_settings_repository.py`):

- **One-time legacy import**: the first time the settings are read, if no `dingtalk_settings` row exists yet, the `dingtalk` section of `config.json` (plus the global `alerts.dingtalk_webhook_secret`) is imported into the table once and recorded in `config_import_state`. From then on `config.json` is not re-imported, and later edits to the file have no effect on stored settings.
- **Priority**: when both exist, the database row wins field by field; `config.json` only supplies values while no database row exists.
- **Deleting via the admin API** removes the row and writes a tombstone that prevents the legacy `config.json` values from being silently re-imported.

Security recommendation: prefer the management-UI (database) channel, especially in production — it keeps the AppSecret and the robot signing secret out of plaintext config files. After the one-time import has run (or once you have saved credentials through the UI), remove the plaintext secrets from `config.json`. If the instance encryption key is later rotated, the stored secrets become undecryptable and the connection test fails with a secret-decryption error; re-enter and save them to fix it.

### 5. Configure DingTalk robot alerts

In `Manage -> Quota Alerts -> Notification Preferences`, set the webhook URL to a DingTalk custom robot URL such as:

```text
https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
```

Open ACE sends DingTalk-compatible `text` payloads for alert notifications. If the DingTalk robot requires signing, the signing secret is resolved by priority: (1) **a per-user secret** — save `openace_dingtalk_secret=<secret>` in the webhook URL under your alert preferences and Open ACE encrypts it (Fernet/AES) into that user's preferences, so each tenant/user signs with their own key, isolated from others; (2) **the global** `alerts.dingtalk_webhook_secret` in `config.json` (shared across all users); (3) `openace_dingtalk_secret=<secret>` carried in the saved URL (legacy fallback). Open ACE strips that parameter before sending and adds DingTalk's `timestamp` / `sign` parameters. The per-user secret is stored only as ciphertext and decrypted solely at signing time; it is never persisted in plaintext or echoed back.

## Cache management

| Command | Description |
|---------|-------------|
| `python3 scripts/shared/dingtalk_user_cache.py list` | List cached users |
| `python3 scripts/shared/dingtalk_user_cache.py clear` | Clear user cache |
| `python3 scripts/shared/dingtalk_group_cache.py list` | List cached groups |
| `python3 scripts/shared/dingtalk_group_cache.py clear` | Clear group cache |

Cache files:

- `~/.open-ace/dingtalk_users.json`
- `~/.open-ace/dingtalk_groups.json`

## Troubleshooting

### User names are not resolving

- Check that the DingTalk app credentials are correct
- Verify the app can call user-detail APIs
- Confirm the imported session metadata contains a DingTalk `sender_id`

### Group names are not resolving

- Confirm the imported session metadata contains a DingTalk `chatId`
- Check that the app can call group-info APIs
- Verify the cached label includes a `chat...` identifier

## Disabling integration

To disable DingTalk integration, remove the `dingtalk` section from your config file. To keep name resolution but stop scheduled org sync, set `dingtalk.org_sync_enabled=false`.

---

## 中文

本指南说明如何配置钉钉集成，实现以下能力：

- 导入会话时把钉钉用户/群组 ID 解析成人名/群名
- 将钉钉组织架构同步到 Open ACE 的本地用户与团队
- 将告警中心通知推送到钉钉自定义机器人 webhook

## 概述

Open ACE 与钉钉集成，支持：

- 显示真实的钉钉用户名，而不是原始 `userId`
- 在元数据包含群 `chatId` 时，显示钉钉群名称，而不是原始群标识
- 将钉钉部门同步为 Open ACE 协作团队
- 将钉钉用户同步为本地用户与团队成员关系
- 向钉钉群机器人推送告警中心通知

当前支持范围：

- OpenClaw 消息导入链路
- 钉钉用户名解析
- 元数据包含 DingTalk `chatId` 时的群名解析
- 手动或按配置定时同步钉钉组织架构
- 钉钉自定义机器人 webhook 告警 payload

## 前置条件

1. 一个钉钉开发者账号
2. 一个具有 API 调用权限的企业内部应用
3. 该应用的 AppKey 和 AppSecret
4. 可选：用于告警推送的钉钉自定义机器人 webhook URL

## 配置步骤

### 1. 创建钉钉应用

1. 打开钉钉开放平台
2. 创建企业内部应用
3. 保存 **AppKey** 和 **AppSecret**

### 2. 配置 Open ACE

编辑 `~/.open-ace/config.json`：

```json
{
  "dingtalk": {
    "app_key": "dingxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here",
    "org_sync_enabled": true,
    "org_sync_tenant_id": 1,
    "org_sync_interval_minutes": 60,
    "org_sync_root_dept_id": "1"
  }
}
```

### 3. 测试名称解析配置

```bash
# 测试用户名解析
python3 scripts/shared/dingtalk_user_cache.py test manager123 <app_key> <app_secret>

# 测试群名解析
python3 scripts/shared/dingtalk_group_cache.py test chatabcd1234 <app_key> <app_secret>
```

### 4. 同步组织架构

保存配置后：

1. 如果修改了 `config.json`，先重启 Open ACE
2. 管理员调用 `POST /api/admin/dingtalk/sync`，可选请求体为 `{"tenant_id": 1}`

当 `dingtalk.org_sync_enabled=true` 时，后台数据抓取调度器还会按照 `dingtalk.org_sync_interval_minutes` 周期性执行自动同步。

当前同步行为：

- 部门会映射为协作 `teams`
- 用户会同步到本地 `users`
- 钉钉身份会写入 `sso_identities`
- 钉钉管理的团队成员关系会按最新组织结构对齐

当前暂不处理：

- 用户从钉钉消失后自动禁用/删除本地账号
- 部门删除后自动删除本地团队
- 钉钉 SSO 登录流程
- 入站钉钉机器人命令

## config.json 与 `dingtalk_settings` 表

系统存在两条配置通道，请确认自己使用的是哪一条：

| | `config.json`（`dingtalk` 段） | `dingtalk_settings` 表 |
|---|---|---|
| **管理方式** | 服务器运维人员编辑文件 | 系统管理员通过管理界面，底层为 `/api/management/dingtalk-config`（仅管理员可用的 GET/PUT/DELETE 及 `POST .../test`） |
| **密钥存储** | 文件中的明文 `app_secret`（以及 `alerts.dingtalk_webhook_secret`） | 落库加密（`app_secret_enc`、`fallback_webhook_secret_enc`，Fernet）；接口只返回"已配置"掩码标志，绝不返回密钥本身 |
| **变更流程** | 改文件 + 重启 | 界面保存并测试连接；无需重启 |
| **字段** | `app_key`、`app_secret`、`org_sync_*`、`org_sync_root_dept_id` | `app_key`、`app_secret`、`fallback_webhook_secret`、`sync_enabled`、`target_tenant_id`、`interval_minutes`、`root_dept_id`、`max_runtime_seconds`、`auto_recovery` |

运行时行为（依据 `app/services/dingtalk_org_sync.py` 与 `app/repositories/notification_settings_repository.py` 的实际实现）：

- **一次性旧配置导入**：首次读取配置时，若 `dingtalk_settings` 表还没有记录，会把 `config.json` 的 `dingtalk` 段（以及全局的 `alerts.dingtalk_webhook_secret`）一次性导入表中，并在 `config_import_state` 里登记。此后不再重复导入，之后再修改该文件也不会影响已存储的配置。
- **优先级**：两者同时存在时，数据库记录逐字段覆盖；`config.json` 只在数据库尚无记录时提供取值。
- **通过管理端 API 删除**会同时删除记录并写入 tombstone，防止旧 `config.json` 中的值被静默重新导入。

安全建议：优先使用管理界面（数据库）通道，生产环境尤其如此——它让 AppSecret 与机器人加签密钥不再以明文出现在配置文件里。一次性导入完成（或通过界面保存过凭证）之后，请把 `config.json` 中的明文密钥删除。若之后轮换了实例加密密钥，已存储的密钥将无法解密，连接测试会因密钥解密失败而报错；重新录入并保存即可恢复。

### 5. 配置钉钉机器人告警

在 `管理 -> 配额告警 -> 通知偏好` 中，将 webhook URL 设置为钉钉自定义机器人 URL，例如：

```text
https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
```

Open ACE 会为告警通知发送钉钉兼容的 `text` payload。如果钉钉机器人启用了加签，加签密钥按以下优先级解析：(1) **按用户密钥**——在「告警偏好」里把 `openace_dingtalk_secret=<secret>` 写进 webhook URL 保存，Open ACE 会把该密钥加密（Fernet/AES）存入当前用户的偏好，每个租户/用户使用各自的密钥、彼此隔离；(2) **全局** `alerts.dingtalk_webhook_secret`（在 `config.json` 中设置，所有用户共用）；(3) 保存的 URL 中的 `openace_dingtalk_secret=<secret>`（兼容回退）。Open ACE 发送前会移除该参数，并自动追加钉钉要求的 `timestamp` / `sign` 参数；按用户密钥仅以密文落库、仅在签名时解密使用，不会以明文持久化或回显。

## 缓存管理

| 命令 | 说明 |
|------|------|
| `python3 scripts/shared/dingtalk_user_cache.py list` | 列出缓存的用户 |
| `python3 scripts/shared/dingtalk_user_cache.py clear` | 清除用户缓存 |
| `python3 scripts/shared/dingtalk_group_cache.py list` | 列出缓存的群组 |
| `python3 scripts/shared/dingtalk_group_cache.py clear` | 清除群组缓存 |

缓存文件：

- `~/.open-ace/dingtalk_users.json`
- `~/.open-ace/dingtalk_groups.json`

## 故障排查

### 用户名没有解析出来

- 检查钉钉应用凭证是否正确
- 确认应用具备调用用户详情 API 的权限
- 确认导入的会话元数据包含 DingTalk `sender_id`

### 群名没有解析出来

- 确认导入的会话元数据包含钉钉 `chatId`
- 检查应用是否具备调用群信息 API 的权限
- 确认缓存标签中包含 `chat...` 形式的标识

## 禁用集成

如需禁用钉钉集成，请从配置文件中删除 `dingtalk` 配置段。如需保留名称解析但停止定时组织同步，请设置 `dingtalk.org_sync_enabled=false`。
