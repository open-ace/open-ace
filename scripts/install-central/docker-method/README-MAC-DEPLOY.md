# Open ACE - Mac 部署指南

## 概述

本指南帮助你在 Mac 上部署 Open ACE。

## 支持的 Mac 类型

| Mac 类型 | 芯片 | 架构 | 镜像平台 |
|---------|------|------|---------|
| Apple Silicon | M1/M2/M3/M4 | ARM64 | `linux/arm64` |
| Intel Mac | Intel Core | AMD64 | `linux/amd64` |

---

## 一、制作镜像（在开发机器上）

### 1. Apple Silicon Mac (M1/M2/M3/M4)

```bash
cd /path/to/open-ace

./scripts/install-central/docker-method/export-image.sh \
  --build \
  --app-platform linux/arm64 \
  --compress
```

### 2. Intel Mac

```bash
cd /path/to/open-ace

./scripts/install-central/docker-method/export-image.sh \
  --build \
  --app-platform linux/amd64 \
  --compress
```

### 输出文件

构建完成后会生成 `open-ace-images.tar.gz`（约 500MB-1GB）。

---

## 二、部署到目标 Mac

### 方法一：一键部署（推荐）

#### 1. 准备文件

将以下文件拷贝到目标 Mac 的同一目录：

```bash
# 文件列表
open-ace-images.tar.gz          # Docker 镜像
quick-install-mac.sh            # 一键部署脚本
```

#### 2. 运行部署脚本

```bash
chmod +x quick-install-mac.sh
./quick-install-mac.sh
```

脚本会自动完成：
1. 检查并安装 Docker Desktop（如需要）
2. 加载 Docker 镜像
3. 创建配置文件
4. 启动服务

#### 3. 访问应用

```
http://localhost:5000
```

默认登录凭据：
- 用户名：`admin`
- 密码：`admin123`

---

### 方法二：手动部署

#### 1. 安装 Docker Desktop

```bash
# 使用 Homebrew 安装
brew install --cask docker

# 启动 Docker Desktop
open /Applications/Docker.app
```

#### 2. 加载镜像

```bash
# 解压并加载
gunzip -c open-ace-images.tar.gz | docker load

# 验证
docker images | grep open-ace
```

#### 3. 创建部署目录

```bash
mkdir -p ~/open-ace/{config,logs}
cd ~/open-ace
```

#### 4. 创建配置文件

**config/config.json:**

```json
{
  "host_name": "my-mac",
  "database": {
    "type": "sqlite",
    "path": "/home/open-ace/.open-ace/ace.db"
  },
  "server": {
    "upload_auth_key": "your-random-key",
    "server_url": "http://localhost:5000",
    "web_port": 5000,
    "web_host": "0.0.0.0"
  },
  "workspace": {
    "enabled": false
  },
  "tools": {
    "claude": { "enabled": true },
    "qwen": { "enabled": true },
    "openclaw": { "enabled": false }
  }
}
```

#### 5. 创建 docker-compose.yml

```yaml
services:
  open-ace:
    image: open-ace:latest
    container_name: open-ace
    restart: unless-stopped
    ports:
      - "5000:5000"
    environment:
      - FLASK_ENV=production
      - PYTHONUNBUFFERED=1
      - SECRET_KEY=your-secret-key
      - UPLOAD_AUTH_KEY=your-upload-key
    volumes:
      - ./config:/home/open-ace/.open-ace:ro
      - ./logs:/app/logs
```

#### 6. 启动服务

```bash
docker compose up -d
```

---

## 三、管理命令

```bash
# 查看状态
docker compose ps

# 查看日志
docker compose logs -f

# 重启服务
docker compose restart

# 停止服务
docker compose down

# 启动服务
docker compose up -d
```

---

## 四、常见问题

### Q: Docker Desktop 启动慢？

A: 首次启动可能需要 1-2 分钟，请耐心等待。可以在菜单栏看到 Docker 图标。

### Q: 端口 5000 被占用？

```bash
# 查找占用进程
lsof -i :5000

# 结束进程
kill -9 <PID>

# 或修改端口
# 在 docker-compose.yml 中修改 "5000:5000" 为 "5002:5000"
```

### Q: 如何更新镜像？

```bash
# 1. 加载新镜像
gunzip -c open-ace-images-new.tar.gz | docker load

# 2. 重启容器
docker compose down
docker compose up -d
```

### Q: 数据存储在哪里？

- 数据库：`~/open-ace/config/ace.db`（SQLite 数据库存储在配置目录中）
- 日志：`~/open-ace/logs/`
- 配置：`~/open-ace/config/config.json`

---

## 五、文件传输示例

### 使用 scp

```bash
# 从开发机器传输到目标 Mac
scp open-ace-images.tar.gz quick-install-mac.sh user@target-mac:~/open-ace/
```

### 使用 AirDrop

1. 在 Finder 中选择文件
2. 右键 → 共享 → AirDrop
3. 选择目标 Mac

### 使用 U 盘

直接拷贝文件到 U 盘，然后在目标 Mac 上复制。

---

## 六、安全建议

1. **修改默认密码**：登录后立即修改 admin 密码
2. **更换密钥**：修改 `SECRET_KEY` 和 `UPLOAD_AUTH_KEY`
3. **限制访问**：如需公网访问，请配置防火墙和 HTTPS
