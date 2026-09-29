# Feishu Integration Configuration — 飞书集成配置

[English](#english) | [中文](#中文)

---

## English

> **ACE** = **AI Computing Explorer**

This guide explains how to configure Feishu (Lark) integration for:

- imported-session user and group name resolution
- local org sync of Feishu departments and users into Open ACE teams and users

It does not provide a Feishu SSO login flow or a general-purpose Feishu chatbot.

## Overview

Open ACE can integrate with Feishu to:

- Display real user names instead of `ou_xxxxx` IDs
- Display group names instead of `oc_xxxxx` identifiers
- Sync Feishu departments into Open ACE collaboration teams
- Sync Feishu users into local users + team memberships

## Prerequisites

1. A Feishu developer account
2. Admin access to create a custom app

## Setup

### 1. Create a Feishu App

1. Visit [Feishu Open Platform](https://open.feishu.cn/app)
2. Click "Create App" → "Enterprise Custom App"
3. Fill in app name (e.g., "Open ACE")
4. Save the **App ID** and **App Secret**

### 2. Configure Permissions

In the app settings:

1. Go to "Permissions"
2. Request the following permissions:

| Permission | Description |
|------------|-------------|
| `contact:contact:user:readonly` | Read user information |
| `contact:contact:department:readonly` | Read department information |
| `chat:chat:readonly` | Read chat information |

3. Submit for approval if required
4. Publish the app

### 3. Configure Open ACE

Edit `~/.open-ace/config.json`:

```json
{
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here",
    "org_sync_enabled": true,
    "org_sync_tenant_id": 1,
    "org_sync_interval_minutes": 60
  }
}
```

### 4. Test Configuration

```bash
# Test user info query
python3 scripts/shared/feishu_user_cache.py test ou_xxxxx <app_id> <app_secret>

# Test group info query
python3 scripts/shared/feishu_group_cache.py test chat_xxxxx <app_id> <app_secret>
```

### 5. Sync the organization structure

After saving the config:

1. Restart Open ACE if you changed `config.json`
2. Open `Manage -> Users`
3. Click `Sync Feishu`

When `feishu.org_sync_enabled=true`, the background data-fetch scheduler also performs periodic sync based on `feishu.org_sync_interval_minutes`.

Current sync behavior:

- departments are mirrored into collaboration `teams`
- users are provisioned into local `users`
- Feishu identities are linked through `sso_identities`
- team memberships are reconciled for Feishu-managed teams

Current non-goals:

- disabling or deleting local users when they disappear from Feishu
- removing Feishu-managed teams automatically when departments disappear
- Feishu SSO login flow
- general-purpose Feishu chatbot commands

## config.json vs the `feishu_settings` Table

There are two configuration channels; know which one you are using:

| | `config.json` (`feishu` section) | `feishu_settings` table |
|---|---|---|
| **Managed by** | Server operators, by editing the file | System administrators, via the admin UI (`Manage -> Users -> Feishu settings`) backed by `/api/management/feishu-config` (admin-only GET/PUT/DELETE/test) |
| **Secret storage** | Plaintext `app_secret` in the file | Encrypted at rest (`app_secret_enc`, Fernet); the API only returns a masked indicator, never the secret |
| **Change flow** | Edit file + restart | UI save + test connection; no restart needed; changing credentials resets the stored verification status |

Behavior at runtime (verified against `app/services/feishu_org_sync.py` and `app/repositories/notification_settings_repository.py`):

- **One-time legacy import**: the first time the settings are read, if no `feishu_settings` row exists yet, the `feishu` section of `config.json` is imported into the table once and recorded in `config_import_state`. From then on `config.json` is not re-imported, and later edits to the file have no effect on stored settings.
- **Priority**: when both exist, the database row wins field by field; `config.json` only supplies values while no database row exists.
- **Deleting via the admin UI** removes the row and writes a tombstone that prevents the legacy `config.json` values from being silently re-imported.

Security recommendation: prefer the management-UI (database) channel, especially in production — it keeps the App Secret out of plaintext config files. After the one-time import has run (or once you have saved credentials through the UI), remove the plaintext `app_secret` from `config.json`. If the instance encryption key is later rotated, the stored secret becomes undecryptable and the connection test returns `FEISHU_SECRET_UNREADABLE` (HTTP 409); re-enter and save the App Secret to fix it.

## Cache Management

User and group information is cached to avoid frequent API calls.

| Command | Description |
|---------|-------------|
| `python3 scripts/shared/feishu_user_cache.py list` | List cached users |
| `python3 scripts/shared/feishu_user_cache.py clear` | Clear user cache |
| `python3 scripts/shared/feishu_group_cache.py list` | List cached groups |
| `python3 scripts/shared/feishu_group_cache.py clear` | Clear group cache |

**Cache location**: `~/.open-ace/feishu_users.json` and `~/.open-ace/feishu_groups.json`

**Cache TTL**: 1 hour (3600 seconds)

## Troubleshooting

### User names not showing

1. Check if the app has `contact:contact:user:readonly` permission
2. Ensure the app is published
3. Verify App ID and App Secret are correct
4. Check if the user is an external contact (not in organization)

### Group names not showing

1. Check if the app has `chat:chat:readonly` permission
2. Note: OpenClaw uses internal `oc_` prefixed IDs, not Feishu `chat_` IDs
3. For OpenClaw integration, group names may require additional configuration

### API returns 403

- Verify App ID and App Secret
- Ensure the app is published
- Check if permissions are approved

### Org sync creates fewer users than expected

- Check whether the app can read the relevant departments
- Check whether an existing local user with the same email belongs to another tenant
- Review server logs for `Skipped Feishu user ...` warnings

## Disabling Integration

To disable Feishu integration, remove the `feishu` section from your config file.

## References

- [Feishu Open Platform Documentation](https://open.feishu.cn/document)
- [User Info API](https://open.feishu.cn/document/ukTMukTMukTM/uYjNwUjL2YDM14iN2ATN)
- [Chat Info API](https://open.feishu.cn/document/ukTMukTMukTM/uEjNwUjLxYDM14SM2ATN)

---

## 中文

> **ACE** = **AI Computing Explorer**

本指南说明如何配置飞书（Lark）集成，实现以下两类能力：

- 导入会话时把飞书用户/群组 ID 解析成人名/群名
- 将飞书组织架构同步到 Open ACE 的本地用户与团队

当前不提供飞书 SSO 登录流程，也不是通用飞书聊天机器人。

## 概述

Open ACE 可以与飞书集成以实现以下功能：

- 显示真实用户名而非 `ou_xxxxx` ID
- 显示群组名而非 `oc_xxxxx` 标识符
- 将飞书部门同步为 Open ACE 协作团队
- 将飞书用户同步为本地用户与团队成员关系

## 前提条件

1. 飞书开发者账户
2. 管理员权限以创建自定义应用

## 设置步骤

### 1. 创建飞书应用

1. 访问[飞书开放平台](https://open.feishu.cn/app)
2. 点击"创建应用" → "企业自建应用"
3. 填写应用名称（如"Open ACE"）
4. 保存 **App ID** 和 **App Secret**

### 2. 配置权限

在应用设置中：

1. 进入"权限管理"
2. 申请以下权限：

| 权限 | 说明 |
|------|------|
| `contact:contact:user:readonly` | 读取用户信息 |
| `contact:contact:department:readonly` | 读取部门信息 |
| `chat:chat:readonly` | 读取群聊信息 |

3. 如有需要，提交审批
4. 发布应用

### 3. 配置 Open ACE

编辑 `~/.open-ace/config.json`：

```json
{
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here",
    "org_sync_enabled": true,
    "org_sync_tenant_id": 1,
    "org_sync_interval_minutes": 60
  }
}
```

### 4. 测试配置

```bash
# 测试用户信息查询
python3 scripts/shared/feishu_user_cache.py test ou_xxxxx <app_id> <app_secret>

# 测试群组信息查询
python3 scripts/shared/feishu_group_cache.py test chat_xxxxx <app_id> <app_secret>
```

### 5. 同步组织架构

保存配置后：

1. 如果修改了 `config.json`，先重启 Open ACE
2. 打开 `管理 -> 用户`
3. 点击 `同步飞书`

当 `feishu.org_sync_enabled=true` 时，后台数据抓取调度器还会按照 `feishu.org_sync_interval_minutes` 周期性执行自动同步。

当前同步行为：

- 部门会映射为协作 `teams`
- 用户会同步到本地 `users`
- 飞书身份会写入 `sso_identities`
- 飞书管理的团队成员关系会按最新组织结构对齐

当前暂不处理：

- 用户从飞书消失后自动禁用/删除本地账号
- 部门删除后自动删除本地团队
- 飞书 SSO 登录流程
- 通用飞书聊天机器人命令

## config.json 与 `feishu_settings` 表

系统存在两条配置通道，请确认自己使用的是哪一条：

| | `config.json`（`feishu` 段） | `feishu_settings` 表 |
|---|---|---|
| **管理方式** | 服务器运维人员编辑文件 | 系统管理员通过管理界面（`管理 -> 用户 -> 飞书设置`），底层为 `/api/management/feishu-config`（仅管理员可用的 GET/PUT/DELETE/test） |
| **密钥存储** | 文件中的明文 `app_secret` | 落库加密（`app_secret_enc`，Fernet）；接口只返回掩码标志，绝不返回密钥本身 |
| **变更流程** | 改文件 + 重启 | 界面保存并测试连接；无需重启；变更凭证会自动重置已保存的校验状态 |

运行时行为（依据 `app/services/feishu_org_sync.py` 与 `app/repositories/notification_settings_repository.py` 的实际实现）：

- **一次性旧配置导入**：首次读取配置时，若 `feishu_settings` 表还没有记录，会把 `config.json` 的 `feishu` 段一次性导入表中，并在 `config_import_state` 里登记。此后不再重复导入，之后再修改该文件也不会影响已存储的配置。
- **优先级**：两者同时存在时，数据库记录逐字段覆盖；`config.json` 只在数据库尚无记录时提供取值。
- **通过管理界面删除**会同时删除记录并写入 tombstone，防止旧 `config.json` 中的值被静默重新导入。

安全建议：优先使用管理界面（数据库）通道，生产环境尤其如此——它让 App Secret 不再以明文出现在配置文件里。一次性导入完成（或通过界面保存过凭证）之后，请把 `config.json` 中的明文 `app_secret` 删除。若之后轮换了实例加密密钥，已存储的密钥将无法解密，连接测试会返回 `FEISHU_SECRET_UNREADABLE`（HTTP 409）；重新录入并保存 App Secret 即可恢复。

## 缓存管理

用户和群组信息会被缓存以避免频繁的 API 调用。

| 命令 | 说明 |
|------|------|
| `python3 scripts/shared/feishu_user_cache.py list` | 列出缓存的用户 |
| `python3 scripts/shared/feishu_user_cache.py clear` | 清除用户缓存 |
| `python3 scripts/shared/feishu_group_cache.py list` | 列出缓存的群组 |
| `python3 scripts/shared/feishu_group_cache.py clear` | 清除群组缓存 |

**缓存位置**：`~/.open-ace/feishu_users.json` 和 `~/.open-ace/feishu_groups.json`

**缓存 TTL**：1 小时（3600 秒）

## 故障排查

### 用户名不显示

1. 检查应用是否具有 `contact:contact:user:readonly` 权限
2. 确保应用已发布
3. 验证 App ID 和 App Secret 是否正确
4. 检查用户是否为外部联系人（不在组织内）

### 群组名不显示

1. 检查应用是否具有 `chat:chat:readonly` 权限
2. 注意：OpenClaw 使用内部的 `oc_` 前缀 ID，而非飞书的 `chat_` ID
3. 对于 OpenClaw 集成，群组名可能需要额外配置

### API 返回 403

- 验证 App ID 和 App Secret
- 确保应用已发布
- 检查权限是否已审批

### 组织同步人数少于预期

- 检查应用是否对目标部门有读取权限
- 检查同邮箱的本地用户是否已归属到其他租户
- 查看服务端日志中的 `Skipped Feishu user ...` 告警

## 禁用集成

要禁用飞书集成，请从配置文件中删除 `feishu` 部分。

## 参考链接

- [飞书开放平台文档](https://open.feishu.cn/document)
- [用户信息 API](https://open.feishu.cn/document/ukTMukTMukTM/uYjNwUjL2YDM14iN2ATN)
- [群聊信息 API](https://open.feishu.cn/document/ukTMukTMukTM/uEjNwUjLxYDM14SM2ATN)
