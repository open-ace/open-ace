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
| `--help, -h` | 显示帮助信息 |

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
./scripts/install.sh
```

安装过程中会提示：
1. 选择安装模式（本地安装 / 远程部署）
2. 设置安装路径
3. 是否安装为 systemd 服务
4. 设置 Web 服务端口

### 使用配置文件安装

```bash
# 创建配置文件
cat > install.conf << 'EOF'
# 本地安装配置
DEPLOY_USER=$USER
DEPLOY_PATH=$HOME/open-ace
SERVICE_PORT=5000
SERVICE_HOST=0.0.0.0
EOF

# 使用配置文件安装
./scripts/install.sh --config install.conf
```

### 远程部署

```bash
# 远程部署配置
cat > deploy.conf << 'EOF'
DEPLOY_HOST=192.168.1.100
DEPLOY_USER=admin
DEPLOY_PATH=/home/admin/open-ace
SERVICE_PORT=5000
SERVICE_HOST=0.0.0.0
EOF

./scripts/install.sh --config deploy.conf
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
./scripts/install.sh
# 检测到现有安装时会提示：
# "Existing installation found at: /path/to/open-ace"
# "Upgrade existing installation? [Y/n]"
```

### 使用配置文件升级

```bash
./scripts/install.sh --config install.conf
```

### 升级时保留的数据

升级过程中以下数据会被保留：

- `~/.open-ace/config.json` - 配置文件
- `~/.open-ace/usage.db` - 使用记录数据库
- `~/.open-ace/feishu_users.json` - 飞书用户配置
- `logs/` - 日志目录
- `data/` - 数据目录

## 卸载

使用 `uninstall.sh` 进行卸载。

### 交互式卸载

```bash
./scripts/uninstall.sh
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
DEPLOY_PATH=$HOME/open-ace
REMOVE_CONFIG=no
REMOVE_DATA=no
EOF

./scripts/uninstall.sh --config uninstall.conf
```

### 远程卸载

```bash
# 远程卸载配置
cat > uninstall-remote.conf << 'EOF'
DEPLOY_HOST=192.168.1.100
DEPLOY_USER=admin
DEPLOY_PATH=/home/admin/open-ace
REMOVE_CONFIG=no
REMOVE_DATA=no
EOF

./scripts/uninstall.sh --config uninstall-remote.conf
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
DEPLOY_PATH=$HOME/open-ace

# Systemd 服务配置
SERVICE_PORT=5000
SERVICE_HOST=0.0.0.0
```

### 卸载配置文件格式

```bash
# 卸载模式
DEPLOY_HOST=              # 留空 = 本地卸载
# DEPLOY_HOST=192.168.1.100  # 设置主机 = 远程卸载

DEPLOY_USER=$USER
DEPLOY_PATH=$HOME/open-ace

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