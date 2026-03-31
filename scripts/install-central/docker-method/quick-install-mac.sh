#!/bin/bash
#
# Open ACE - Mac 一键部署脚本
#
# 此脚本用于在 Mac 上快速部署 Open ACE
#
# 使用方法:
#   1. 将此脚本和 open-ace-images.tar.gz 放在同一目录
#   2. 运行: ./quick-install-mac.sh
#
# 支持的 Mac 类型:
#   - Apple Silicon (M1/M2/M3/M4) - ARM64
#   - Intel Mac - AMD64
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
DEPLOY_DIR="${DEPLOY_DIR:-$HOME/open-ace}"
WEB_PORT="${WEB_PORT:-5000}"
IMAGE_FILE="${IMAGE_FILE:-open-ace-arm64.tar.gz}"
IMAGE_NAME="${IMAGE_NAME:-open-ace:arm64}"

# ============================================================================
# Helper Functions
# ============================================================================

print_header() {
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  $1${NC}"
    echo -e "${GREEN}========================================${NC}"
    echo ""
}

print_success() {
    echo -e "${GREEN}✓ $1${NC}"
}

print_error() {
    echo -e "${RED}✗ $1${NC}"
}

print_warning() {
    echo -e "${YELLOW}⚠ $1${NC}"
}

print_info() {
    echo -e "${BLUE}ℹ $1${NC}"
}

detect_arch() {
    local arch=$(uname -m)
    case "$arch" in
        arm64)
            echo "ARM64 (Apple Silicon)"
            ;;
        x86_64)
            echo "AMD64 (Intel)"
            ;;
        *)
            echo "$arch"
            ;;
    esac
}

# ============================================================================
# Main Script
# ============================================================================

print_header "Open ACE - Mac 一键部署"

# 显示系统信息
print_info "系统架构: $(detect_arch)"
print_info "部署目录: $DEPLOY_DIR"
print_info "Web 端口: $WEB_PORT"
echo ""

# Step 1: 检查 Docker
print_header "步骤 1/5: 检查 Docker"

if ! command -v docker &>/dev/null; then
    print_warning "Docker 未安装"
    print_info "正在安装 Docker Desktop..."

    # 检查 Homebrew
    if ! command -v brew &>/dev/null; then
        print_error "Homebrew 未安装"
        print_info "请先安装 Homebrew: https://brew.sh"
        exit 1
    fi

    brew install --cask docker
    print_success "Docker Desktop 安装完成"
    print_warning "请启动 Docker Desktop 后重新运行此脚本"
    open /Applications/Docker.app
    exit 0
fi

print_success "Docker 已安装: $(docker --version)"

# 检查 Docker daemon 是否运行
if ! docker info &>/dev/null; then
    print_warning "Docker daemon 未运行"
    print_info "正在启动 Docker Desktop..."
    open /Applications/Docker.app

    print_info "等待 Docker 启动..."
    for i in {1..30}; do
        sleep 2
        if docker info &>/dev/null; then
            print_success "Docker 已启动"
            break
        fi
        if [ $i -eq 30 ]; then
            print_error "Docker 启动超时"
            print_info "请手动启动 Docker Desktop 后重新运行"
            exit 1
        fi
    done
fi

print_success "Docker daemon 运行中"

# Step 2: 检查镜像文件
print_header "步骤 2/5: 检查镜像文件"

if [ ! -f "$IMAGE_FILE" ]; then
    print_error "镜像文件不存在: $IMAGE_FILE"
    echo ""
    echo "请确保以下文件存在于当前目录:"
    echo "  - $IMAGE_FILE"
    echo ""
    echo "导出镜像命令 (在开发机器上执行):"
    echo "  ./scripts/install-central/docker-method/export-image.sh --build --app-platform linux/arm64 --compress"
    exit 1
fi

FILE_SIZE=$(ls -lh "$IMAGE_FILE" | awk '{print $5}')
print_success "镜像文件: $IMAGE_FILE ($FILE_SIZE)"

# Step 3: 加载镜像
print_header "步骤 3/5: 加载 Docker 镜像"

# 检查镜像是否已加载
if docker image inspect "$IMAGE_NAME" &>/dev/null; then
    print_warning "镜像 $IMAGE_NAME 已存在"
    read -p "是否重新加载? [y/N]: " reload
    if [[ "$reload" =~ ^[Yy]$ ]]; then
        print_info "加载镜像..."
        gunzip -c "$IMAGE_FILE" | docker load
    fi
else
    print_info "加载镜像 (可能需要几分钟)..."
    gunzip -c "$IMAGE_FILE" | docker load
