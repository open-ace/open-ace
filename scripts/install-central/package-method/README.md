# Open ACE 安装指南

本目录包含 Open ACE 的打包、安装、升级和卸载脚本。

## 目录

- [打包](#打包)
- [安装](#安装)
- [升级](#升级)
- [卸载](#卸载)
- [配置文件](#配置文件)
- [Systemd 服务管理](#systemd-服务管理)

## 打包

使用 `package.sh` 创建发布包。

### 基本用法

```bash
# 使用 git commit 信息自动生成版本号
./package.sh

# 指定版本号
./package.sh --version 1.2.0

# 强制重新下载依赖
./package.sh --force-download

# 从 PostgreSQL 数据库生成 schema.sql（需要数据库连接）
./package.sh --generate-schema
```

### 输出

打包完成后会在 `dist/` 目录生成：

```
dist/open-ace-{commit_hash}-{date}.tar.gz
```

### 选项

| 选项 | 说明 |
|------|------|
| `--version, -v` | 指定版本号 |
| `--force-download, -f` | 强制重新下载 Python 依赖 |
| `--generate-schema, -g` | 从 PostgreSQL 生成 schema.sql（需要 DATABASE_URL） |
| `--help, -h` | 显示帮助信息 |

### Schema 生成

默认情况下，`package.sh` 使用已有的 `schema/schema-postgres.sql` 和 `schema/schema-sqlite.sql` 文件。

如需重新生成 schema（通常在开发环境）：

```bash
# 设置数据库连接
export DATABASE_URL='postgresql://user:pass@host:port/dbname'

# 运行打包并生成 schema
./package.sh --generate-schema
```

**注意：** 生产环境打包通常不需要 `--generate-schema`，schema 文件应该已经存在于代码库中。

### 特性

- 自动下载多平台 Python 依赖（Linux x86_64/aarch64, macOS arm64/x86_64）
- 支持离线安装
- 缓存依赖以加速后续打包

## 安装

使用 `install.sh` 进行安装。

### 交互式安装

```bash
# 解压发布包
tar -xzf open-ace-*.tar.gz
cd open-ace-*

# 运行安装脚本
./scripts/install-central/package-method/install.sh
```

安装过程中会提示：
1. 选择安装模式（本地安装 / 远程部署）
2. 设置安装路径
3. 是否安装为 systemd 服务
4. 设置 Web 服务端口

### 使用配置文件安装

```bash
# 创建配置文件（PostgreSQL）
cat > install.conf << 'EOF'
# 安装配置
DEPLOY_USER=openace
DEPLOY_PATH=$HOME

# PostgreSQL 数据库配置
DB_TYPE=postgresql
DB_HOST=localhost
DB_PORT=5432
DB_NAME=openace
DB_USER=openace
DB_PASSWORD=openace123

# 服务配置
INSTALL_SERVICE=yes
SERVICE_PORT=5000
SERVICE_HOST=0.0.0.0
EOF

# 使用配置文件安装
./scripts/install-central/package-method/install.sh --config install.conf
```

### SQLite 数据库

```bash
# SQLite 配置（用于开发/测试）
cat > install.conf << 'EOF'
DEPLOY_USER=$USER
DEPLOY_PATH=$HOME
DB_TYPE=sqlite
SERVICE_PORT=5000
EOF

./scripts/install-central/package-method/install.sh --config install.conf
```

### 数据库初始化

安装脚本会自动：

1. **PostgreSQL**: 创建数据库用户和数据库，执行 `schema/schema-postgres.sql` 创建表结构，然后使用 `alembic stamp head` 标记版本
2. **SQLite**: 执行 `schema/schema-sqlite.sql` 创建表结构

### psycopg2 安装

安装脚本会自动处理 psycopg2 依赖：

- **Rocky Linux/CentOS**: 使用系统包 `python3-psycopg2`（避免 pip 版本的段错误问题）
- **Ubuntu/Debian**: 使用系统包 `python3-psycopg2`
- **其他系统**: 从 pip 安装

### 远程部署

```bash
# 远程部署配置
cat > deploy.conf << 'EOF'
DEPLOY_HOST=192.168.1.100
DEPLOY_USER=admin
DEPLOY_PATH=$HOME
INSTALL_SERVICE=yes
SERVICE_PORT=5000
SERVICE_HOST=0.0.0.0
EOF

./scripts/install-central/package-method/install.sh --config deploy.conf
```

### Systemd 服务

设置 `INSTALL_SERVICE=yes` 会创建 systemd 服务，自动管理 Open ACE 进程：

- 服务名：`open-ace.service`
- 服务用户：配置中的 `DEPLOY_USER`
- 自动启动、重启、日志记录

**服务管理命令：**

```bash
# 查看状态
systemctl status open-ace

# 启动/停止/重启
sudo systemctl start open-ace
sudo systemctl stop open-ace
sudo systemctl restart open-ace

# 查看日志
journalctl -u open-ace -f
```

**注意：** 如果不设置 `INSTALL_SERVICE=yes`，需要手动启动服务：

```bash
cd /home/openace && python3 web.py
```

### 选项

| 选项 | 说明 |
|------|------|
| `--config FILE` | 使用配置文件 |
| `--help, -h` | 显示帮助信息 |

## 升级

升级与安装使用同一个脚本，脚本会自动检测现有安装并提示是否升级。

### 交互式升级

```bash
./scripts/install-central/package-method/install.sh
# 检测到现有安装时会提示：
# "Existing installation found at: /path/to/open-ace"
# "Upgrade existing installation? [Y/n]"
```

### 使用配置文件升级

```bash
./scripts/install-central/package-method/install.sh --config install.conf
```

### 升级时保留的数据

升级过程中以下数据会被保留：

- `~/.open-ace/config.json` - 配置文件
- `~/.open-ace/feishu_users.json` - 飞书用户配置
- `logs/` - 日志目录

## 卸载

使用 `uninstall.sh` 进行卸载。

### 交互式卸载

```bash
./scripts/install-central/package-method/uninstall.sh
```

卸载过程中会提示：
1. 选择卸载模式（本地 / 远程）
2. 设置安装路径
3. 是否删除配置目录（`~/.open-ace`）
4. 是否删除数据文件

### 使用配置文件卸载

```bash
# 本地卸载配置
cat > uninstall.conf << 'EOF'
DEPLOY_USER=$USER
DEPLOY_PATH=$HOME
REMOVE_CONFIG=no
REMOVE_DATA=no
EOF

./scripts/install-central/package-method/uninstall.sh --config uninstall.conf
```

### 远程卸载

```bash
# 远程卸载配置
cat > uninstall-remote.conf << 'EOF'
DEPLOY_HOST=192.168.1.100
DEPLOY_USER=admin
DEPLOY_PATH=$HOME
REMOVE_CONFIG=no
REMOVE_DATA=no
EOF

./scripts/install-central/package-method/uninstall.sh --config uninstall-remote.conf
```

### 选项

| 选项 | 说明 |
|------|------|
| `--config FILE` | 使用配置文件 |
| `--help, -h` | 显示帮助信息 |

## 配置文件

### 安装配置文件格式

```bash
# 安装模式（留空为本地安装，设置主机则为远程部署）
DEPLOY_HOST=              # 留空 = 本地安装
# DEPLOY_HOST=192.168.1.100  # 设置主机 = 远程部署

# 用户和路径
DEPLOY_USER=$USER
DEPLOY_PATH=$HOME

# 数据库配置
DB_TYPE=postgresql        # postgresql 或 sqlite
DB_HOST=localhost         # PostgreSQL 主机
DB_PORT=5432              # PostgreSQL 端口
DB_NAME=openace           # PostgreSQL 数据库名
DB_USER=openace           # PostgreSQL 用户名
DB_PASSWORD=openace123    # PostgreSQL 密码

# Systemd 服务配置
INSTALL_SERVICE=yes       # 是否安装 systemd 服务
SERVICE_PORT=5000         # Web 服务端口
SERVICE_HOST=0.0.0.0      # Web 服务主机

# 多用户 Workspace 模式（可选）
WORKSPACE_MULTI_USER_MODE=true    # 启用多用户模式
WORKSPACE_PORT_RANGE_START=3100   # 端口池起始端口
WORKSPACE_PORT_RANGE_END=3200     # 端口池结束端口
WORKSPACE_MAX_INSTANCES=20        # 最大实例数
WORKSPACE_IDLE_TIMEOUT=30         # 空闲超时（分钟）
```

### 多用户 Workspace 模式

多用户模式为每个用户启动独立的 `qwen-code-webui` 进程，确保用户数据隔离。

**前置要求：**

1. **Node.js 18+**：Vite 6.x 需要 Node.js 18 或更高版本
   ```bash
   # 使用 NodeSource 安装 Node.js 20.x
   curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
   yum install -y nodejs  # Rocky Linux/CentOS
   
   # 或 Debian/Ubuntu
   curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
   apt-get install -y nodejs
   ```

2. **安装 qwen-code-webui 和 qwen-code CLI**：
   ```bash
   npm install -g qwen-code-webui
   npm install -g @qwen-code/qwen-code
   ```
   
   **注意：**
   - qwen-code CLI 包名是 `@qwen-code/qwen-code`（不是 `qwen-code`）
   - CLI 命令名是 `qwen`（不是 `qwen-code`）
   - 安装过程可能需要几分钟，请耐心等待

3. **确保每个用户有对应的系统账号和 `~/.qwen/` 目录**

**自动配置：**

安装脚本在启用多用户模式时会自动：
- 安装 Node.js 20.x（如果未安装）
- 安装 `qwen-code-webui` 和 `@qwen-code/qwen-code` CLI
- 检测 `qwen-code-webui` 安装位置并写入配置
- 创建 `/etc/sudoers.d/open-ace-webui` 配置文件
- 设置 systemd 服务 `NoNewPrivileges=false`（允许 sudo）
- 设置 `workspace.url` 为服务器 IP 地址
- 验证 sudoers 语法

```bash
# 交互式安装时选择启用多用户模式
./scripts/install-central/package-method/install.sh

# 或使用配置文件
cat > install.conf << 'EOF'
DEPLOY_USER=openace
DEPLOY_PATH=$HOME
WORKSPACE_MULTI_USER_MODE=true
WORKSPACE_PORT_RANGE_START=3100
WORKSPACE_PORT_RANGE_END=3200
EOF

./scripts/install-central/package-method/install.sh --config install.conf
```

**手动配置 sudoers：**

如果自动配置失败，可手动配置：

```bash
sudo visudo -f /etc/sudoers.d/open-ace-webui
```

添加内容：
```
openace ALL=(ALL) NOPASSWD: /usr/bin/qwen-code-webui *
```

**CORS 配置：**

Open-ACE 已配置 CORS 允许来自 workspace 端口范围 (3100-3200) 的请求，iframe 内的 webui 可以正常调用 Open-ACE API。

**详细配置说明请参考 [部署文档](../../docs/DEPLOYMENT.md#multi-user-workspace-deployment)**

### 卸载配置文件格式

```bash
# 卸载模式
DEPLOY_HOST=              # 留空 = 本地卸载
# DEPLOY_HOST=192.168.1.100  # 设置主机 = 远程卸载

DEPLOY_USER=$USER
DEPLOY_PATH=$HOME

# 数据删除选项
REMOVE_CONFIG=no          # 是否删除 ~/.open-ace 配置目录
REMOVE_DATA=no            # 是否删除数据文件
```

## Systemd 服务管理

如果安装时选择了 systemd 服务，可以使用以下命令管理：

### 本地服务管理

```bash
# 查看服务状态
systemctl status open-ace

# 启动服务
sudo systemctl start open-ace

# 停止服务
sudo systemctl stop open-ace

# 重启服务
sudo systemctl restart open-ace

# 查看日志
journalctl -u open-ace -f
```

### 远程服务管理

```bash
# 查看服务状态
ssh user@host 'sudo systemctl status open-ace'

# 启动服务
ssh user@host 'sudo systemctl start open-ace'

# 停止服务
ssh user@host 'sudo systemctl stop open-ace'

# 重启服务
ssh user@host 'sudo systemctl restart open-ace'

# 查看日志
ssh user@host 'sudo journalctl -u open-ace -f'
```

## 快速参考

| 操作 | 命令 |
|------|------|
| 打包 | `./package.sh` |
| 安装 | `./install.sh` |
| 升级 | `./install.sh`（自动检测） |
| 卸载 | `./uninstall.sh` |
| 查看帮助 | `./script.sh --help` |