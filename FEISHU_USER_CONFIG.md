# 飞书用户信息映射配置指南

## 概述

为了在 Messages 页面显示飞书用户的真实姓名（而不是用户 ID 如 `ou_xxxxx`），需要配置飞书开放平台应用并启用用户信息查询功能。

## 当前状态

**已配置应用信息：**
- App ID: `cli_xxxxxxxxxxxxxxxx`
- App Secret: `your_feishu_app_secret_here`

**API 测试结果：**
- ✅ 应用凭证有效（可以获取 access_token）
- ⚠️ 用户信息 API 返回的数据不包含姓名字段

**可能原因：**
1. 应用缺少 `contact:contact:user:readonly` 权限
2. 目标用户是外部联系人
3. 应用未发布或未通过审核

## 解决方案

### 检查应用权限

1. 访问飞书开放平台：https://open.feishu.cn/app
2. 找到应用 `cli_xxxxxxxxxxxxxxxx`
3. 点击"权限管理"
4. 确保已添加以下权限：
   - `contact:contact:user:readonly` - 读取用户信息
5. 如果权限未申请，点击"申请权限"并提交审核
6. 发布应用版本

### 测试权限是否生效

配置完成后，运行以下命令测试：

```bash
cd /Users/rhuang/workspace/ai-token-analyzer
python3 scripts/shared/feishu_user_cache.py test ou_3e479c7f81f8674741d778e8f838f8ed <your_app_id> <your_app_secret>
```

如果返回用户姓名，说明配置成功。

---

## 完整配置步骤（如需重新配置）

### 1. 创建飞书自建应用

1. 访问飞书开放平台：https://open.feishu.cn/app
2. 点击"创建应用"
3. 选择"企业自建应用"
4. 填写应用名称（如：AI Token Analyzer）
5. 创建完成后记录 **App ID** 和 **App Secret**

### 2. 配置应用权限

在应用管理页面：

1. 点击"权限管理"
2. 点击"申请权限"
3. 搜索并添加以下权限：
   - `contact:contact:user:readonly` - 读取用户信息
   - `contact:contact:user:readonly` 下的具体权限：
     - 读取用户姓名
     - 读取用户邮箱
4. 提交审核（如果需要）

### 3. 发布应用

1. 点击"版本管理与发布"
2. 点击"发布版本"
3. 填写版本说明
4. 提交发布

### 4. 配置本地配置文件

编辑 `~/.ai-token-analyzer/config.json`：

```json
{
  "host_name": "your-machine-name",
  "server": {
    "upload_auth_key": "your-auth-key",
    "server_url": "http://server-ip:5001"
  },
  "tools": {
    "openclaw": {
      "enabled": true,
      "token_env": "OPENCLAW_TOKEN",
      "gateway_url": "http://localhost:18789",
      "hostname": "your-machine-name"
    }
  },
  "feishu": {
    "app_id": "cli_xxxxxxxxxxxxxxxx",
    "app_secret": "xxxxxxxxxxxxxxxxxxxxxxxx"
  }
}
```

### 5. 同步配置到远程机器（如果使用远程收集）

如果在远程机器上运行 OpenClaw：

```bash
# 在远程机器上编辑配置
ssh openclaw@<REMOTE_HOST>
nano /home/openclaw/.ai-token-analyzer/config.json

# 或者从本地同步
scp ~/.ai-token-analyzer/config.json openclaw@<REMOTE_HOST>:/home/openclaw/.ai-token-analyzer/
```

## 测试配置

### 测试用户信息查询

```bash
cd /opt/ai-token-analyzer
python3 scripts/shared/feishu_user_cache.py test ou_3e479c7f81f8674741d778e8f838f8ed <your_app_id>
```

### 查看缓存的用户信息

```bash
python3 scripts/shared/feishu_user_cache.py list
```

### 清除缓存

```bash
python3 scripts/shared/feishu_user_cache.py clear
```

## 缓存说明

- 用户信息会缓存在 `~/.ai-token-analyzer/feishu_users.json`
- 缓存有效期：1 小时（3600 秒）
- 缓存避免频繁调用飞书 API

## 效果对比

### 配置前
```
👤 ou_3e479c7f81f8674741d778e8f838f8ed [FEISHU]
你不能打开群里上传的附件文件吗？
```

### 配置后
```
👤 张三 [FEISHU]
你不能打开群里上传的附件文件吗？
```

## 常见问题

### Q: 权限申请失败？
A: 确保应用已发布，并且权限申请已提交审核。

### Q: 获取用户信息返回 403？
A: 检查 App ID 和 App Secret 是否正确，确保应用已发布。

### Q: 缓存文件在哪里？
A: `~/.ai-token-analyzer/feishu_users.json`

### Q: 如何禁用用户信息查询？
A: 从配置文件中删除 `feishu_app_id` 和 `feishu_app_secret` 字段即可。

### Q: API 返回数据但没有姓名？
A: 这可能是因为：
   - 应用缺少 `contact:contact:user:readonly` 权限
   - 目标用户是外部联系人（不在企业组织架构内）
   - 应用未发布或未通过审核

   请检查应用权限设置，确保已添加用户信息读取权限。

## 参考资料

- 飞书开放平台文档：https://open.feishu.cn/document/ukTMukTMukTM/uEjNwUjLxYDM14SM2ATN
- 用户信息 API：https://open.feishu.cn/document/ukTMukTMukTM/uYjNwUjL2YDM14iN2ATN
