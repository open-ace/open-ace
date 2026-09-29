# Application Modules Reference — 应用模块参考

[English](#english) | [中文](#中文)

---

## English

This is the reference for `app/modules/` — the six domain packages that hold the platform's business logic. Each entry gives the module's responsibility, its entry points (routes, services, workers), the invariants its code enforces, and pointers to its tables and topic documents. For the surrounding architecture (blueprints, services, repositories) see [ARCHITECTURE.md](ARCHITECTURE.md); for per-route permissions see [API_PERMISSION_MATRIX.md](API_PERMISSION_MATRIX.md).

> Table references point into [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) by domain group. Its domain map covers all 103 tables; the Alembic migration under `migrations/versions/` that created a table remains the canonical DDL.

| Module | Responsibility | Primary routes |
|--------|----------------|-----------------|
| `analytics` | Usage analytics, ROI, cost optimization | `routes/analytics.py`, `routes/roi.py` |
| `compliance` | Compliance reports, data retention | `routes/compliance.py` |
| `governance` | Audit logging, content filter, quotas, alerts | `routes/governance.py`, `alerts.py`, `quota.py` |
| `policy` | Pluggable policy & approval engine | `routes/policy.py` |
| `sso` | OAuth2 / OIDC / SAML single sign-on | `routes/sso.py` |
| `workspace` | Sessions, remote workspaces, autonomous development | `routes/workspace.py`, `remote.py`, `autonomous.py` |

### analytics

Enterprise usage analytics: trends and anomaly detection, ROI calculation, and cost-optimization suggestions.

**Entry points**

- Routes: `app/routes/analytics.py`, `app/routes/roi.py`

**Key files**: `usage_analytics.py`, `cost_optimizer.py`, `roi_calculator.py`, `efficiency_registry.py`, `efficiency_thresholds.py`, `task_type_inferencer.py`

**Invariants & design constraints**

- Efficiency algorithms are versioned through `efficiency_registry.py`: register → A/B gradual rollout → full release → deprecate → remove (6+ months later); rollback is a version switch, not a code change.
- Every threshold in `efficiency_thresholds.py` carries a documented source (`EXPERIENCE` / `HEURISTIC` / `TO_BE_VALIDATED`) — no unexplained magic numbers.
- Thresholds are task-type aware; `task_type_inferencer.py` classifies tasks by `tool_name` pattern (general / code generation / document analysis / conversation).
- Forecasting (Issue #3244) uses continuous calendar days, excludes the incomplete current day, and returns history-window metadata so consumers can verify the window.

**Tables**: [Statistics](DATABASE_SCHEMA.md#statistics) group — `daily_stats`, `hourly_stats`, `daily_usage`, `usage_summary`, `session_stats` (materialized view).

**Tests**: `tests/unit/test_usage_analytics.py`, `test_cost_optimizer.py`, `test_efficiency_registry.py`, `test_roi_cost_consistency.py`.

**See also**: [TOKEN_ACCOUNTING.md](TOKEN_ACCOUNTING.md) explains where these usage numbers come from.

### compliance

Compliance reporting and data retention: analyzes the audit trail, generates regulatory reports, and enforces retention policies.

**Entry points**

- Routes: `app/routes/compliance.py`

**Key files**: `report.py`, `audit.py`, `retention.py`

**Invariants & design constraints**

- Compliance is a *consumer* of governance's audit trail: `audit.py` analyzes rows written by `app/modules/governance/audit_logger.py`. The writer lives in governance; only the analyzer lives here.
- `retention.py` is mapping-driven: every `data_type` maps to a `(table, time_column)` pair; deletes are counted, support dry-run, and every run is recorded in `retention_history`.
- `report.py` reads existing tables (usage, audit, quota) rather than owning domain tables of its own; reports target enterprise/regulatory shapes (SOX/GDPR/HIPAA-style sections).

**Tables**: reads `audit_logs` ([Governance & Compliance](DATABASE_SCHEMA.md#governance--compliance)); writes `retention_history` ([Sync & Retention Snapshots](DATABASE_SCHEMA.md#sync--retention-snapshots)).

**Tests**: `tests/unit/test_retention.py`, `test_audit_analyzer_2750.py`, `test_report_csv_excel_nested_fields.py`.

### governance

The enterprise governance runtime: audit logging, content filtering (PII detection), quota management, and alerting/notification.

**Entry points**

- Routes: `app/routes/governance.py`, `alerts.py`, `quota.py`, `admin.py`, `auth.py`, plus most admin/config routes (`ai_agent_settings.py`, `feishu_config.py`, `smtp_config.py`, `notification_integrations.py`, `tool_accounts.py`, …)
- Background: `app/services/alert_compensation_worker.py`, `deregister_compensation_worker.py`, `pending_revoke_cleanup.py`, `quota_enforcement_scheduler.py`

**Key files**: `audit_logger.py`, `content_filter.py` + `content_filter_singleton.py`, `quota_manager.py`, `alert_notifier.py`, `alert_transaction_manager.py`, `alert_state_synchronizer.py`

**Invariants & design constraints**

- In audit logs, `resource_id` is ALWAYS a real entity primary key (`user_id`, `rule_id`, `alert_id`, `machine_id`, …) or NULL — never a synthesized token prefix or date hash. Entity-less operations (login/logout/config singletons) correctly leave it NULL.
- `details` is persisted as JSON TEXT and parsed at a single point (`AuditLog._parse_details` via `from_dict`) — do not add `audit_logs` queries that bypass `query()`/`from_dict`.
- `ContentFilter` is a process-wide singleton (Issue #2768) so a rule CRUD refreshes the one cached instance all modules share; in multi-process deployments each process still has its own singleton (cross-process sync is a known follow-up).
- Quota alerts dual-write to both `quota_alerts` and `alerts`: `alert_transaction_manager.py` wraps the pair in a transaction with retry (up to 3) plus a failure-compensation queue; `alert_state_synchronizer.py` re-consistifies the two tables on acknowledge/delete/cleanup.
- `alert_notifier.py` fans alerts out over WebSocket push, email, and webhook.

**Tables**: [Governance & Compliance](DATABASE_SCHEMA.md#governance--compliance) (`audit_logs`, `content_filter_rules`) and [Alerts & Quotas](DATABASE_SCHEMA.md#alerts--quotas) (`alerts`, `quota_usage`, `quota_alerts`, `notification_preferences`).

**Tests**: `tests/unit/test_audit_logger.py`, `test_content_filter.py`, `test_e2e_governance.py`.

### policy

A pluggable, self-contained policy engine (MVP) for remote-agent actions. It owns the durable *decision* lifecycle; the CLI permission request is merely the input event that starts it.

**Entry points**

- Routes: `app/routes/policy.py` (admin CRUD for rules)
- Integration sites elsewhere are 1–2-line guarded `evaluate()` calls at permission/approval points

**Key files**: `evaluator.py`, `models.py`, `repo.py`, `cache.py`, `fingerprint.py`

**Invariants & design constraints**

- Feature-flagged: `policy.enabled` (60s TTL) in `~/.open-ace/config.json`. When off, `get_evaluator()` returns a `NullPolicyEvaluator` and the system behaves exactly as before (real-time manual approval) — every call site can call `evaluate()` unconditionally.
- Fail-closed: `evaluate()` NEVER raises. On internal error it returns `require_human` for tool targets and `deny` for model/provider selection, so a broken rule store can neither auto-allow nor silently select a denied model.
- Deterministic specificity ranking: machine > project > user > team > tenant, then explicit `priority`, then `created_at`; first match wins. No match → `require_human` (tools) / `allow` (model selection).
- Rules are immutable versioned snapshots: an edit inserts a new row (`version + 1`, `is_current = true`) and every decision pins `(rule_id, rule_version)`, so a later edit can never retroactively change a past decision.
- Fingerprints fold only surface forms via versioned normalization profiles (`generic:v1`, `bash-command:v1`, `file-path:v1`) — never make a path or command more permissive.
- Persistence: the decision INSERT and the atomic `consume_decision` UPDATE run synchronously in `repo.py` (never via the async run-timeline writer); decisions are soft-linked to `agent_approvals` by `request_id` (no hard FK — the input event is persisted asynchronously).

**Tables**: `policy_rules` / `policy_decisions` — listed in the "Autonomous Development" group of [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md); canonical DDL in `migrations/versions/20260626_005_add_policy_tables.py`. The evaluation hot path is served from a 60s TTL rule cache (`cache.py`) invalidated by admin CRUD.

**Tests**: `tests/unit/test_policy_evaluator.py`, `test_policy_fingerprint.py`, `test_policy_repo.py`, `test_policy_integration.py`.

### sso

Single sign-on for enterprise authentication: OAuth2, OIDC, and SAML 2.0 behind one provider abstraction.

**Entry points**

- Routes: `app/routes/sso.py`
- Org-sync jobs: `app/services/feishu_org_sync.py`, `dingtalk_org_sync.py`; also `app/scheduler_worker.py`

**Key files**: `manager.py`, `provider.py`, `oauth2.py`, `oidc.py`, `saml.py`, `secret_holder.py`, `exceptions.py`

**Invariants & design constraints**

- One `SSOProvider` contract covers all three protocols; `OIDCProvider` extends the OAuth2 flow with ID-token verification; `SAMLProvider` implements SP metadata / AuthnRequest / ACS with XML signature verification.
- Provider secrets (`client_secret`, …) live in `SecretHolder`, an in-memory holder that prevents accidental logging or serialization.
- `SSOManager` owns the provider lifecycle and authentication sessions; org-sync services go through it rather than touching providers directly.

**Tables**: [SSO](DATABASE_SCHEMA.md#sso) group — `sso_providers`, `sso_identities`, `sso_sessions`.

**Tests**: `tests/unit/test_sso_manager_security.py`, `test_sso_redirect_whitelist_3224.py`, `test_sso_auto_provision_2893.py`.

**See also**: [SSO_CONFIG.md](../guide/SSO_CONFIG.md) for provider configuration.

### workspace

Everything around agent sessions — the "用" (Use) plane of the platform: session persistence, remote workspaces, browser-access proxies, usage recording, and the autonomous-development stack. It is by far the largest module; navigate it as a set of subdomains.

**Sub-structure guide**

- **Session core** — `session_manager.py` (persistence/recovery), `state_sync.py` (real-time sync), `prompt_library.py`, `collaboration.py`, `tool_connector.py`, `tenant_config_cache.py`.
- **Remote workspace** — `remote_agent_manager.py` (agent WebSocket connections, heartbeat, command dispatch), `remote_session_manager.py` (session lifecycle, message forwarding), `session_access.py`, `agent_token.py`, `api_key_proxy.py` + `api_key_router.py`, `llm_proxy_handler.py`, and the usage pipeline `usage_parser.py` → `usage_evidence.py` → `usage_dedup.py` → `usage_sink.py` (plus `usage_diagnostics.py`).
- **Browser-access proxies** — VSCode (`vscode_proxy.py` / `vscode_store.py` / `vscode_ws_bridge.py`) and terminal (`terminal_store.py` / `terminal_ws_bridge.py` / `terminal_relay_store.py`), with `relay_distributed_store.py` providing Redis-backed HA state.
- **`run_timeline/`** — persisted provenance for remote sessions: one durable `agent_runs` record per session, an append-only `agent_run_events` stream, and durable `agent_approvals`.
- **`model_gateway/`** — optional LiteLLM-compatible routing of LLM proxy traffic; see [MODEL_GATEWAY.md](MODEL_GATEWAY.md).
- **`autonomous/`** — autonomous-development orchestration: `orchestrator.py`, `phases/` (development, PR review, acceptance verification, merge), `git_workspace.py`, `github_ops.py`, and the evidence subsystem; `autonomous/sandbox/` holds the execution-isolation backends. See [AUTONOMOUS_DEVELOPMENT.md](AUTONOMOUS_DEVELOPMENT.md) and [SANDBOX_BACKENDS.md](SANDBOX_BACKENDS.md).

**Entry points**

- Routes: `workspace.py`, `remote.py`, `autonomous.py`, `model_gateway.py`, `run_timeline.py`, `api_keys.py`, `usage.py`, `tenant.py`, `workspace_isolation.py`
- Services: `workspace_service.py`, `webui_manager.py`, `webui_sandbox.py`, `autonomous_scheduler.py`, `data_fetch_scheduler.py`, `insights_service.py`, `workspace_isolation_contract.py`
- WebSocket plumbing outside the module: `app/remote_ws_handler.py`, `app/terminal_ws_middleware.py`

**Invariants & design constraints**

- API keys never leave the server: `api_key_proxy.py` stores them encrypted and hands remote agents short-lived proxy tokens, exchanged for real keys only by the server's LLM proxy endpoint.
- Registration tokens are one-time-use (machine enrollment); agent tokens are long-lived Bearer credentials for heartbeat/message endpoints (`agent_token.py`).
- Session authorization is shared code: `session_access.py` centralizes the owner / system-admin / machine-admin check (and remote-user loading) so the remote and run-timeline blueprints cannot drift apart.
- Usage recording is provider-neutral and deduplicated at request level before reaching the unified sink (Issue #2184).
- The run timeline is append-only and self-contained, so the feature can be removed or re-implemented behind an external API with minimal scarring.
- The model gateway preserves quota, usage recording, attribution, and direct-provider behavior; there is a single integration seam in `llm_proxy_handler.handle_llm_proxy_request` (Null/real planner pair with an `is_noop` short-circuit).
- Relay state is tiered: terminal metadata is process-local with TTL cleanup, while cross-pod HA comes from the Redis-backed `relay_distributed_store.py` (circuit breaker, 300s TTL, graceful degradation to in-memory when Redis is down).
- Sandbox callers depend on the `autonomous/sandbox/` package contract surface, never on the internal module layout.

**Tables**: [Messages & Sessions](DATABASE_SCHEMA.md#messages--sessions) (`agent_sessions`, `session_messages`), [Workspace & Projects](DATABASE_SCHEMA.md#workspace--projects) (`prompt_templates`), [Remote Workspace](DATABASE_SCHEMA.md#remote-workspace) (`remote_machines`, `machine_assignments`, `api_key_store`, `proxy_token_jtis`), [Collaboration](DATABASE_SCHEMA.md#collaboration), [Sync & Retention Snapshots](DATABASE_SCHEMA.md#sync--retention-snapshots). Run-timeline and gateway tables (`agent_runs`, `agent_run_events`, `agent_approvals`, `model_gateway_config`) are listed in the "Autonomous Development" / "Other Domains" groups of [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md).

**Tests**: `tests/unit/test_api_key_proxy_*.py`, `tests/unit/test_api_key_router.py`, `tests/autonomous/`, `tests/integration/routes/`.

**See also**: [WORKSPACE_ISOLATION_CAPABILITIES.md](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md), [REMOTE_WORKSPACE.md](../guide/REMOTE_WORKSPACE.md)

---

## 中文

本文是 `app/modules/` 的参考文档——平台业务逻辑所在的 6 个领域包。每个条目给出模块职责、入口（routes / services / workers）、代码约束的核心不变量，以及数据表与专题文档的指针。外围架构（blueprint、services、repositories）见 [ARCHITECTURE.md](ARCHITECTURE.md)；按路由的权限矩阵见 [API_PERMISSION_MATRIX.md](API_PERMISSION_MATRIX.md)。

> 数据表引用按领域分组指向 [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md)。其领域地图覆盖全部 103 张表；建表所用的 `migrations/versions/` 下 Alembic 迁移始终是权威 DDL。

| 模块 | 职责 | 主要路由 |
|------|------|----------|
| `analytics` | 用量分析、ROI、成本优化 | `routes/analytics.py`、`routes/roi.py` |
| `compliance` | 合规报表、数据保留 | `routes/compliance.py` |
| `governance` | 审计日志、内容过滤、配额、告警 | `routes/governance.py`、`alerts.py`、`quota.py` |
| `policy` | 可插拔策略与审批引擎 | `routes/policy.py` |
| `sso` | OAuth2 / OIDC / SAML 单点登录 | `routes/sso.py` |
| `workspace` | 会话、远程工作区、自主开发 | `routes/workspace.py`、`remote.py`、`autonomous.py` |

### analytics

企业用量分析：趋势与异常检测、ROI 计算、成本优化建议。

**入口**

- 路由：`app/routes/analytics.py`、`app/routes/roi.py`

**关键文件**：`usage_analytics.py`、`cost_optimizer.py`、`roi_calculator.py`、`efficiency_registry.py`、`efficiency_thresholds.py`、`task_type_inferencer.py`

**核心不变量与设计约束**

- 效率算法通过 `efficiency_registry.py` 做版本管理：注册 → A/B 灰度 → 全量 → 废弃 → 6 个月以上后移除；回滚是切换版本，不是改代码。
- `efficiency_thresholds.py` 中每个阈值都标注来源（`EXPERIENCE` / `HEURISTIC` / `TO_BE_VALIDATED`）——不允许无解释的魔法数字。
- 阈值感知任务类型；`task_type_inferencer.py` 按 `tool_name` 模式分类（通用 / 代码生成 / 文档分析 / 对话）。
- 预测算法（Issue #3244）使用连续自然日、排除未结束的当天，并返回历史窗口元数据，消费方可核对窗口。

**数据表**：[统计](DATABASE_SCHEMA.md#统计)分组——`daily_stats`、`hourly_stats`、`daily_usage`、`usage_summary`、`session_stats`（物化视图）。

**测试**：`tests/unit/test_usage_analytics.py`、`test_cost_optimizer.py`、`test_efficiency_registry.py`、`test_roi_cost_consistency.py`。

**另见**：[TOKEN_ACCOUNTING.md](TOKEN_ACCOUNTING.md) 解释这些用量数字的来源。

### compliance

合规报表与数据保留：分析审计轨迹、生成合规报告、执行保留策略。

**入口**

- 路由：`app/routes/compliance.py`

**关键文件**：`report.py`、`audit.py`、`retention.py`

**核心不变量与设计约束**

- compliance 是 governance 审计轨迹的*消费方*：`audit.py` 分析由 `app/modules/governance/audit_logger.py` 写入的行。写入方在 governance，这里只有分析方。
- `retention.py` 由映射驱动：每个 `data_type` 映射到 `(table, time_column)` 对；删除先计数、支持 dry-run，每次运行记录到 `retention_history`。
- `report.py` 读取既有表（用量、审计、配额），不拥有自己的领域表；报表面向企业/监管形态（SOX/GDPR/HIPAA 风格的章节）。

**数据表**：读 `audit_logs`（[治理与合规](DATABASE_SCHEMA.md#治理与合规)）；写 `retention_history`（[同步与保留快照](DATABASE_SCHEMA.md#同步与保留快照)）。

**测试**：`tests/unit/test_retention.py`、`test_audit_analyzer_2750.py`、`test_report_csv_excel_nested_fields.py`。

### governance

企业治理运行时：审计日志、内容过滤（PII 检测）、配额管理与告警通知。

**入口**

- 路由：`app/routes/governance.py`、`alerts.py`、`quota.py`、`admin.py`、`auth.py`，以及多数管理/配置路由（`ai_agent_settings.py`、`feishu_config.py`、`smtp_config.py`、`notification_integrations.py`、`tool_accounts.py` 等）
- 后台：`app/services/alert_compensation_worker.py`、`deregister_compensation_worker.py`、`pending_revoke_cleanup.py`、`quota_enforcement_scheduler.py`

**关键文件**：`audit_logger.py`、`content_filter.py` + `content_filter_singleton.py`、`quota_manager.py`、`alert_notifier.py`、`alert_transaction_manager.py`、`alert_state_synchronizer.py`

**核心不变量与设计约束**

- 审计日志的 `resource_id` 永远是真实实体主键（`user_id`、`rule_id`、`alert_id`、`machine_id` 等）或 NULL——绝不合成 token 前缀或日期哈希。无单一实体的操作（登录/登出/配置单例）正确地留 NULL。
- `details` 以 JSON TEXT 持久化，只在单一解析点（`AuditLog._parse_details`，经 `from_dict`）还原——不要新增绕过 `query()`/`from_dict` 的 `audit_logs` 查询。
- `ContentFilter` 是进程级单例（Issue #2768），规则 CRUD 后刷新所有模块共享的同一缓存实例；多进程部署下每个进程仍有独立单例（跨进程同步是已知后续项）。
- 配额告警双写 `quota_alerts` 与 `alerts` 两表：`alert_transaction_manager.py` 用事务包裹，含重试（最多 3 次）与失败补偿队列；`alert_state_synchronizer.py` 在确认/删除/清理时重新对齐两表。
- `alert_notifier.py` 通过 WebSocket 推送、邮件、webhook 三种渠道分发告警。

**数据表**：[治理与合规](DATABASE_SCHEMA.md#治理与合规)（`audit_logs`、`content_filter_rules`）与[告警与配额](DATABASE_SCHEMA.md#告警与配额)（`alerts`、`quota_usage`、`quota_alerts`、`notification_preferences`）。

**测试**：`tests/unit/test_audit_logger.py`、`test_content_filter.py`、`test_e2e_governance.py`。

### policy

面向 remote-agent 动作的可插拔、自包含策略引擎（MVP）。它持有持久的*决策*生命周期；CLI 权限请求只是触发它的输入事件。

**入口**

- 路由：`app/routes/policy.py`（规则的管理端 CRUD）
- 其余集成点是权限/审批处 1–2 行带守卫的 `evaluate()` 调用

**关键文件**：`evaluator.py`、`models.py`、`repo.py`、`cache.py`、`fingerprint.py`

**核心不变量与设计约束**

- 功能开关：`~/.open-ace/config.json` 的 `policy.enabled`（60s TTL）。关闭时 `get_evaluator()` 返回 `NullPolicyEvaluator`，系统行为与从前完全一致（实时人工审批）——所有调用点都可以无条件调用 `evaluate()`。
- 失败即关闭（fail-closed）：`evaluate()` 永不抛异常。内部出错时对工具目标返回 `require_human`、对模型/供应商选择返回 `deny`，坏掉的规则存储既不能自动放行，也不能悄悄选中被拒的模型。
- 确定性的 specificity 排序：machine > project > user > team > tenant，其次显式 `priority`，再次 `created_at`；首个匹配生效。无匹配 → `require_human`（工具）/ `allow`（模型选择）。
- 规则是不可变的版本化快照：编辑插入新行（`version + 1`、`is_current = true`），每个决策钉住 `(rule_id, rule_version)`，之后的编辑永远无法追溯改写历史决策。
- 指纹只折叠表面形态，使用版本化的归一化 profile（`generic:v1`、`bash-command:v1`、`file-path:v1`）——绝不让路径或命令变得更宽松。
- 持久化：决策 INSERT 与原子 `consume_decision` UPDATE 在 `repo.py` 中同步执行（绝不走异步 run-timeline writer）；决策通过 `request_id` 软链接到 `agent_approvals`（无硬外键——输入事件是异步持久化的）。

**数据表**：`policy_rules` / `policy_decisions`——收录于 [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) 的"自治开发"分组；权威 DDL 在 `migrations/versions/20260626_005_add_policy_tables.py`。评估热路径由 60s TTL 规则缓存（`cache.py`）供给，管理端 CRUD 会使其失效。

**测试**：`tests/unit/test_policy_evaluator.py`、`test_policy_fingerprint.py`、`test_policy_repo.py`、`test_policy_integration.py`。

### sso

企业认证的单点登录：OAuth2、OIDC、SAML 2.0 统一在一个 provider 抽象之后。

**入口**

- 路由：`app/routes/sso.py`
- 组织同步作业：`app/services/feishu_org_sync.py`、`dingtalk_org_sync.py`；另有 `app/scheduler_worker.py`

**关键文件**：`manager.py`、`provider.py`、`oauth2.py`、`oidc.py`、`saml.py`、`secret_holder.py`、`exceptions.py`

**核心不变量与设计约束**

- 一个 `SSOProvider` 契约覆盖三种协议；`OIDCProvider` 在 OAuth2 流程之上扩展 ID token 校验；`SAMLProvider` 实现 SP metadata / AuthnRequest / ACS 与 XML 签名校验。
- provider 机密（`client_secret` 等）放在 `SecretHolder`——一个防止被意外打日志或序列化的内存持有器。
- `SSOManager` 统一管理 provider 生命周期与认证会话；组织同步服务经由它，而不直接触碰 provider。

**数据表**：[SSO](DATABASE_SCHEMA.md#sso)分组——`sso_providers`、`sso_identities`、`sso_sessions`。

**测试**：`tests/unit/test_sso_manager_security.py`、`test_sso_redirect_whitelist_3224.py`、`test_sso_auto_provision_2893.py`。

**另见**：[SSO_CONFIG.md](../guide/SSO_CONFIG.md) provider 配置指南。

### workspace

围绕 agent 会话的一切——平台的"用"侧：会话持久化、远程工作区、浏览器接入代理、用量记录，以及自主开发栈。它是最大的模块，建议按子域导航。

**子结构导览**

- **会话核心** —— `session_manager.py`（持久化/恢复）、`state_sync.py`（实时同步）、`prompt_library.py`、`collaboration.py`、`tool_connector.py`、`tenant_config_cache.py`。
- **远程工作区** —— `remote_agent_manager.py`（agent WebSocket 连接、心跳、命令分发）、`remote_session_manager.py`（会话生命周期、消息转发）、`session_access.py`、`agent_token.py`、`api_key_proxy.py` + `api_key_router.py`、`llm_proxy_handler.py`，以及用量管线 `usage_parser.py` → `usage_evidence.py` → `usage_dedup.py` → `usage_sink.py`（另有 `usage_diagnostics.py`）。
- **浏览器接入代理** —— VSCode（`vscode_proxy.py` / `vscode_store.py` / `vscode_ws_bridge.py`）与终端（`terminal_store.py` / `terminal_ws_bridge.py` / `terminal_relay_store.py`），`relay_distributed_store.py` 提供 Redis 支撑的 HA 状态。
- **`run_timeline/`** —— 远程会话的持久化溯源：每会话一条持久 `agent_runs` 记录、只追加的 `agent_run_events` 流、持久的 `agent_approvals`。
- **`model_gateway/`** —— 可选的 LiteLLM 兼容 LLM 代理流量路由；见 [MODEL_GATEWAY.md](MODEL_GATEWAY.md)。
- **`autonomous/`** —— 自主开发编排：`orchestrator.py`、`phases/`（开发、PR 审查、验收、合并）、`git_workspace.py`、`github_ops.py` 与证据子系统；`autonomous/sandbox/` 是执行隔离后端。见 [AUTONOMOUS_DEVELOPMENT.md](AUTONOMOUS_DEVELOPMENT.md) 与 [SANDBOX_BACKENDS.md](SANDBOX_BACKENDS.md)。

**入口**

- 路由：`workspace.py`、`remote.py`、`autonomous.py`、`model_gateway.py`、`run_timeline.py`、`api_keys.py`、`usage.py`、`tenant.py`、`workspace_isolation.py`
- 服务：`workspace_service.py`、`webui_manager.py`、`webui_sandbox.py`、`autonomous_scheduler.py`、`data_fetch_scheduler.py`、`insights_service.py`、`workspace_isolation_contract.py`
- 模块外的 WebSocket 管道：`app/remote_ws_handler.py`、`app/terminal_ws_middleware.py`

**核心不变量与设计约束**

- API key 永不离开服务器：`api_key_proxy.py` 加密存储，发给远程 agent 的是短时代理 token，只有服务器自身的 LLM 代理端点才会换取真实 key。
- 注册 token 一次性使用（机器注册）；agent token 是用于心跳/消息端点的长时 Bearer 凭证（`agent_token.py`）。
- 会话鉴权是共享代码：`session_access.py` 集中 owner / system-admin / machine-admin 检查（及远程用户加载），remote 与 run-timeline 两个 blueprint 不会各自漂移。
- 用量记录 provider 无关，并在进入统一 sink 前做请求级去重（Issue #2184）。
- run timeline 只追加且自包含，整块功能可移除或在外部 API 之后重实现，对既有代码创伤极小。
- model gateway 保持配额、用量记录、归因与直连 provider 行为不变；唯一集成缝在 `llm_proxy_handler.handle_llm_proxy_request`（Null/real planner 对，带 `is_noop` 短路）。
- 中继状态分层：终端元数据是进程内 TTL 缓存；跨 pod HA 由 Redis 支撑的 `relay_distributed_store.py` 提供（熔断器、300s TTL、Redis 不可用时优雅降级为内存模式）。
- sandbox 调用方依赖 `autonomous/sandbox/` 包的契约面，绝不依赖内部模块布局。

**数据表**：[消息与会话](DATABASE_SCHEMA.md#消息与会话)（`agent_sessions`、`session_messages`）、[工作区与项目](DATABASE_SCHEMA.md#工作区与项目)（`prompt_templates`）、[远程工作区](DATABASE_SCHEMA.md#远程工作区)（`remote_machines`、`machine_assignments`、`api_key_store`、`proxy_token_jtis`）、[协作](DATABASE_SCHEMA.md#协作)、[同步与保留快照](DATABASE_SCHEMA.md#同步与保留快照)。run-timeline 与网关表（`agent_runs`、`agent_run_events`、`agent_approvals`、`model_gateway_config`）收录于 [DATABASE_SCHEMA.md](DATABASE_SCHEMA.md) 的"自治开发" / "其余领域"分组。

**测试**：`tests/unit/test_api_key_proxy_*.py`、`tests/unit/test_api_key_router.py`、`tests/autonomous/`、`tests/integration/routes/`。

**另见**：[WORKSPACE_ISOLATION_CAPABILITIES.md](../contracts/WORKSPACE_ISOLATION_CAPABILITIES.md)、[REMOTE_WORKSPACE.md](../guide/REMOTE_WORKSPACE.md)
