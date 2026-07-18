# 钉钉集成配置

本指南说明如何配置钉钉集成，实现以下能力：

- 导入会话时把钉钉用户/群组 ID 解析成人名/群名
- 将钉钉组织架构同步到 Open ACE 的本地用户与团队
- 将告警中心通知推送到钉钉自定义机器人 webhook

## 概述

Open ACE 可以调用钉钉 API 以实现：

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

### 5. 配置钉钉机器人告警

在 `管理 -> 配额告警 -> 通知偏好` 中，将 webhook URL 设置为钉钉自定义机器人 URL，例如：

```text
https://oapi.dingtalk.com/robot/send?access_token=xxxxxxxx
```

Open ACE 会为告警通知发送钉钉兼容的 `text` payload。如果钉钉机器人启用了加签，请在 `config.json` 中设置 `alerts.dingtalk_webhook_secret`。如果需要按单个 webhook 配置，也可以在保存的 URL 中加入 `openace_dingtalk_secret=<secret>`；Open ACE 发送前会移除该参数，并自动追加钉钉要求的 `timestamp` / `sign` 参数。

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
