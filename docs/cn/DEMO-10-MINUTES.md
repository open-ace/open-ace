# 10 分钟演示：从零到远程 AI Coding Agent

本演练带你从空仓库起步，把 Open ACE 跑成一个可在浏览器驱动、运行在**远程**机器上的 AI 编码会话——正是项目成功信号所指向的场景（“新用户能在 10 分钟内本地跑起 Open ACE”）。

它同时也是 [安全模型](SECURITY.md) 的导览：每一步都能看到纵深防御的一层在起作用——默认凭据轮换、一次性机器注册、加密的 API Key 与短期代理令牌。

> **前置条件**
> - Docker 与 Docker Compose
> - 第二台机器（或第二个 VM/容器），网络可达，充当“远程 Agent”宿主
> - 一个 LLM API Key（OpenAI 或 Anthropic）用于驱动编码会话
>
> **没有第二台机器？** 也可以在单机上完成：把 Agent 的 `server_url` 指向 `http://host.docker.internal:5000`，在第二个终端运行 Agent。安全模型完全一致。

---

## 时间线

| 时间 | 步骤 | 对应的安全层 |
|------|------|--------------|
| 0:00 | [启动服务](#1-启动服务-000020) | 生产安全默认值、`SECRET_KEY` |
| 0:20 | [登录并修改默认密码](#2-登录并修改默认密码-020030) | `must_change_password`、bcrypt、登录锁定 |
| 0:30 | [保存 API Key](#3-保存-api-key-030140) | Fernet 静态加密、Key 哈希 |
| 1:40 | [注册远程机器](#4-注册远程机器-140330) | 一次性 256 位注册令牌 |
| 3:30 | [安装远程 Agent](#5-安装远程-agent-330600) | Agent 配置、`skip_ssl_verify` |
| 6:00 | [启动编码会话](#6-启动编码会话-600800) | 代理令牌、LLM 代理流程 |
| 8:00 | [打开浏览器终端](#7-打开浏览器终端-800900) | 会话归属、机器访问控制 |
| 9:00 | [查看审计记录](#8-查看审计记录-9001000) | 审计日志、配额、合规 |

---

## 1. 启动服务 (0:00–0:20)

```bash
git clone https://github.com/open-ace/open-ace.git
cd open-ace
docker compose up -d --build
```

打开 <http://localhost:5000>。种子脚本已创建一个 `admin/admin123` 账号。

> 🔒 **安全提示**：容器在生产环境需要 `SECRET_KEY`。演示中 `docker-compose.yml` 提供占位值；真实部署应设置强且唯一的值（并单独设置 `OPENACE_ENCRYPTION_KEY`）。种子用户带 `must_change_password = True`，默认凭据无法重复使用——第 2 步处理。

## 2. 登录并修改默认密码 (0:20–0:30)

1. 用 `admin` / `admin123` 登录。
2. UI 提示设置新密码——设一个。（若未弹出，前往 **Manage → Users → admin → Change password**。）

> 🔒 **安全提示**：密码使用 **bcrypt（12 轮）** 哈希。连续 5 次登录失败将锁定 15 分钟（可在 `security_settings` 配置）。由于种子强制改密，`admin123` 在首次登录后几秒内即失效。

## 3. 保存 API Key (0:30–1:40)

1. 前往 **Manage → API Keys → Add Key**。
2. 选择供应商（OpenAI 或 Anthropic），粘贴 Key 并保存。

> 🔒 **安全提示**：Key 在写入数据库前用 **Fernet（AES-128-CBC + HMAC-SHA256）** 加密；加密密钥由 `OPENACE_ENCRYPTION_KEY`（或 `SECRET_KEY`）经 SHA-256 派生，永不落库。同时存储 Key 的 SHA-256 哈希以便在不解密的情况下查重。此后明文 Key 仅在代理请求时短暂存在于服务端内存。

## 4. 注册远程机器 (1:40–3:30)

1. 在 **Manage → Remote Machines** 点击 **Register Machine**。
2. 服务端返回**注册令牌**——复制它，它**只显示一次**。

```bash
# 从管理 UI 你会拿到类似令牌：
#   a1b2c3...   （256 位，十六进制）
```

> 🔒 **安全提示**：令牌为 256 位随机数（`secrets.token_hex(32)`）。数据库只存其 **SHA-256 哈希**，因此数据库泄露不会暴露可用令牌。它**一次性使用**、**有效期 1 小时**：Agent 注册的瞬间，令牌被原子地标记为已消费，任何重放都会被拒。生成令牌仅限系统管理员。

## 5. 安装远程 Agent (3:30–6:00)

在 *远程* 机器（Linux/macOS）上运行安装脚本，传入服务端地址与一次性令牌：

```bash
curl -fsSL http://<server-host>:5000/api/remote/agent/install.sh | bash -s -- \
  --server http://<server-host>:5000 \
  --token <registration-token> \
  --name dev-machine
```

安装程序在 `~/.open-ace-agent/config.json` 写入配置，安装一个 CLI 工具（默认 Qwen Code），并启动 Agent 守护进程，回连服务端 WebSocket。

> 🔒 **安全提示**：演示环境下 `skip_ssl_verify` 默认为 `true` 以兼容自签证书。**任何真实部署**，一旦具备有效证书，都应设置 `OPENACE_SKIP_SSL_VERIFY=false`（或 `skip_ssl_verify: false`），否则 Agent 会信任任意被出示的证书。此后 Agent 用注册时获得的长效 **agent token** 鉴权——绝不接触你的 API Key。

## 6. 启动编码会话 (6:00–8:00)

1. 回到浏览器，打开 **Work → New Session**。
2. 选择远程机器（`dev-machine`）、CLI 工具与模型，启动会话。
3. 输入提示词——AI CLI 在远程机器执行并把输出流式回传到浏览器。

> 🔒 **安全提示**：服务端**没有**把真实 API Key 发给远程机器，而是签发了**短期代理令牌**——一个 HMAC-SHA256 签名的 payload（工作区会话默认 15 分钟），携带你的 `user_id`、`session_id`、`tenant_id` 与 provider。CLI 调用模型时：
>
> 1. 以 `Authorization: Bearer <proxy_token>` 请求 `/api/remote/llm-proxy`。
> 2. 服务端校验签名（常数时间比较）与过期时间，并确认会话仍活跃。
> 3. **在内存中解密真实 Key**，替换到 Authorization 头后转发给 LLM 供应商。
> 4. 从响应中解析 Token 用量并计入配额。
>
> **API Key 永不落盘到远程机器。** 构建 CLI 的 `settings.json` 时，服务端会剥离 `OPENAI_API_KEY`、`ANTHROPIC_API_KEY`、base URL 及任何自定义 `envKey`，仅以临时环境变量注入凭据。

## 7. 打开浏览器终端 (8:00–9:00)

在会话页面打开 **Terminal** 标签，即可通过 WebSocket PTY 获得远程机器的交互式 shell。

> 🔒 **安全提示**：会话访问有归属校验——只有你本人、系统管理员或该机器的机器管理员可查看或停止你的会话。`machine_assignments` 表控制用户与机器的绑定；机器管理员可委派，但权限不超出自己的机器。

## 8. 查看审计记录 (9:00–10:00)

1. 打开 **Manage → Audit Logs**，查看注册、会话启动与代理调用等记录。
2. 在 **Manage → Usage / Quota** 确认 Token 消耗已归属到你的会话与租户。

> 🔒 **安全提示**：所有远程操作经 `AuditLogger` 记录。本地与远程会话共用 `quota_usage` 表统一计费，调度器每 60 秒强制租户配额。若启用了内容过滤器，PII 与敏感内容命中也会在此呈现供合规审查。

---

## 你验证了什么

不到 10 分钟，你搭建了一个自托管控制面，它：

- 首次登录即轮换默认凭据，
- 用认证加密保存 API Key，
- 用一次性令牌注册远程机器，
- 在远程机器上运行 AI 编码会话，**全程未把真实 Key 发过去**，并
- 留下了完整、带配额的审计链路。

## 后续

- 按[加固清单](SECURITY.md#9-生产加固清单)锁定生产环境。
- 阅读完整安全模型：[SECURITY.md](SECURITY.md)。
- 深入远程架构：[Remote Workspace](REMOTE-WORKSPACE.md) 与 [Remote Agent](REMOTE-AGENT.md)。
- 在 TLS 后部署：[Deployment](DEPLOYMENT.md) 与 [NGINX](NGINX.md)。
