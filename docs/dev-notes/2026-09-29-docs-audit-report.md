# 文档内容审计报告（2026-09-29）

> 治理过程文档。本文档治理（目录重组 + 全量双语 + 文档站同步）的 Phase 0 产出。
> 方法：机器对账（359 端点 / 13 环境变量 / 42 张迁移表 / pytest.ini 与 CI 对账）+
> 5 路并行人工/模型评审（38 篇存量文档 + 9 份散落文档逐篇对代码核实）。
> 所有脱节结论均带 file:line 证据，抽查通过项不重复列出。

## 一、总评

- **单篇质量两极分化**：与代码/守护测试强绑定的契约文档（TOKEN_ACCOUNTING、MODEL_GATEWAY、
  SCHEMA_MIGRATION_GUIDE、TEST_LAYERS、WORKSPACE_ISOLATION_CAPABILITIES、
  AUTONOMOUS_PHASE_CONTRACTS、SSH 两篇、TRANSCRIPT_CONTRACT、NGINX、REMOTE_AGENT）
  抽查引用几乎 100% 命中，质量高。
- **系统性风险不在"写错"而在"没写"**：2026-06 迁移基线之后新增的 40 张数据库表所代表的
  全部功能域（合规保留、自治审批、webhook、调度、租户计划）在文档体系中整体缺席。
- **方案稿冒充长期参考**是第二类通病：TENANT_ADMIN_PERMISSIONS、GH_CLI_VERSION_COMPATIBILITY、
  API_EXCEPTIONS 的灰度段都带有"更新日志/空记录表/待实现脚本/pytest 命令"等一次性交付物特征。

## 二、P0（照做即失败 / 核心参考严重失真）

| # | 文档 | 问题 | 证据 |
|---|------|------|------|
| P0-1 | docs/en+cn/API.md | 覆盖率仅 113/359≈31%；三组**幽灵端点**（文档有、代码无，共 16 个）：`/api/usage/request/*`（实际 `/api/request/*`）、`/api/sessions`、`/api/prompts`（实际挂 `/api/workspace` 前缀）——照文档调用必 404 | app/routes/usage.py:306-344、app/__init__.py:1020、app/routes/workspace.py:581,621,843 |
| P0-2 | docs/en+cn/DEPLOYMENT.md | `manage.py local/remote deploy` 等命令**不存在**（实际 action 为 init/deploy/... + `--remote` 开关）；缺 SERVER_IP、WORKSPACE_PORT_RANGE_START/END 三个在用环境变量；升级备份写已废弃的 `usage.db`；数据位置自相矛盾（./data vs 4 个命名卷）；卸载漏 agent-state 卷；镜像名两种写法 | scripts/manage.py:597-604、docker-compose.yml:80,109,116-133,249、.env.example:81-87 |
| P0-3 | docs/en+cn/DATABASE_SCHEMA.md | 103 张表仅覆盖 46 张；迁移新增 42 张中 40 张**全文档体系零覆盖**（retention_policies/legal_holds/agent_runs/agent_approvals/webhook_*/scheduler_*/tenant_plans/proxy_token_jtis/encryption_keys 等）。裁决：不手工补——转"领域地图（103 表一句话职责）+ 逐列参考生成物"混合方案（仓库已有 schema-sync 管线） | migrations/versions/ 42 张 op.create_table vs 文档 47 节 |
| P0-4 | docs/en+cn/DATABASE_BACKUP.md | S3 部署步骤照做**静默失败**：文档教 `kubectl apply -k .`，但 kustomization.yaml 的 resources **不含 secret-s3.yaml**，凭据不会被部署；且全文限定 K8s，Docker Compose（默认部署方式）备份完全没写 | k8s/extras/backup/kustomization.yaml resources 列表 |

## 三、P1（明显缺口 / 误导性内容）

