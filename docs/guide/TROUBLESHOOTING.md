# Troubleshooting Index — 排障索引

[English](#english) | [中文](#中文)

---

## English

This page is the cross-component entry point for troubleshooting an Open ACE deployment. It maps symptoms to the guide that owns the problem. If you already know which component is at fault, go directly to that guide's own **Troubleshooting** section — the linked entries below all have one.

## Symptom Index

| Symptom | Likely component | Go to |
|---------|------------------|-------|
| WebUI iframe is blank; browser console reports mixed content on an HTTPS page | Reverse proxy / webui over HTTPS | [NGINX.md](NGINX.md) — Problem A and "Blank iframe" in Troubleshooting |
| Remote agent stays offline; agent logs show repeated `HTTP 413` on `/api/remote/agent/message` | nginx `client_max_body_size` (default 1 MB) | [NGINX.md](NGINX.md) — Problem E |
| Remote terminal disconnects after exactly 5 minutes of idle time ("Connection closed. Reconnecting...") | nginx `proxy_read_timeout` | [NGINX.md](NGINX.md) — Problem F |
| Remote session API returns "Not Found" when accessed through the proxy | API paths rewritten by global `sub_filter` | [NGINX.md](NGINX.md) — Troubleshooting, "Remote session returns Not Found" |
| Remote machine cannot register: install script returns 404; agent cannot connect; agent stuck in a WebSocket 405 reconnect loop | Remote workspace server side / registration token | [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md) — Troubleshooting |
| Agent won't connect; CLI tool not found; browser terminal not connecting; session sync not working | Remote agent on the machine | [REMOTE_AGENT.md](REMOTE_AGENT.md) — Troubleshooting |
| `alembic upgrade` fails; `/readyz` returns 503 with `schema_version` incompatible / behind head | Database schema migration | [SCHEMA_MIGRATION_GUIDE.md](../dev/SCHEMA_MIGRATION_GUIDE.md) — Troubleshooting; for in-place upgrade failures and rollback see [UPGRADING.md](UPGRADING.md) — Migration Failure Handling |
| Sandbox pod abnormal, workspace creation rejected with fail-closed reason codes | Sandbox backend (legacy / OpenSandbox) | [SANDBOX_BACKENDS.md](../dev/SANDBOX_BACKENDS.md) — section 10, Troubleshooting |
| Service or container fails to start: port already in use, database locked / connection refused, permission issues, code-server installation verification | Deployment / runtime environment | [DEPLOYMENT.md](DEPLOYMENT.md) — Troubleshooting |
| Multi-user WebUI fails to start or a user's workspace instance misbehaves (startup errors, runtime issues) | Multi-user workspace | [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md) — Troubleshooting |
| Backup CronJob fails, restore job errors, backup integrity check fails | Database backup tooling | [DATABASE_BACKUP.md](DATABASE_BACKUP.md) — Recovery procedure and common failures |
| SSO login fails; browser URL carries `?sso_error=<code>` (signature failure, clock skew, `redirect_uri_not_allowed`, ...) | SSO provider configuration | [SSO_CONFIG.md](SSO_CONFIG.md) — Testing and Troubleshooting |
| Feishu / DingTalk user or group names are not resolving in imported sessions | Feishu / DingTalk integration | [FEISHU_CONFIG.md](FEISHU_CONFIG.md) / [DINGTALK_CONFIG.md](DINGTALK_CONFIG.md) — Troubleshooting |
| Pod stuck not ready; liveness/readiness probes failing | Health probes and dependencies | [OPERATIONS.md](OPERATIONS.md) — Health and Monitoring Endpoints |
| Cluster-level deploy, scaling, or ingress problems | Kubernetes manifests | [KUBERNETES.md](KUBERNETES.md) |

## Collecting Diagnostics

Before digging into a specific guide, gather this baseline:

1. **Readiness and liveness** (web service, port 19888):

```bash
curl -s http://localhost:19888/readyz | python3 -m json.tool
curl -s http://localhost:19888/livez
```

   `/readyz` reports each check (`database`, `schema_version`, `config_dir`, `workspace_dir`, `encryption_keys`, `init_status`, `security_mode`, `ssh_sync`, `frontend_build`) and, on 503, an `action` hint — for example `alembic upgrade head` when the schema is behind head.

2. **Security baseline**:

```bash
curl -s http://localhost:19888/security-status | python3 -m json.tool
```

3. **Scheduler worker** (port 9090): `curl http://localhost:9090/health` returns `is_leader`; `/metrics` exposes Prometheus metrics.

4. **Logs**:
   - Docker Compose: `docker compose ps`, then `docker compose logs -f open-ace` and `docker compose logs -f scheduler`. The app also writes to `/app/logs` inside the container, bind-mounted to `./logs` on the host.
   - Kubernetes: `kubectl -n open-ace get pods`, `kubectl -n open-ace describe pod <pod>` (probe failure events), `kubectl -n open-ace logs <pod>`.

5. **SSH sync alert files**: a failed secure SSH sync writes `/var/log/openace/ssh-sync-failure.warning` (human-readable, with remediation hints) and `/var/log/openace/ssh-sync-failure.json` (structured). While the `.warning` file exists, `/readyz` fails its `ssh_sync` check. Details are in `/var/log/openace/ssh-sync.log`. Delete the warning file only after fixing the cause.

---

## 中文

本页是 Open ACE 部署排障的跨组件入口：按症状索引到对应指南。如果已能定位故障组件，请直接查看该指南自带的**排障章节** —— 下表条目均已经过核对，指向真实存在的排障内容。

## 症状索引

| 症状 | 可能组件 | 去哪篇 |
|------|----------|--------|
| WebUI iframe 空白；HTTPS 页面浏览器控制台报混合内容 | 反向代理 / HTTPS 下的 webui | [NGINX.md](NGINX.md) —— 问题 A 与排障节 "Blank iframe" |
| 远程 Agent 一直离线；Agent 日志在 `/api/remote/agent/message` 上反复出现 `HTTP 413` | nginx `client_max_body_size`（默认 1 MB） | [NGINX.md](NGINX.md) —— 问题 E |
| 远程终端空闲恰好 5 分钟后断开（"Connection closed. Reconnecting..."） | nginx `proxy_read_timeout` | [NGINX.md](NGINX.md) —— 问题 F |
| 经代理访问时远程会话 API 返回 "Not Found" | 全局 `sub_filter` 改写了 API 路径 | [NGINX.md](NGINX.md) —— 排障节 "Remote session returns Not Found" |
| 远程机器无法注册：安装脚本 404；Agent 连不上服务器；Agent 陷入 WebSocket 405 重连循环 | 远程工作区服务端 / 注册令牌 | [REMOTE_WORKSPACE.md](REMOTE_WORKSPACE.md) —— 排障节 |
| Agent 无法连接；CLI 工具找不到；浏览器终端连不上；会话同步不工作 | 机器上的远程 Agent | [REMOTE_AGENT.md](REMOTE_AGENT.md) —— 排障节 |
| `alembic upgrade` 失败；`/readyz` 返回 503 且 `schema_version` 不兼容 / 落后 | 数据库 schema 迁移 | [SCHEMA_MIGRATION_GUIDE.md](../dev/SCHEMA_MIGRATION_GUIDE.md) —— Troubleshooting 节；原地升级失败与回滚见 [UPGRADING.md](UPGRADING.md) —— Migration Failure Handling 节 |
| 沙箱 pod 异常、工作区创建被 fail-closed 原因码拒绝 | 沙箱后端（legacy / OpenSandbox） | [SANDBOX_BACKENDS.md](../dev/SANDBOX_BACKENDS.md) —— 第 10 节 故障排查 |
| 服务或容器启动失败：端口被占用、数据库锁死 / 连接失败、权限问题、code-server 安装验证 | 部署 / 运行环境 | [DEPLOYMENT.md](DEPLOYMENT.md) —— 故障排查节 |
| 多用户 WebUI 无法启动或某用户的工作区实例异常（启动错误、运行时问题） | 多用户工作区 | [MULTI_USER_WORKSPACE.md](MULTI_USER_WORKSPACE.md) —— 故障排查节 |
| 备份 CronJob 失败、恢复 Job 报错、备份完整性校验失败 | 数据库备份工具链 | [DATABASE_BACKUP.md](DATABASE_BACKUP.md) —— 恢复流程与常见故障 |
| SSO 登录失败；浏览器 URL 带 `?sso_error=<code>`（签名失败、时钟偏移、`redirect_uri_not_allowed` 等） | SSO Provider 配置 | [SSO_CONFIG.md](SSO_CONFIG.md) —— Testing and Troubleshooting 节 |
| 导入会话中飞书 / 钉钉的用户名或群名没有解析出来 | 飞书 / 钉钉集成 | [FEISHU_CONFIG.md](FEISHU_CONFIG.md) / [DINGTALK_CONFIG.md](DINGTALK_CONFIG.md) —— 排障节 |
| Pod 卡在未就绪；存活 / 就绪探针失败 | 健康探针与依赖 | [OPERATIONS.md](OPERATIONS.md) —— 健康与监控端点 |
| 集群层面的部署、扩缩容或 Ingress 问题 | Kubernetes 清单 | [KUBERNETES.md](KUBERNETES.md) |

## 收集诊断信息

深入具体指南之前，先收集以下基线信息：

1. **就绪与存活**（Web 服务，19888 端口）：

```bash
curl -s http://localhost:19888/readyz | python3 -m json.tool
curl -s http://localhost:19888/livez
```

   `/readyz` 会逐项报告检查结果（`database`、`schema_version`、`config_dir`、`workspace_dir`、`encryption_keys`、`init_status`、`security_mode`、`ssh_sync`、`frontend_build`），503 时附 `action` 提示 —— 例如 schema 落后时提示 `alembic upgrade head`。

2. **安全基线**：

```bash
curl -s http://localhost:19888/security-status | python3 -m json.tool
```

3. **调度器 worker**（9090 端口）：`curl http://localhost:9090/health` 返回 `is_leader`；`/metrics` 暴露 Prometheus 指标。

4. **日志**：
   - Docker Compose：`docker compose ps`，然后 `docker compose logs -f open-ace` 与 `docker compose logs -f scheduler`。应用还会写容器内 `/app/logs`，Compose 中绑定挂载到宿主机 `./logs`。
   - Kubernetes：`kubectl -n open-ace get pods`、`kubectl -n open-ace describe pod <pod>`（探针失败事件）、`kubectl -n open-ace logs <pod>`。

5. **SSH 同步告警文件**：安全 SSH 同步失败时会写 `/var/log/openace/ssh-sync-failure.warning`（人类可读，含修复提示）与 `/var/log/openace/ssh-sync-failure.json`（结构化）。`.warning` 文件存在期间，`/readyz` 的 `ssh_sync` 检查失败。详情见 `/var/log/openace/ssh-sync.log`。修复原因后才可删除告警文件。
