# 钉钉导入解析配置

本指南说明如何配置钉钉导入解析，使 Open ACE 在导入 OpenClaw 会话日志时能够解析钉钉用户名和群名称。

当前范围仅限 OpenClaw 导入链路中的名称解析和本地缓存。钉钉通讯录同步、钉钉群机器人或 webhook 能力尚未实现，后续工作见 [#1785](https://github.com/open-ace/open-ace/issues/1785)。

## 概述

Open ACE 可以调用钉钉 API 以实现：

- 显示真实的钉钉用户名，而不是原始 `userId`
- 在元数据包含群 `chatId` 时，显示钉钉群名称，而不是原始群标识

当前支持范围：

- OpenClaw 消息导入链路
- 钉钉用户名解析
- 元数据包含 DingTalk `chatId` 时的群名解析

当前不包含：

- 将钉钉部门/用户同步为 Open ACE 本地用户和团队
- 钉钉群机器人命令或告警推送

## 前置条件

1. 一个钉钉开发者账号
2. 一个具有 API 调用权限的企业内部应用
3. 该应用的 AppKey 和 AppSecret

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
    "app_secret": "your_app_secret_here"
  }
}
```

### 3. 测试配置

```bash
# 测试用户名解析
python3 scripts/shared/dingtalk_user_cache.py test manager123 <app_key> <app_secret>

# 测试群名解析
python3 scripts/shared/dingtalk_group_cache.py test chatabcd1234 <app_key> <app_secret>
```

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

如需禁用钉钉导入解析，请从配置文件中删除 `dingtalk` 配置段。
