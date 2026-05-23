# 飞书集成配置

> **ACE** = **AI Computing Explorer**

本指南说明如何配置飞书（Lark）集成，以显示真实用户名和群组名而非 ID。

## 概述

Open ACE 可以与飞书集成以实现以下功能：

- 显示真实用户名而非 `ou_xxxxx` ID
- 显示群组名而非 `oc_xxxxx` 标识符

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
| `chat:chat:readonly` | 读取群聊信息 |

3. 如有需要，提交审批
4. 发布应用

### 3. 配置 Open ACE

编辑 `~/.open-ace/config.json`：

```json
{
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "your_app_secret_here"
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

## 禁用集成

要禁用飞书集成，请从配置文件中删除 `feishu` 部分。

## 参考链接

- [飞书开放平台文档](https://open.feishu.cn/document)
- [用户信息 API](https://open.feishu.cn/document/ukTMukTMukTM/uYjNwUjL2YDM14iN2ATN)
- [群聊信息 API](https://open.feishu.cn/document/ukTMukTMukTM/uEjNwUjLxYDM14SM2ATN)
