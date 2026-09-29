# Upgrading — 升级

[English](#english) | [中文](#中文)

---

## English

This guide covers upgrading Open ACE: preparation and backup, the upgrade procedure, database migration failure handling, and rollback. Replacing the application image never touches your data — all state lives in named volumes (`postgres-data`, `config-data`, `workspace-data`, `agent-state`).

## Table of Contents

- [Before You Upgrade](#before-you-upgrade)
- [Minimum Upgrade Baseline](#minimum-upgrade-baseline)
- [Upgrade Steps](#upgrade-steps)
- [Migration Failure Handling](#migration-failure-handling)
- [Rollback](#rollback)
- [Secret Key Compatibility](#secret-key-compatibility)
- [Post-Upgrade Verification](#post-upgrade-verification)

## Before You Upgrade

1. **Back up the database.** The data lives in the `postgres-data` named volume, backed up with `pg_dump` (there is no `usage.db` file anymore):

   ```bash
   docker compose exec postgres pg_dump -U ace ace > openace_backup_$(date +%Y%m%d).sql
   ```

   Adjust `-U <DB_USER>` and the database name if you changed `DB_USER` / `DB_NAME` in `.env`. See [DATABASE_BACKUP.md](DATABASE_BACKUP.md) for scheduled backup strategies.

2. **Know your secrets.** `SECRET_KEY`, `OPENACE_ENCRYPTION_KEY`, and `DB_PASSWORD` in `.env` must survive the upgrade unchanged (see [Secret Key Compatibility](#secret-key-compatibility)).

3. **Pick the target version.** By default Compose tracks `openace/open-ace:latest`; for controlled upgrades, pin a version tag in `.env`:

   ```bash
   # Pin a specific version (in .env)
   echo "IMAGE_NAME=openace/open-ace:v1.2.0" >> .env
   ```

## Minimum Upgrade Baseline

**The minimum supported upgrade starting point is `baseline_2026_06_23`.**

- If the database is at `baseline_2026_06_23` or any later revision on the supported migration chain, `scripts/check_min_revision.py` lets the container start and `alembic upgrade head` applies the missing migrations automatically.
- If the database still sits on a revision from before the baseline (e.g., a historical hash revision), `check_min_revision.py` errors out and blocks container startup, refusing to run on an unsupported migration path.
- Fresh databases (no `alembic_version` table) are allowed through in development mode; production mode requires an explicit migration before startup.

Such a failure means the database has drifted off the supported migration chain: restore from a known-healthy backup that is already on the `baseline_2026_06_23` chain, then upgrade. Do not keep retrying against the stale revision.

## Upgrade Steps

### Docker Compose (recommended)

```bash
cd /path/to/open-ace

# 1. Back up (see Before You Upgrade)

# 2. Pull the new image
docker compose pull

# 3. Recreate the containers
docker compose up -d

# 4. Watch the startup and automatic migration
docker compose logs -f open-ace
```

On first start after the image change, `docker-entrypoint.sh` automatically:

1. Waits for PostgreSQL to be ready
2. Checks whether the application schema already exists
3. Runs `scripts/check_min_revision.py` to verify the current revision is on the supported migration chain (fresh databases pass automatically)
4. Runs `alembic upgrade head`
5. Creates the default admin account on a fresh database

To run the check and migration manually:

```bash
docker compose exec open-ace sh -c 'python3 scripts/check_min_revision.py && alembic upgrade head'
```

With the scheduler profile or the multi-user overlay, upgrade the whole stack the same way (for multi-user: `docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d`).

### Bare metal (package / source installs)

```bash
# Back up first, then:
git pull

# Run database migrations
alembic upgrade head

# Restart the service
python3 scripts/manage.py stop
python3 scripts/manage.py start
# or, for a systemd installation:
sudo systemctl restart open-ace
```

Package-method installs reuse `install.sh`, which detects the existing installation and prompts to upgrade; it also runs `check_min_revision.py` before migrating and refuses on pre-baseline databases.

### Offline servers

On a connected machine, export the new images and transfer them:

```bash
./scripts/install-central/docker-method/export-image.sh --build --compress
# Copy open-ace-images.tar.gz to the server, then:
gunzip -c open-ace-images.tar.gz | docker load
docker compose up -d
```

`scripts/install-central/docker-method/upgrade-remote.sh` automates the remote upgrade flow.

## Migration Failure Handling

If startup fails during the migration step:

```bash
docker compose logs open-ace
```

- `check_min_revision.py` failure — the database revision predates `baseline_2026_06_23`. Restore from a healthy backup on the supported chain; there is no in-place path from ancient revisions.
- `alembic upgrade head` failure — the entrypoint logs `ERROR: alembic upgrade head failed`. Keep the failed container stopped, inspect the log for the failing migration, fix the reported cause (disk space, connectivity to PostgreSQL, interrupted previous migration), and start again. Alembic applies migrations transactionally per migration file.
- After any manual database surgery, re-run the verification command above before letting the service serve traffic.

Do not run two versions of the application against a half-migrated database: runtime schema checks (`schema_guard.py`) use a stricter require-head policy than the install-time check and will refuse to start a service on a not-fully-migrated database.

## Rollback

Rolling back is image pinning plus database restore:

1. **Pin the previous image** in `.env` (e.g. `IMAGE_NAME=openace/open-ace:v1.2.0` for the version you ran before).
2. **Restore the database** from the pre-upgrade backup:

   ```bash
   docker compose down
   cat openace_backup_YYYYMMDD.sql | docker compose run --rm postgres psql -U ace -d ace
   docker compose up -d
   ```

   Restore into a clean database when practical (drop and recreate, or restore into the volume) rather than replaying over a newer schema.
3. **Revert `.env` changes** made for the new version (for example, newly required variables) and restart.

Notes:

- Downgrading migrations with `alembic downgrade` is not a supported rollback path — schema downgrades are not maintained. Always roll the data back from a backup.
- Named volumes are preserved by `docker compose down`; only `docker compose down -v` or explicit `docker volume rm` deletes data.
- If the new version rotated any secrets (you should not rotate during an upgrade), revert `.env` to the pre-upgrade values as part of rollback.

## Secret Key Compatibility

- Carry `SECRET_KEY`, `OPENACE_ENCRYPTION_KEY`, and `DB_PASSWORD` over unchanged when upgrading. A new `OPENACE_ENCRYPTION_KEY` that does not match the old one leaves every stored secret (SSO client secrets, SMTP passwords, API keys, integration credentials) permanently undecryptable.
- Historical note for deployments that predate the split between session signing and secret encryption: before upgrading an existing deployment that already stores encrypted secrets, set `OPENACE_ENCRYPTION_KEY` to the same value previously used for `SECRET_KEY`; after the upgraded service proves it can read existing secrets, rotate to a dedicated key during a planned maintenance window.
- Full rotation procedures, the eight encrypted stores covered by one key, and the `OPENACE_ENCRYPTION_KEYS` data-key registry are documented in [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md).

## Post-Upgrade Verification

- [ ] `docker compose ps` shows `open-ace` (and `postgres`) healthy; the healthcheck probes `/readyz`
- [ ] `docker compose logs open-ace` shows the migration completed and the service listening on 19888
- [ ] Login works; existing users, sessions, and configuration are intact
- [ ] Workspaces and integrations (SSO, SMTP, DingTalk/Feishu, webhooks) still authenticate — this also proves the encryption key carried over correctly
- [ ] Archive or remove the database backup after verification

---

## 中文

本指南涵盖 Open ACE 的升级：准备与备份、升级步骤、数据库迁移失败处置与回滚。更换应用镜像不会触碰数据——所有状态都在命名卷（`postgres-data`、`config-data`、`workspace-data`、`agent-state`）中。

## 目录

- [升级前准备](#升级前准备)
- [最低升级基线](#最低升级基线)
- [升级步骤](#升级步骤)
- [迁移失败处置](#迁移失败处置)
- [回滚](#回滚)
- [密钥兼容注意](#密钥兼容注意)
- [升级后验证](#升级后验证)

## 升级前准备

1. **备份数据库。** 数据存放在 `postgres-data` 命名卷中，用 `pg_dump` 备份（已不存在 `usage.db` 文件）：

   ```bash
   docker compose exec postgres pg_dump -U ace ace > openace_backup_$(date +%Y%m%d).sql
   ```

   若在 `.env` 中改过 `DB_USER` / `DB_NAME`，请相应调整 `-U <DB_USER>` 和库名。定时备份策略见 [DATABASE_BACKUP.md](DATABASE_BACKUP.md)。

2. **确认密钥。** `.env` 中的 `SECRET_KEY`、`OPENACE_ENCRYPTION_KEY`、`DB_PASSWORD` 必须在升级中原样保留（见[密钥兼容注意](#密钥兼容注意)）。

3. **确定目标版本。** Compose 默认跟踪 `openace/open-ace:latest`；可控升级建议在 `.env` 中固定版本标签：

   ```bash
   # 指定版本（写入 .env）
   echo "IMAGE_NAME=openace/open-ace:v1.2.0" >> .env
   ```

## 最低升级基线

**最低支持升级起点为 `baseline_2026_06_23`。**

- 若数据库已处于 `baseline_2026_06_23` 或受支持迁移链上的任意更新 revision，`scripts/check_min_revision.py` 会放行，容器启动时 `alembic upgrade head` 自动补齐缺失的迁移。
- 若数据库仍停留在基线之前的 revision（如历史 hash revision），`check_min_revision.py` 会直接报错并阻止容器启动，拒绝在不受支持的迁移路径上运行。
- 全新数据库（无 `alembic_version` 表）在开发模式下自动放行；生产模式要求先显式完成迁移。

出现这类失败说明数据库已偏离受支持的迁移链：请先从已位于 `baseline_2026_06_23` 链上的健康备份恢复，再升级。不要对着过期 revision 反复重试。

## 升级步骤

### Docker Compose（推荐）

```bash
cd /path/to/open-ace

# 1. 备份（见「升级前准备」）

# 2. 拉取新镜像
docker compose pull

# 3. 重建容器
docker compose up -d

# 4. 观察启动与自动迁移
docker compose logs -f open-ace
```

镜像更换后的首次启动，`docker-entrypoint.sh` 会自动完成：

1. 等待 PostgreSQL 就绪
2. 检查应用 schema 是否已存在
3. 运行 `scripts/check_min_revision.py` 校验当前 revision 在受支持的迁移链上（全新库自动放行）
4. 执行 `alembic upgrade head`
5. 对全新数据库创建默认管理员账号

手动执行校验与迁移：

```bash
docker compose exec open-ace sh -c 'python3 scripts/check_min_revision.py && alembic upgrade head'
```

带 scheduler profile 或多用户 overlay 时，用同样方式升级整个栈（多用户：`docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d`）。

### 裸机（package / 源码安装）

```bash
# 先备份，然后：
git pull

# 运行数据库迁移
alembic upgrade head

# 重启服务
python3 scripts/manage.py stop
python3 scripts/manage.py start
# systemd 安装则：
sudo systemctl restart open-ace
```

package 方式安装可直接复用 `install.sh`：脚本会检测既有安装并提示升级，迁移前同样运行 `check_min_revision.py`，对基线之前的数据库直接拒绝。

### 离线服务器

在有网络的机器上导出新镜像并传输：

```bash
./scripts/install-central/docker-method/export-image.sh --build --compress
# 将 open-ace-images.tar.gz 拷贝到服务器后：
gunzip -c open-ace-images.tar.gz | docker load
docker compose up -d
```

`scripts/install-central/docker-method/upgrade-remote.sh` 可自动完成远程升级流程。

## 迁移失败处置

启动在迁移阶段失败时：

```bash
docker compose logs open-ace
```

- `check_min_revision.py` 失败——数据库 revision 早于 `baseline_2026_06_23`。从受支持链上的健康备份恢复；不存在从远古 revision 就地升级的路径。
- `alembic upgrade head` 失败——入口脚本会记录 `ERROR: alembic upgrade head failed`。保持失败容器停止，从日志定位失败的迁移，修复报告的原因（磁盘空间、PostgreSQL 连接、上次迁移被中断）后重试。Alembic 按迁移文件粒度以事务方式执行。
- 任何手工数据库修复之后，先重跑上面的校验命令，再让服务对外提供服务。

不要让两个版本的应用同时连着一个迁移到一半的数据库：运行时 schema 检查（`schema_guard.py`）比安装期检查更严格（require-head 策略），会拒绝在未完全迁移的数据库上启动服务。

## 回滚

回滚 = 固定旧镜像 + 恢复数据库：

1. **在 `.env` 中固定回旧版镜像**（如升级前使用的 `IMAGE_NAME=openace/open-ace:v1.2.0`）。
2. **从升级前备份恢复数据库**：

   ```bash
   docker compose down
   cat openace_backup_YYYYMMDD.sql | docker compose run --rm postgres psql -U ace -d ace
   docker compose up -d
   ```

   条件允许时恢复到干净的数据库（先删后建，或恢复进卷），而不是在更新的 schema 上重放。
3. **回退为新版所做的 `.env` 变更**（例如新增的必需变量）并重启。

注意：

- `alembic downgrade` 不是受支持的回滚路径——schema 降级脚本并不维护。数据一律从备份回滚。
- `docker compose down` 会保留命名卷；只有 `docker compose down -v` 或显式 `docker volume rm` 才会删除数据。
- 若新版期间轮换过密钥（升级过程中不应轮换），回滚时需一并把 `.env` 恢复为升级前的值。

## 密钥兼容注意

- 升级时 `SECRET_KEY`、`OPENACE_ENCRYPTION_KEY`、`DB_PASSWORD` 必须原样保留。换了与旧值不匹配的 `OPENACE_ENCRYPTION_KEY` 会导致所有已存储的敏感数据（SSO client secret、SMTP 密码、API Key、集成凭据）永久无法解密。
- 针对早于「会话签名与敏感数据加密拆分」的历史部署：已有加密数据的部署升级前，请先把 `OPENACE_ENCRYPTION_KEY` 设置为旧版曾用于 `SECRET_KEY` 的同一个值；确认升级后的服务能读取既有密文后，再在计划维护窗口轮换为专用密钥。
- 完整轮换流程、单密钥覆盖的 8 类加密存储、`OPENACE_ENCRYPTION_KEYS` 数据密钥注册表见 [KEY_MANAGEMENT.md](KEY_MANAGEMENT.md)。

## 升级后验证

- [ ] `docker compose ps` 显示 `open-ace`（及 `postgres`）健康；健康检查探测 `/readyz`
- [ ] `docker compose logs open-ace` 显示迁移完成、服务监听 19888
- [ ] 登录正常；既有用户、会话与配置完好
- [ ] 工作区与各集成（SSO、SMTP、钉钉/飞书、Webhook）认证正常——这同时证明加密钥被正确保留
- [ ] 验证后归档或删除数据库备份
