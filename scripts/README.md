# scripts/ 目录说明

本目录混合承载四类性质完全不同的资产。**新增或移动文件前先读本文**：
本目录是 `pyproject.toml` 打包范围（`include = ["app*", "scripts*"]`）的一部分，
`shared/` 会被 `server.py` 直接 import，`fetch_*.py` 与 `openace-*` 的路径被
`app/routes/fetch.py`、`Dockerfile`、`generate-sudoers.sh`、installers、
GitHub workflows 和 pre-commit 硬编码引用。**不要随意移动或改名现有文件**；
确需移动时必须同步全仓 grep 路径并更新所有引用方。

## 分类速查

### 1. 运行时依赖（生产代码直接调用，动不得）

| 文件 | 调用方 |
| --- | --- |
| `fetch_{claude,codex,openclaw,qwen,zcode}.py` | `app/routes/fetch.py`（subprocess，路径硬编码） |
| `shared/` | `server.py`、各 fetch/检查脚本（Python 包 import） |
| `openace-run-as.sh`、`openace-fetch-wrapper`、`openace-validate-launch`、`openace-ssh-sync` | scheduler / workspace 模块、`docker-entrypoint.sh` |
| `setup-cgroup-v2.sh` | sandbox policy |
| `push.sh` | 自主开发 orchestrator |

### 2. CI / 质量门禁（workflows + pre-commit 引用）

- `ci.py` — 本地与 GitHub Actions 同一实现的 CI 总入口
- `run_extended_tests.py`、`scan_test_false_positives.py`、`multiuser_smoke.py`
- `lint/`、`e2e/`、`ci/`、`hooks/` 子目录（分别被 pre-commit、extended-tests、weekly-quality 等引用）
- schema 门禁：`validate_schema.py`、`check_schema_sync.py`、`check_migration_heads.py`、`generate_schema.py`、`rebuild_schema_snapshots.py`

### 3. 部署 / 安装 / 运维

- `install-central/` — Docker 与 Package 两种安装方式
- `upload-to-central/` — 远端机器增量同步守护进程（见其 README）
- `docker/`、`systemd/` — 构建与部署单元
- `start-multi-user.sh`、`bootstrap-compose-env.sh` + `bootstrap_compose_env.py` — Compose 部署
- `open-ace.service`、`openace-scheduler.service` — systemd 单元
- sudoers 家族：`generate-sudoers.sh`、`upgrade-sudoers-security.sh`、`openace-{cat,chown,mkdir,rm,restore-sudoers,useradd,write-as,webui-launch}.sh`、`openace-{gh,git}.py`
- 发布：`release.sh`、`gen_requirements_lock.sh`、`generate_changelog.py`
- 日常运维：`manage.py`、`init_db.py`、`check_min_revision.py`、`verify_schema_integrity.py`、`audit_production_schema.py`、`frontend_asset_retention.py`、`generate_permission_matrix.py`、`check_daily_usage_{conflicts,quality}.py` + `resolve_daily_usage_conflicts.py`、`manual_e2e_quota_enforcement.py`、`export/import_encrypted_data.py`、`migrate_encryption_keys_to_db.py`、`migrate_security_mode.sh`（health payload 元数据与 `.env.example` 文档引用，非直接调用）

### 4. 应急工具（低频但关键，勿当死代码清理）

- `rotate_sso_encryption.py` — SSO Provider `client_secret` 批量重加密（见 `docs/{cn,en}/KEY_MANAGEMENT.md` 的密钥泄露响应）

## 新增脚本的规矩

1. **一次性数据迁移（schema 变更、存量数据回填、角色/会话迁移等）一律写
   Alembic migration 放 `migrations/versions/`，不要在 `scripts/` 顶层堆
   一次性脚本。** 历史上堆积的 `migrate_*` / `backfill_*` / `cleanup_*` /
   `annotate_*` 顶层脚本已于 2026-09 清理（git 历史可找回）。
2. 长期运维入口优先挂到 `manage.py` 子命令，而不是新增顶层文件。
3. 确需新增顶层脚本时：自带 docstring 说明用途与调用方；若是 CI 门禁，
   同步登记 `ci/suites.json`；若是安全/应急工具，在 `docs/` 对应文档留引用。

## 升级 qwen 栈（qwen-code-webui + @qwen-code/qwen-code）runbook

两个包按**经验证的一对**固定版本分发（不追 `@latest`：安装脚本里的
Node>=22 门与 adapter 启动参数只对 pin 过的组合验证过，而 npm 对
engines 冲突只给 EBADENGINE 警告仍 exit 0）。版本 pin 在**五处**，
`tests/unit/test_ci_docker_job_contract.py::test_qwen_stack_pins_are_consistent_across_all_sites`
锁定它们必须一致——升级时改漏任何一处 CI 会以"各站点版本清单"报错。

升级步骤：

1. **改五处 pin**（同一 commit）：
   - `Dockerfile`（`npm install -g qwen-code-webui@X @qwen-code/qwen-code@Y`）
   - `scripts/docker/webui-sandbox.Dockerfile`（同一对）
   - `scripts/install-central/package-method/install.sh`（`QWEBUI_VERSION` / `QWEN_CLI_VERSION`）
   - `remote-agent/install.sh`（`QWEN_CLI_VERSION`）
   - `remote-agent/install.ps1`（`$QwenCliVersion`）
2. **兼容性验证**（对照新版本源码/产物逐项核实，参考 PR #3386 的先例）：
   - 会话存储布局 `~/.qwen/projects/<id>/chats/<sessionId>.jsonl` 是否不变
     （`fetch_qwen.py` 与 webui histories 依赖）
   - remote-agent 使用的 6 个 flag：`--auth-type openai`、
     `--input-format/--output-format stream-json`、`--channel=SDK`、
     `--resume`、`--approval-mode`（含取值枚举——0.15→0.20 期间曾新增
     `auto`，`suggest` 从来不是合法值）
   - `OPENAI_API_KEY`/`OPENAI_BASE_URL` 代理约定
   - `engines.node`：若提高，同步所有 Node 版本门（镜像 NodeSource、
     安装脚本 `ensure_node_22`/版本门、CI 契约）
   - webui dist 行为：此前 5 个 bundle patch 对应的上游修复
     （histories/navparams/permission/vscode-folder/local-permission）
     是否仍然存在
   - 沙箱基础镜像账户布局（node 官方镜像 uid/gid 1000）
3. **同步测试期望**：`tests/unit/test_remote_agent_installer.py` 的固定
   版本断言、`test_qwen_adapter_approval_mode.py` 的合法枚举。
4. **CI 兜底**：`docker-sandbox` job 每个 PR 构建沙箱镜像（PR Gate
   required）；`docker` job 在 main push 构建完整生产镜像。