1. **PERMISSION_MODEL.md**：只写 4 角色 3 装饰器；代码实际 6 角色宇宙（app/models/user.py:26 ADMIN_ROLES 三管理员角色）+ 9 种装饰器（`@admin_required`×92、`@platform_admin_required`×25 等）+ `OPENACE_PLATFORM_ADMIN_STRICT_MODE` 未提。
2. **CONCEPTS.md**：名不符实——正文 taxonomy（message_user/message_assistant/message_toolresult/message_error）**代码中零命中**；真实模型是 `role ∈ {user,assistant,system,tool}`（app/utils/roles.py）。裁决：改名 USAGE_METRICS_CONCEPTS 或并入 TOKEN_ACCOUNTING。
3. **ARCHITECTURE.md**：run_timeline 前缀写错（/api → 实际 /api/remote）；/health 已废弃仍按现行写；/livez /readyz /metrics /security-status 零文档；"41 Services/26 Repositories"计数对不上。
4. **KUBERNETES.md**：ConfigMap 键清单列了不存在的 FLASK_ENV、漏了最关键的 OPENACE_SECURITY_MODE；/health 表述前后矛盾；docs/README.md 索引把它误描述为"单实例"（实际 3 副本）。
5. **SAML_CONFIG.md**：60 行速查卡，无 IdP 配置向导/测试/排障；SLO 端点（sso.py:1888,1947）未提；**OIDC/OAuth2 SSO 无任何文档**（SSO_ALLOWED_REDIRECT_DOMAINS 生产必配只在 DEPLOYMENT 一句带过）。
6. **KEY_MANAGEMENT.md**：只管 13 个环境变量密钥体系中的 1 个（OPENACE_ENCRYPTION_KEY）；SECRET_KEY/UPLOAD_AUTH_KEY 职责与轮转影响（SECRET_KEY 轮转=全员下线）未写；OPENACE_ENCRYPTION_KEYS 数据密钥注册表无独立章节。
7. **REMOTE_WORKSPACE.md**：WebSocket 传输已 410 Gone 仍按 "Planned/501" 写（remote.py:2448-2459）；与 REMOTE_AGENT.md 双份维护安装参数表且不同步。
8. **api/API_PERMISSION_MATRIX.md（生成物失真）**：生成器只识别 `@tenant_bp.route`/`@bp.route`（generate_permission_matrix.py:60-95），92 处 `@admin_required`、7 处 `@platform_admin_required` **全部漏报**，矩阵 "Total admin_required endpoints: 0"；Last updated 硬编码在模板里；无 CI 再生成钩子。
9. **TENANT_ADMIN_PERMISSIONS.md**：#2179 实施方案稿（带更新日志/pytest 命令），裁决**降级 dev-notes**，长期语义并入 PERMISSION_MODEL。
10. **GH_CLI_VERSION_COMPATIBILITY.md**：500 行约八成是未实施方案（空记录表、规划中缓存、不存在的版本告警）；Dockerfile:124-131 fallback 路径装**不 pin 版本**的 gh，锁定策略实际有洞。裁决：重写收缩为"pin + 过期策略 + fallback 例外"。
11. **DEVELOPMENT.md**：新人第一入口**没有"如何启动后端"的命令**（L108 只说 must be running）；e2e 目录树缺 ui/work/performance 三个子目录。
12. **FRONTEND_GUIDE.md**：路由表含不存在路由 `/manage/remote/api-keys`（实际 settings/api-keys，App.tsx:491）；缺 10+ 现役路由（work/files、work/alerts、work/autonomous、settings/branding|encryption-keys|model-gateway 等）。
13. **配置文档三处分裂**：config/CONFIG_GUIDE.md（config.json，中文，docs 体系外不可发现）、DEPLOYMENT.md Configuration 章、FEISHU/DINGTALK 各自维护——三处互不覆盖。裁决：以 CONFIG_GUIDE 为底稿建 docs 双语 CONFIG_REFERENCE。
14. **upload-to-central 未进 docs**：DEPLOYMENT "Central Server + Remote Collectors" 场景仍教旧路径（manage.py remote deploy + scp），专用同步守护进程（scripts/upload-to-central/）无入口。
15. **remote-agent/ 无目录 README**（22 个源文件零说明）；**cli.py 9 个子命令仅 config init 有文档**（aggregate-quota/reset-tenant-period/repair-consistency 直接影响数据正确性）。
16. **cn/DEPLOYMENT.md:396-398 事实错误**：macOS AirPlay 端口冲突写成 19888（en 版已修为历史注记：实际冲突端口是 5000）。
17. **守护测试缺口**：test_phase_b_acceptance.py:200-208 `_CONTRACT_PHASES` 缺 acceptance_verification（#2335 加入的 phase 未纳入守护）。