fi

print_success "镜像加载完成"

# 显示已加载的镜像
echo ""
docker images | grep -E "open-ace|postgres" || true
echo ""

# Step 4: 创建部署目录和配置
print_header "步骤 4/5: 创建部署配置"

mkdir -p "$DEPLOY_DIR"/config
mkdir -p "$DEPLOY_DIR"/logs

# 生成随机密钥
SECRET_KEY=$(openssl rand -hex 32)
UPLOAD_AUTH_KEY=$(openssl rand -hex 16)

# 创建配置文件
cat > "$DEPLOY_DIR/config/config.json" << EOF
{
  "host_name": "$(hostname)",
  "database": {
    "type": "sqlite",
    "path": "/home/open-ace/.open-ace/ace.db"
  },
  "server": {
    "upload_auth_key": "$UPLOAD_AUTH_KEY",
    "server_url": "http://localhost:$WEB_PORT",
    "web_port": $WEB_PORT,
    "web_host": "0.0.0.0"
  },
  "workspace": {
    "enabled": false,
    "url": ""
  },
  "tools": {
    "openclaw": {
      "enabled": false,
      "token_env": "OPENCLAW_TOKEN",
      "gateway_url": "",
      "hostname": "$(hostname)"
    },
    "claude": {
      "enabled": true,
      "hostname": "$(hostname)"
    },
    "qwen": {
      "enabled": true,
      "hostname": "$(hostname)"
    }
  }
}
EOF

print_success "配置文件已创建: $DEPLOY_DIR/config/config.json"

# 创建 docker-compose.yml
cat > "$DEPLOY_DIR/docker-compose.yml" << 'EOF'
services:
  open-ace:
    image: ${IMAGE_NAME:-open-ace:arm64}
    container_name: open-ace
    restart: unless-stopped
    ports:
      - "${WEB_PORT:-5000}:5000"
    environment:
      - FLASK_ENV=production
      - PYTHONUNBUFFERED=1
      - SECRET_KEY=${SECRET_KEY}
      - UPLOAD_AUTH_KEY=${UPLOAD_AUTH_KEY}
    volumes:
      - ./config:/home/open-ace/.open-ace:ro
      - ./logs:/app/logs
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:5000/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 10s
EOF

# 创建 .env 文件
cat > "$DEPLOY_DIR/.env" << EOF
WEB_PORT=$WEB_PORT
SECRET_KEY=$SECRET_KEY
UPLOAD_AUTH_KEY=$UPLOAD_AUTH_KEY
EOF

print_success "Docker Compose 配置已创建"

# Step 5: 启动服务
print_header "步骤 5/5: 启动服务"

cd "$DEPLOY_DIR"

# 停止旧容器（如果存在）
if docker ps -a --format '{{.Names}}' | grep -q '^open-ace$'; then
    print_info "停止旧容器..."
    docker compose down 2>/dev/null || docker stop open-ace 2>/dev/null || true
fi

# 启动新容器
print_info "启动 Open ACE..."
docker compose up -d

# 等待服务启动
print_info "等待服务启动..."
for i in {1..30}; do
    sleep 1
    if curl -s "http://localhost:$WEB_PORT/health" > /dev/null 2>&1; then
        print_success "服务已启动"
        break
    fi
    if [ $i -eq 30 ]; then
        print_warning "服务启动时间较长，请检查日志"
        docker compose logs
    fi
done

# 完成
print_header "部署完成"

echo -e "${GREEN}Open ACE 已成功部署!${NC}"
echo ""
echo "访问地址: ${BLUE}http://localhost:$WEB_PORT${NC}"
echo ""
echo "默认登录凭据:"
echo "  用户名: admin"
echo "  密码: admin123"
echo ""
echo -e "${YELLOW}重要: 请登录后立即修改默认密码!${NC}"
echo ""
echo "管理命令:"
echo "  查看状态: cd $DEPLOY_DIR && docker compose ps"
echo "  查看日志: cd $DEPLOY_DIR && docker compose logs -f"
echo "  重启服务: cd $DEPLOY_DIR && docker compose restart"
echo "  停止服务: cd $DEPLOY_DIR && docker compose down"
echo ""
echo "配置文件: $DEPLOY_DIR/config/config.json"
echo "数据库: $DEPLOY_DIR/config/ace.db (SQLite)"
echo ""

# 自动打开浏览器
read -p "是否打开浏览器? [Y/n]: " open_browser
if [[ ! "$open_browser" =~ ^[Nn]$ ]]; then
    open "http://localhost:$WEB_PORT"
fi