## 四、缺失文档清单（该写没写）

**P0 级**：① API.md 重写（remote 55 端点 / workspace 32 / api-keys / 运维健康端点族 / schedulers/settings 等整章缺失）② DATABASE_SCHEMA 领域地图（见 P0-3）
**P1 级**：③ 升级/回滚 runbook（baseline_2026_06_23 最低基线目前只在游离的 install-central README 里）④ 环境变量权威总表 ⑤ CONFIG_REFERENCE（config.json）⑥ OIDC/OAuth2 SSO 配置 ⑦ Docker Compose 备份/恢复 ⑧ scheduler 服务（docker profiles: [scheduler]）何时/如何启用
**P2 级**：⑨ 集中故障排查索引/RUNBOOK ⑩ 监控告警（/metrics、告警规则、容量）⑪ TLS 证书获取/续期 ⑫ 数据保留/合规运维（retention 域 5 张表背后的功能）⑬ policy/governance/compliance/analytics 四个模块零工程参考 ⑭ cli.py CLI 参考 ⑮ remote-agent 目录 README

## 五、P2（保留 + 小修）

- INTRO：补 AUTONOMOUS_DEVELOPMENT 链接。
- AUTONOMOUS_DEVELOPMENT：§17 回归矩阵拆 dev-notes、§13/§12.2 压缩；§15 补 internal/events/ingest 例外与正确路径。
- NGINX：补 k8s Ingress 场景互链（本批写作质量最高的文档之一）。
- DATABASE_CONVENTIONS：MIG001/MIG002 迁移铁律（L156-232）并入 SCHEMA_MIGRATION_GUIDE，本篇聚焦字段命名。
- TEST_LAYERS：macOS Bash 44 行安装细节降为附录。
- SANDBOX_BACKENDS:322,536 与 WORKSPACE_ISOLATION_CAPABILITIES:118 互引小写文件名（大写化重构遗留），代码注释 provider.py:877 同病。
- TRANSCRIPT_CONTRACT：Phase B follow-ups（#2046/#2022）迁 issue/dev-notes。
- API_EXCEPTIONS：ENFORCE_PROMPT_OWERSHIP 灰度段（一次性 rollout 方案）移出；与 BANDIT_BASELINE 重复的"绕过+季度审查"流程二选一集中。
- FILTER_PATTERNS_V2：旧 API 示例标注"历史行为（现固定返回 deprecation 错误体）"；410 时间线标注"需届时代码变更"。
- 散落文档死链：install-central docker-method README:327-329（3 个）、package-method README:393、k8s/extras/backup/README:193-194。
- 散落文档裁决：scripts/README、tests/README、k8s 两份 README、install-central 4 份均**原地保留**（依附脚本/清单），但内容与 docs 双写处收敛为"docs 单一权威 + README 指针"，并修死链。

## 六、en/cn 漂移抽查

- REMOTE_AGENT、SAML_CONFIG：逐节对齐，无漂移。
- DEPLOYMENT：结构对齐，唯一实质差异即 P1-16 的 cn 事实错误。
- 结论：镜像同步机制总体有效，合并单文件双语时逐对 diff 的成本可控。

## 七、审计方法局限

- 端点覆盖按蓝图前缀折算后精确 token 匹配，约 ±5 个边界误差。
- 代码对账为抽查（每篇 2-5 处），未逐行全量核对；"未发现脱节"指抽查样本内。
- Agent D 修正：app/modules/ 实际 6 个模块（analytics/compliance/governance/policy/sso/workspace）。
