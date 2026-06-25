#!/bin/bash
#
# Open ACE - Quick Install Script
#
# This script deploys Open ACE with PostgreSQL using Docker Compose.
#
# Usage:
#   ./install.sh                    # Interactive installation
#   ./install.sh --non-interactive  # Non-interactive mode (use defaults)
#   ./install.sh --help             # Show help
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Default values
RUN_USER="${RUN_USER:-open-ace}"
# RUN_USER_UID: Optional UID for the run user. If not set, system will auto-assign.
# Set this for Docker bind mount or NFS share permission consistency (Issue #1116).
RUN_USER_UID="${RUN_USER_UID:-}"
DEPLOY_DIR="${DEPLOY_DIR:-/home/$RUN_USER/open-ace}"
IMAGE_NAME="${IMAGE_NAME:-open-ace:latest}"
WEB_PORT="${WEB_PORT:-5000}"
INTERNAL_WEB_PORT="${INTERNAL_WEB_PORT:-5000}"
DB_USER="${DB_USER:-$RUN_USER}"
DB_PASSWORD="${DB_PASSWORD:-$(openssl rand -hex 16)}"
DB_NAME="${DB_NAME:-ace}"
DB_FILE="${DB_FILE:-ace.db}"
SECRET_KEY="${SECRET_KEY:-$(openssl rand -hex 32)}"
UPLOAD_AUTH_KEY="${UPLOAD_AUTH_KEY:-$(openssl rand -hex 16)}"
NON_INTERACTIVE=false

# Docker installation mirror source (can be overridden by environment variable)
# Empty = interactive selection, "official" = Docker official source, "aliyun" = Aliyun mirror
DOCKER_INSTALL_MIRROR="${DOCKER_INSTALL_MIRROR:-}"

# Config defaults (can be overridden by environment variables)
HOST_NAME="${HOST_NAME:-}"
WORKSPACE_ENABLED="${WORKSPACE_ENABLED:-true}"
WORKSPACE_URL="${WORKSPACE_URL:-http://localhost:3000}"
WORKSPACE_PORT="${WORKSPACE_PORT:-}"
# Multi-user workspace mode defaults
WORKSPACE_MULTI_USER_MODE="${WORKSPACE_MULTI_USER_MODE:-true}"
WORKSPACE_PORT_RANGE_START="${WORKSPACE_PORT_RANGE_START:-3100}"
WORKSPACE_PORT_RANGE_END="${WORKSPACE_PORT_RANGE_END:-3200}"
WORKSPACE_MAX_INSTANCES="${WORKSPACE_MAX_INSTANCES:-30}"
WORKSPACE_IDLE_TIMEOUT="${WORKSPACE_IDLE_TIMEOUT:-30}"
WORKSPACE_TOKEN_SECRET="${WORKSPACE_TOKEN_SECRET:-}"
OPENCLAW_ENABLED="${OPENCLAW_ENABLED:-true}"
OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-http://localhost:18789}"
OPENCLAW_PORT="${OPENCLAW_PORT:-}"
CLAUDE_ENABLED="${CLAUDE_ENABLED:-true}"
QWEN_ENABLED="${QWEN_ENABLED:-true}"

# SSH configuration for remote host access (Issue #1122)
SSH_ENABLED="${SSH_ENABLED:-no}"
# SSH_MOUNT_SOURCE: Use current user's .ssh directory (not RUN_USER which is container user)
SSH_MOUNT_SOURCE="${SSH_MOUNT_SOURCE:-$HOME/.ssh}"

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

prompt_input() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [ "$NON_INTERACTIVE" = true ]; then
        eval "$var_name='$default'"
        return
    fi

    # Enable Backspace key support for terminal input
    # Save current terminal settings and restore after read
    local stty_settings=""
    if [ -t 0 ]; then
        stty_settings=$(stty -g 2>/dev/null || true)
        stty erase '^H' 2>/dev/null || true
    fi

    if [ -n "$default" ]; then
        echo -ne "${BLUE}$prompt [${default}]: ${NC}"
    else
        echo -ne "${BLUE}$prompt: ${NC}"
    fi

    read -r value

    # Restore terminal settings
    if [ -n "$stty_settings" ] && [ -t 0 ]; then
        stty "$stty_settings" 2>/dev/null || true
    fi

    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi

    eval "$var_name='$value'"
}

prompt_yesno() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

    if [ "$NON_INTERACTIVE" = true ]; then
        eval "$var_name='$default'"
        return
    fi

    local options="[Y/n]"
    [ "$default" = "n" ] && options="[y/N]"

    echo -ne "${BLUE}$prompt ${options}: ${NC}"
    read -r value

    value=$(echo "$value" | tr '[:upper:]' '[:lower:]')

    if [ -z "$value" ]; then
        value="$default"
    fi

    if [ "$value" = "y" ] || [ "$value" = "yes" ]; then
        eval "$var_name='yes'"
    else
        eval "$var_name='no'"
    fi
}

# ============================================================================
# Docker Hub Access Detection Functions
# ============================================================================

# List of domestic Docker image mirrors (China)
DOMESTIC_DOCKER_MIRRORS=(
    "docker.1ms.run"
    "docker.xuanyuan.me"
    "hub.rat.dev"
    "docker.hlyun.org"
    "dockerpull.org"
    "docker.m.daocloud.io"
)

# Check if Docker Hub or configured mirrors are accessible
# Returns: 0 if accessible, 1 if not accessible
check_docker_image_access() {
    local test_image="alpine:latest"
    local timeout=15

    print_info "检测 Docker 镜像拉取能力..."

    # First check if test image already exists locally
    if docker image inspect "$test_image" &>/dev/null; then
        print_success "测试镜像已存在，跳过检测"
        return 0
    fi

    # Try pulling with timeout
    print_info "尝试拉取测试镜像 (超时 ${timeout}s)..."
    if timeout $timeout docker pull "$test_image" 2>&1 | grep -qE "Pulling from|Downloaded|Pull complete"; then
        print_success "Docker Hub 可访问"
        # Clean up test image
        docker rmi "$test_image" &>/dev/null || true
        return 0
    fi

    # Check if any registry mirrors are configured
    local mirrors=$(docker info 2>/dev/null | grep -A10 "Registry Mirrors" | grep "https://" | sed 's/.*https:\/\/\(.*\).*/\1/' | head -5)
    if [ -n "$mirrors" ]; then
        print_info "检测到已配置的镜像加速器:"
        echo "$mirrors" | while read mirror; do
            print_info "  - $mirror"
        done
        print_warning "镜像加速器可能不工作，尝试检测可用镜像源..."

        # Try each mirror
        for mirror in $mirrors; do
            print_info "尝试镜像源: $mirror"
            if timeout $timeout docker pull "$mirror/library/$test_image" 2>&1 | grep -qE "Pulling from|Downloaded|Pull complete"; then
                print_success "镜像源可用: $mirror"
                # Clean up
                docker rmi "$mirror/library/$test_image" &>/dev/null || true
                return 0
            fi
        done
    fi

    print_warning "Docker Hub 和已配置的镜像加速器均不可访问"
    return 1
}

# Pull base image with retry from multiple mirrors
# Args: image_name (e.g., "python:3.11-slim")
pull_base_image_with_retry() {
    local image_name="$1"
    local timeout=300  # Increased timeout for large images
    local max_retries=3
    local retry_count=0
    local pulled=false

    print_info "拉取基础镜像: $image_name"

    # Try direct pull with retries (uses configured registry mirrors)
    while [ $retry_count -lt $max_retries ] && [ "$pulled" = false ]; do
        if [ $retry_count -gt 0 ]; then
            print_warning "重试第 $retry_count 次..."
            sleep 5
        fi

        print_info "尝试拉取 (超时 ${timeout}s)..."
        if timeout $timeout docker pull "$image_name" 2>&1; then
            print_success "镜像拉取成功: $image_name"
            pulled=true
        else
            retry_count=$((retry_count + 1))
        fi
    done

    if [ "$pulled" = true ]; then
        return 0
    fi

    print_error "镜像拉取失败: $image_name (重试 $max_retries 次后仍失败)"
    return 1
}

# Configure Docker daemon registry mirrors
# Args: mirror_url (e.g., "https://docker.1ms.run")
configure_docker_registry_mirror() {
    local mirror_url="$1"
    local daemon_json="/etc/docker/daemon.json"

    print_info "配置 Docker 镜像加速器: $mirror_url"

    # Create directory if not exists
    sudo mkdir -p /etc/docker

    # Check existing config
    if [ -f "$daemon_json" ]; then
        local existing_config=$(cat "$daemon_json" 2>/dev/null || echo "{}")
        # Check if mirror is already configured
        if echo "$existing_config" | grep -q "$mirror_url"; then
            print_success "镜像加速器已配置: $mirror_url"
            return 0
        fi
        # Merge with existing config
        print_info "更新现有 Docker 配置..."
        if printf '%s\n' "$existing_config" | python3 -c "import json,sys; c=json.load(sys.stdin); c['registry-mirrors']=json.loads('["$mirror_url"]'); json.dump(c,sys.stdout,indent=2)" > /tmp/daemon.json.new 2>/dev/null; then
            sudo mv /tmp/daemon.json.new "$daemon_json"
        else
            # Fallback: create new config
            print_warning "无法合并配置，创建新配置文件..."
            printf '%s\n' "{\"registry-mirrors\": [\"$mirror_url\"]}" | sudo tee "$daemon_json" > /dev/null
        fi
    else
        # Create new config
        printf '%s\n' "{\"registry-mirrors\": [\"$mirror_url\"]}" | sudo tee "$daemon_json" > /dev/null
    fi

    # Restart Docker daemon to apply config
    print_info "重启 Docker 服务以应用配置..."
    sudo systemctl restart docker

    # Wait for Docker to be ready
    sleep 5
    if docker info &>/dev/null; then
        print_success "Docker 镜像加速器配置完成"
        print_info "配置内容:"
        sudo cat "$daemon_json" 2>/dev/null | python3 -c "import json,sys; print(json.dumps(json.load(sys.stdin), indent=2))" || sudo cat "$daemon_json"
        return 0
    else
        print_error "Docker 重启失败，请手动检查"
        return 1
    fi
}

# Interactive selection for Docker image source
select_docker_image_source() {
    echo ""
    echo "Docker Hub 无法访问或镜像加速器不工作"
    echo "请选择:"
    echo "  1) 配置国内镜像加速器并重试（推荐）"
    echo "  2) 手动输入镜像源地址配置"
    echo "  3) 手动导入基础镜像文件"
    echo "  4) 跳过镜像构建（稍后手动处理）"
    echo ""

    prompt_input "请选择" "1" source_choice

    case "$source_choice" in
        1)
            print_info "将配置国内镜像加速器..."
            # Select a mirror to configure
            echo ""
            echo "请选择镜像加速器:"
            echo "  1) docker.1ms.run (推荐)"
            echo "  2) hub.rat.dev"
            echo "  3) docker.m.daocloud.io"
            echo "  4) docker.hlyun.org"
            echo ""
            prompt_input "请选择" "1" mirror_choice

            local selected_mirror=""
            case "$mirror_choice" in
                1) selected_mirror="https://docker.1ms.run" ;;
                2) selected_mirror="https://hub.rat.dev" ;;
                3) selected_mirror="https://docker.m.daocloud.io" ;;
                4) selected_mirror="https://docker.hlyun.org" ;;
                *) selected_mirror="https://docker.1ms.run" ;;
            esac

            print_info "选择镜像加速器: $selected_mirror"
            if configure_docker_registry_mirror "$selected_mirror"; then
                print_success "镜像加速器配置成功，后续拉取将使用加速器"
                return 0
            else
                print_error "镜像加速器配置失败"
                return 1
            fi
            ;;
        2)
            prompt_input "镜像源地址（如 docker.1ms.run，不带 https://）" "" custom_mirror
            if [ -n "$custom_mirror" ]; then
                local mirror_url="https://$custom_mirror"
                print_info "将配置镜像加速器: $mirror_url"
                if configure_docker_registry_mirror "$mirror_url"; then
                    print_success "镜像加速器配置成功"
                    return 0
                else
                    return 1
                fi
            else
                print_error "未输入镜像源地址"
                return 1
            fi
            ;;
        3)
            print_info "请手动导入基础镜像后重新运行"
            print_info "导出命令（在有镜像的机器上）: docker save python:3.11-slim postgres:15-alpine | gzip > base-images.tar.gz"
            print_info "导入命令: docker load < base-images.tar.gz"
            return 2
            ;;
        4)
            print_info "跳过镜像构建"
            return 1
            ;;
        *)
            # Default: configure recommended mirror
            print_info "将配置推荐镜像加速器..."
            if configure_docker_registry_mirror "https://docker.1ms.run"; then
                return 0
            else
                return 1
            fi
            ;;
    esac
}

# ============================================================================
# SSH Configuration Functions (Issue #1122)
# ============================================================================

# Check if SSH keys exist for RUN_USER
check_ssh_keys() {
    local ssh_dir="/home/$RUN_USER/.ssh"

    if [ ! -d "$ssh_dir" ]; then
        print_warning "未找到 SSH 密钥目录: $ssh_dir"
        return 1
    fi

    local key_files=("id_rsa" "id_ed25519" "id_ecdsa")
    local found_keys=""

    for key in "${key_files[@]}"; do
        if [ -f "$ssh_dir/$key" ]; then
            found_keys="$found_keys $key"
        fi
    done

    if [ -n "$found_keys" ]; then
        print_success "检测到 SSH 密钥: $found_keys"
        return 0
    else
        print_warning "未检测到 SSH 私钥文件 (id_rsa, id_ed25519, etc.)"
        return 1
    fi
}

# Check known_hosts for target hosts
check_known_hosts() {
    local ssh_dir="/home/$RUN_USER/.ssh"
    local known_hosts_file="$ssh_dir/known_hosts"

    if [ ! -f "$known_hosts_file" ]; then
        print_warning "未找到 known_hosts 文件"
        return 1
    fi

    print_success "检测到 known_hosts 文件"
    return 0
}

# Prompt for SSH configuration
prompt_ssh_config() {
    print_header "SSH 远程访问配置"

    # Fix: NON_INTERACTIVE mode must set default value
    if [ "$NON_INTERACTIVE" = true ]; then
        SSH_ENABLED="${SSH_ENABLED:-no}"
        return
    fi

    # Check SSH keys
    if ! check_ssh_keys; then
        print_info "如需 SSH 远程访问，请先为用户 '$RUN_USER' 配置 SSH 密钥"
        SSH_ENABLED="no"
        return
    fi

    echo ""
    echo "SSH 远程访问允许容器内的 AI 连接到外部主机（如实验室节点）。"
    echo ""
    echo "⚠️  安全警告："
    echo "    - 挂载 SSH 密钥会将私钥暴露给容器内所有进程"
    echo "    - 建议使用专用 SSH 密钥（而非个人常用密钥）"
    echo ""

    prompt_yesno "是否启用 SSH 远程访问功能?" "n" SSH_ENABLED

    if [ "$SSH_ENABLED" = "yes" ]; then
        print_info "SSH 远程访问已启用"
        print_info "密钥挂载路径: $SSH_MOUNT_SOURCE -> /root/.ssh"

        # Check known_hosts
        if check_known_hosts; then
            print_success "known_hosts 文件将一同挂载"
        else
            print_warning "未检测到 known_hosts，首次连接需手动确认主机指纹"
            print_info "建议预先配置: ssh-keyscan -H <目标主机IP> >> /home/$RUN_USER/.ssh/known_hosts"
        fi
    else
        print_info "SSH 远程访问未启用"
    fi
}

# Check existing SSH config in docker-compose.yml (for upgrade mode)
check_existing_ssh_config() {
    local compose_file="$DEPLOY_DIR/docker-compose.yml"

    if [ -f "$compose_file" ]; then
        if grep -q "/root/.ssh" "$compose_file"; then
            print_info "检测到现有 SSH 密钥挂载配置"
            SSH_ENABLED="yes"
            return 0
        fi
    fi
    SSH_ENABLED="no"
    return 1
}

# ============================================================================
# Multi-User Workspace Sudo Configuration
# ============================================================================

# Stop and disable qwen-code-webui systemd service if it exists
# Open ACE will manage qwen-code-webui instances in multi-user mode
stop_webui_systemd_service() {
    local os_type=$(detect_os)

    # Skip on macOS (no systemd)
    if [[ "$os_type" == "macos" ]]; then
        return 0
    fi

    # Check if systemd is available
    if ! command -v systemctl &>/dev/null; then
        return 0
    fi

    # Check if qwen-code-webui service exists
    local service_name="qwen-code-webui"
    # List all service unit files and check if our service exists
    if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${service_name}.service"; then
        print_warning "检测到已存在的 qwen-code-webui systemd 服务"
        print_info "多用户模式下，Open ACE 会自动管理 qwen-code-webui 实例"
        print_info "停止并禁用独立运行的 qwen-code-webui 服务..."

        # Stop the service
        if systemctl is-active --quiet "${service_name}.service" 2>/dev/null; then
            systemctl stop "${service_name}.service"
            if [ $? -eq 0 ]; then
                print_success "已停止 ${service_name} 服务"
            else
                print_warning "停止 ${service_name} 服务失败"
            fi
        fi

        # Disable the service
        if systemctl is-enabled --quiet "${service_name}.service" 2>/dev/null; then
            systemctl disable "${service_name}.service"
            if [ $? -eq 0 ]; then
                print_success "已禁用 ${service_name} 服务"
            else
                print_warning "禁用 ${service_name} 服务失败"
            fi
        fi

        print_info "Open ACE 将在需要时自动启动 qwen-code-webui 实例"
    fi

    return 0
}

# Find qwen-code-webui executable
find_webui_executable() {
    local candidates=(
        "/usr/local/bin/qwen-code-webui"
        "/usr/bin/qwen-code-webui"
        "/opt/qwen-code-webui/bin/qwen-code-webui"
    )

    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    # Try to find in PATH
    if command -v qwen-code-webui &>/dev/null; then
        which qwen-code-webui
        return 0
    fi

    return 1
}

# Install qwen-code-webui via npm
install_qwen_code_webui() {
    print_header "安装 qwen-code-webui"

    # Check if npm is available
    if ! command -v npm &>/dev/null; then
        print_error "npm 未安装"
        print_info "请先安装 Node.js (包含 npm)"
        return 1
    fi

    print_info "检测到 npm 版本: $(npm --version)"
    print_info "正在安装 qwen-code-webui..."

    # Install qwen-code-webui
    if npm install -g qwen-code-webui 2>&1; then
        print_success "qwen-code-webui 安装完成"

        # Verify installation
        if command -v qwen-code-webui &>/dev/null; then
            local webui_path=$(which qwen-code-webui)
            print_success "安装路径: $webui_path"
            return 0
        else
            print_warning "安装完成但未找到可执行文件，请检查 npm 全局路径配置"
            return 1
        fi
    else
        print_error "qwen-code-webui 安装失败"
        print_info "请手动安装: npm install -g qwen-code-webui"
        return 1
    fi
}

# Find qwen-code executable (note: npm package @qwen-code/qwen-code installs as 'qwen')
find_qwen_code_executable() {
    local candidates=(
        "/usr/local/bin/qwen"
        "/usr/bin/qwen"
        "/opt/qwen-code/bin/qwen"
    )

    for candidate in "${candidates[@]}"; do
        if [ -x "$candidate" ]; then
            echo "$candidate"
            return 0
        fi
    done

    # Try to find in PATH
    if command -v qwen &>/dev/null; then
        which qwen
        return 0
    fi

    return 1
}

# Install qwen-code via npm
install_qwen_code() {
    print_header "安装 qwen-code"

    # Check if npm is available
    if ! command -v npm &>/dev/null; then
        print_error "npm 未安装"
        print_info "请先安装 Node.js (包含 npm)"
        return 1
    fi

    print_info "检测到 npm 版本: $(npm --version)"
    print_info "正在安装 @qwen-code/qwen-code..."

    # Install qwen-code (the official package name is @qwen-code/qwen-code, installs as 'qwen')
    if npm install -g @qwen-code/qwen-code 2>&1; then
        print_success "@qwen-code/qwen-code 安装完成"

        # Verify installation (note: the executable is named 'qwen', not 'qwen-code')
        if command -v qwen &>/dev/null; then
            local qwen_path=$(which qwen)
            print_success "安装路径: $qwen_path"
            return 0
        else
            print_warning "安装完成但未找到可执行文件，请检查 npm 全局路径配置"
            return 1
        fi
    else
        print_error "@qwen-code/qwen-code 安装失败"
        print_info "请手动安装: npm install -g @qwen-code/qwen-code"
        return 1
    fi
}

# Check and prompt for qwen-code installation
check_qwen_code() {
    local qwen_path=$(find_qwen_code_executable)
    if [ -n "$qwen_path" ]; then
        print_success "找到 qwen-code (qwen): $qwen_path"
        return 0
    fi

    print_warning "未找到 qwen-code 可执行文件"
    echo ""
    echo "请选择:"
    echo "  1) 协助安装 (通过 npm 自动安装)"
    echo "  2) 手动安装 (稍后自行安装)"
    echo ""

    prompt_input "请选择" "1" qwen_choice

    case "$qwen_choice" in
        1)
            install_qwen_code
            if [ $? -eq 0 ]; then
                return 0
            else
                print_info "安装失败，请手动安装后重新运行此脚本"
                return 1
            fi
            ;;
        2)
            print_info "请手动安装 qwen-code:"
            print_info "  npm install -g @qwen-code/qwen-code"
            print_info ""
            prompt_yesno "是否继续安装 Open ACE（稍后手动安装 qwen-code）?" "y" continue_without_qwen
            if [ "$continue_without_qwen" != "yes" ]; then
                return 1
            fi
            return 0
            ;;
        *)
            print_error "无效选择"
            return 1
            ;;
    esac
}

# Configure sudoers for multi-user workspace mode
configure_sudoers() {
    print_header "配置 Sudo 权限"

    # Check if running as root
    if [ "$(id -u)" -ne 0 ]; then
        print_error "需要 root 权限来配置 sudoers"
        print_info "请使用 sudo 运行安装脚本"
        return 1
    fi

    # Find webui executable
    local webui_path=$(find_webui_executable)
    if [ -z "$webui_path" ]; then
        print_warning "未找到 qwen-code-webui 可执行文件"
        echo ""
        echo "请选择:"
        echo "  1) 协助安装 (通过 npm 自动安装)"
        echo "  2) 手动安装 (稍后自行安装)"
        echo ""

        prompt_input "请选择" "1" webui_choice

        case "$webui_choice" in
            1)
                install_qwen_code_webui
                if [ $? -eq 0 ]; then
                    # Re-check for webui path after installation
                    webui_path=$(find_webui_executable)
                    if [ -z "$webui_path" ]; then
                        print_error "安装成功但仍未找到可执行文件"
                        print_info "请检查 npm 全局路径是否在 PATH 中"
                        return 1
                    fi
                    # Continue with sudoers configuration
                else
                    print_info "安装失败，请手动安装后重新运行此脚本"
                    print_info "  npm install -g qwen-code-webui"
                    return 1
                fi
                ;;
            2)
                print_info "请手动安装 qwen-code-webui:"
                print_info "  npm install -g qwen-code-webui"
                print_info ""
                print_info "安装完成后，重新运行此脚本或手动配置 sudoers:"
                print_info "  sudo visudo -f /etc/sudoers.d/open-ace-webui"
                print_info "  添加: $RUN_USER ALL=(ALL) NOPASSWD: /path/to/qwen-code-webui *"

                if [ "$NON_INTERACTIVE" = false ]; then
                    prompt_yesno "是否继续安装（稍后手动配置 sudoers）?" "y" continue_without_sudoers
                    if [ "$continue_without_sudoers" != "yes" ]; then
                        return 1
                    fi
                fi
                return 0
                ;;
            *)
                print_error "无效选择"
                return 1
                ;;
        esac
    fi

    print_success "找到 qwen-code-webui: $webui_path"

    # Create sudoers file
    local sudoers_file="/etc/sudoers.d/open-ace-webui"
    # NOTE: Commands must have '*' suffix to allow arguments (e.g., 'test -r', 'ls -1')
    local sudoers_content="# Open ACE WebUI - Multi-user mode sudo configuration
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Allows the service account to run qwen-code-webui as other users
# and perform file system operations as other users

$RUN_USER ALL=(ALL) NOPASSWD: $webui_path *
$RUN_USER ALL=(ALL) NOPASSWD: /usr/bin/test *, /usr/bin/ls *, /usr/bin/cat *, /usr/bin/stat *, /usr/bin/mkdir *, /usr/bin/chown *

# Preserve environment variables for sudo env_keep passing
Defaults env_keep += \"OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR OPENACE_PROXY_TOKEN OPENACE_PROXY_URL SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH\"
"

    # Check if sudoers file already exists
    if [ -f "$sudoers_file" ]; then
        if grep -q "$webui_path" "$sudoers_file" 2>/dev/null; then
            print_success "Sudoers 规则已存在"
            return 0
        fi
        print_info "更新现有 sudoers 文件..."
    fi

    # Write sudoers file
    echo "$sudoers_content" > "$sudoers_file"
    chmod 440 "$sudoers_file"

    # Validate sudoers syntax
    if visudo -c -f "$sudoers_file" &>/dev/null; then
        print_success "Sudoers 配置成功: $sudoers_file"
        print_info "服务账号 '$RUN_USER' 可以执行:"
        print_info "  sudo -u <username> $webui_path --port <port>"
    else
        print_error "Sudoers 语法错误，回滚..."
        rm -f "$sudoers_file"
        return 1
    fi

    return 0
}

show_help() {
    echo "Open ACE - Quick Install Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --non-interactive    Run without prompts (use defaults)"
    echo "  --deploy-dir DIR     Deployment directory (default: /opt/open-ace)"
    echo "  --image IMAGE        Docker image name (default: open-ace:latest)"
    echo "  --port PORT          Web server port (default: 5000)"
    echo "  --help, -h           Show this help message"
    echo ""
    echo "Environment Variables:"
    echo "  RUN_USER             User to run docker commands (default: open-ace)"
    echo "  RUN_USER_UID         UID for the run user (default: auto, for NFS/Docker bind mount set explicit UID)"
    echo "  DEPLOY_DIR           Deployment directory (default: /home/\$RUN_USER/open-ace)"
    echo "  IMAGE_NAME           Docker image name"
    echo "  WEB_PORT             Web server port"
    echo "  DB_USER              PostgreSQL username (default: \$RUN_USER)"
    echo "  DB_PASSWORD          PostgreSQL password"
    echo "  DB_NAME              PostgreSQL database name"
    echo "  DB_FILE              SQLite database filename (default: ace.db)"
    echo "  SECRET_KEY           Flask secret key"
    echo "  UPLOAD_AUTH_KEY      Upload authentication key"
    echo ""
    echo "Docker Auto-Install Support:"
    echo "  If Docker is not installed, the script will prompt to install it"
    echo "  automatically. Supported systems:"
    echo "    - macOS (via Homebrew)"
    echo "    - Debian/Ubuntu"
    echo "    - RHEL/CentOS"
    echo "    - Fedora"
    echo ""
    echo "Examples:"
    echo "  $0                              # Interactive deployment"
    echo "  $0 --non-interactive            # Use all defaults"
    echo "  $0 --deploy-dir /opt/my-ace     # Custom deployment directory"
    echo ""
    exit 0
}

# ============================================================================
# Docker Installation Functions
# ============================================================================

detect_os() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "macos"
    elif [[ -f /etc/debian_version ]]; then
        echo "debian"
    elif [[ -f /etc/redhat-release ]]; then
        echo "redhat"
    elif [[ -f /etc/centos-release ]]; then
        echo "centos"
    elif [[ -f /etc/fedora-release ]]; then
        echo "fedora"
    else
        echo "unknown"
    fi
}

# Detect CPU architecture and return Docker platform string
detect_arch() {
    local arch=""

    # Try uname -m first (most reliable)
    local machine_arch=$(uname -m 2>/dev/null || echo "")

    case "$machine_arch" in
        x86_64|amd64)
            arch="amd64"
            ;;
        aarch64|arm64)
            arch="arm64"
            ;;
        armv7l|armhf)
            arch="arm"
            ;;
        *)
            # Fallback: try to detect from Docker
            if command -v docker &>/dev/null && docker info &>/dev/null; then
                local docker_arch=$(docker info 2>/dev/null | grep -i "Architecture" | awk '{print $2}' || echo "")
                case "$docker_arch" in
                    x86_64|amd64)
                        arch="amd64"
                        ;;
                    aarch64|arm64)
                        arch="arm64"
                        ;;
                    *)
                        arch="amd64"  # Default fallback
                        ;;
                esac
            else
                arch="amd64"  # Default fallback
            fi
            ;;
    esac

    echo "$arch"
}

# Get the full Docker platform string (e.g., linux/amd64, linux/arm64)
get_docker_platform() {
    local os_type=$(detect_os)
    local arch=$(detect_arch)

    # Docker platform format: linux/amd64 or linux/arm64
    echo "linux/${arch}"
}

install_docker_macos() {
    print_info "检测到 macOS 系统"

    # Check if Homebrew is installed
    if ! command -v brew &>/dev/null; then
        print_warning "Homebrew 未安装"
        prompt_yesno "是否安装 Homebrew?" "y" install_brew
        if [ "$install_brew" = "yes" ]; then
            print_info "安装 Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            print_success "Homebrew 安装完成"
        else
            print_error "需要 Homebrew 来安装 Docker"
            return 1
        fi
    fi

    print_info "通过 Homebrew 安装 Docker..."
    brew install --cask docker

    print_success "Docker Desktop 安装完成"
    print_info "请启动 Docker Desktop 应用程序"
    return 0
}

install_docker_debian() {
    print_info "检测到 Debian/Ubuntu 系统"

    # Update package index
    print_info "更新软件包索引..."
    sudo apt-get update

    # Install dependencies
    print_info "安装依赖..."
    sudo apt-get install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release

    # Determine which mirror to use
    local distro=$(lsb_release -is | tr '[:upper:]' '[:lower:]')
    local codename=$(lsb_release -cs)
    local arch=$(dpkg --print-architecture)
    local keyring_file="/usr/share/keyrings/docker-archive-keyring.gpg"
    local sources_file="/etc/apt/sources.list.d/docker.list"
    local official_gpg_url="https://download.docker.com/linux/${distro}/gpg"
    local aliyun_gpg_url="https://mirrors.aliyun.com/docker-ce/linux/${distro}/gpg"
    local official_repo_url="https://download.docker.com/linux/${distro}"
    local aliyun_repo_url="https://mirrors.aliyun.com/docker-ce/linux/${distro}"
    local use_mirror="${DOCKER_INSTALL_MIRROR:-}"

    # If no explicit setting, ask user in interactive mode
    if [ -z "$use_mirror" ] && [ "$NON_INTERACTIVE" = false ]; then
        echo ""
        echo "请选择 Docker 软件源:"
        echo "  1) 官方源 (download.docker.com) - 海外网络推荐"
        echo "  2) 阿里云镜像 (mirrors.aliyun.com) - 国内网络推荐"
        echo ""
        prompt_input "请选择" "2" mirror_choice
        case "$mirror_choice" in
            1) use_mirror="official" ;;
            2|*) use_mirror="aliyun" ;;
        esac
    fi

    # Default to aliyun for non-interactive mode if not specified
    if [ -z "$use_mirror" ]; then
        use_mirror="aliyun"
    fi

    # Remove existing files to avoid conflicts
    sudo rm -f "$keyring_file"
    sudo rm -f "$sources_file"

    # Add Docker GPG key and repository
    print_info "添加 Docker GPG 密钥..."
    case "$use_mirror" in
        official)
            print_info "使用官方源..."
            if ! curl -fsSL "$official_gpg_url" | sudo gpg --dearmor -o "$keyring_file" 2>/dev/null; then
                print_warning "官方 GPG 密钥下载失败，切换到阿里云镜像..."
                curl -fsSL "$aliyun_gpg_url" | sudo gpg --dearmor -o "$keyring_file"
                echo "deb [arch=$arch signed-by=$keyring_file] $aliyun_repo_url $codename stable" | sudo tee "$sources_file" > /dev/null
            else
                echo "deb [arch=$arch signed-by=$keyring_file] $official_repo_url $codename stable" | sudo tee "$sources_file" > /dev/null
            fi
            ;;
        aliyun|*)
            print_info "使用阿里云镜像..."
            curl -fsSL "$aliyun_gpg_url" | sudo gpg --dearmor -o "$keyring_file"
            echo "deb [arch=$arch signed-by=$keyring_file] $aliyun_repo_url $codename stable" | sudo tee "$sources_file" > /dev/null
            ;;
    esac

    # Install Docker with retry on SSL failure
    print_info "安装 Docker..."
    sudo apt-get update
    local packages="docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    local install_output=""
    local install_success=false

    install_output=$(sudo apt-get install -y $packages 2>&1) && install_success=true || install_success=false

    if [ "$install_success" = false ]; then
        # Check if it's SSL/network error
        if echo "$install_output" | grep -qE "SSL connect error|Curl error|连接被对方重设|Cannot download|Failed to fetch|404"; then
            print_warning "检测到网络错误，切换到阿里云镜像重试..."

            # Clean up and switch to Aliyun mirror
            sudo rm -f "$keyring_file"
            sudo rm -f "$sources_file"

            print_info "添加阿里云镜像源..."
            curl -fsSL "$aliyun_gpg_url" | sudo gpg --dearmor -o "$keyring_file"
            echo "deb [arch=$arch signed-by=$keyring_file] $aliyun_repo_url $codename stable" | sudo tee "$sources_file" > /dev/null

            print_info "重新尝试安装..."
            sudo apt-get update
            if sudo apt-get install -y $packages; then
                install_success=true
            fi
        fi
    fi

    if [ "$install_success" = false ]; then
        print_error "Docker 安装失败，请检查网络或手动安装"
        print_info "手动安装参考: https://docs.docker.com/engine/install/ubuntu/"
        return 1
    fi

    # Start Docker service
    print_info "启动 Docker 服务..."
    sudo systemctl start docker
    sudo systemctl enable docker

    # Add current user to docker group
    print_info "将当前用户添加到 docker 组..."
    sudo usermod -aG docker "$USER"

    print_success "Docker 安装完成"
    print_warning "请注销并重新登录以使用户组生效"
    return 0
}

install_docker_redhat() {
    print_info "检测到 RHEL/CentOS 系统"

    # Install dependencies
    print_info "安装依赖..."
    sudo yum install -y yum-utils

    # Determine which mirror to use
    local repo_file="/etc/yum.repos.d/docker-ce.repo"
    local official_repo="https://download.docker.com/linux/centos/docker-ce.repo"
    local aliyun_repo="https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo"
    local use_mirror="${DOCKER_INSTALL_MIRROR:-}"

    # If no explicit setting, ask user in interactive mode
    if [ -z "$use_mirror" ] && [ "$NON_INTERACTIVE" = false ]; then
        echo ""
        echo "请选择 Docker 软件源:"
        echo "  1) 官方源 (download.docker.com) - 海外网络推荐"
        echo "  2) 阿里云镜像 (mirrors.aliyun.com) - 国内网络推荐"
        echo ""
        prompt_input "请选择" "2" mirror_choice
        case "$mirror_choice" in
            1) use_mirror="official" ;;
            2|*) use_mirror="aliyun" ;;
        esac
    fi

    # Default to aliyun for non-interactive mode if not specified
    if [ -z "$use_mirror" ]; then
        use_mirror="aliyun"
    fi

    # Remove existing repo file to avoid conflicts
    sudo rm -f "$repo_file"

    # Add Docker repository
    print_info "添加 Docker 软件源..."
    case "$use_mirror" in
        official)
            print_info "使用官方源..."
            if ! sudo yum-config-manager --add-repo "$official_repo" 2>/dev/null; then
                print_warning "官方源添加失败，切换到阿里云镜像..."
                sudo yum-config-manager --add-repo "$aliyun_repo"
            fi
            ;;
        aliyun|*)
            print_info "使用阿里云镜像..."
            sudo yum-config-manager --add-repo "$aliyun_repo"
            ;;
    esac

    # Install Docker with retry on SSL failure
    print_info "安装 Docker..."
    local packages="docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    local install_output=""
    local install_success=false

    install_output=$(sudo yum install -y $packages 2>&1) && install_success=true || install_success=false

    if [ "$install_success" = false ]; then
        # Check if it's SSL/network error
        if echo "$install_output" | grep -qE "SSL connect error|Curl error.*35|连接被对方重设|Cannot download|All mirrors were already tried"; then
            print_warning "检测到 SSL 连接错误，切换到阿里云镜像重试..."

            # Clean up and switch to Aliyun mirror
            sudo rm -f "$repo_file"
            sudo yum clean all

            print_info "添加阿里云镜像源..."
            sudo yum-config-manager --add-repo "$aliyun_repo"

            print_info "重新尝试安装..."
            if sudo yum install -y --nogpgcheck $packages; then
                install_success=true
            fi
        else
            # Non-SSL error, try --nogpgcheck
            print_warning "安装失败，尝试跳过 GPG 检查..."
            if sudo yum install -y --nogpgcheck $packages; then
                install_success=true
            fi
        fi
    fi

    if [ "$install_success" = false ]; then
        print_error "Docker 安装失败，请检查网络或手动安装"
        print_info "手动安装参考: https://docs.docker.com/engine/install/centos/"
        return 1
    fi

    # Start Docker service
    print_info "启动 Docker 服务..."
    sudo systemctl start docker
    sudo systemctl enable docker

    # Add current user to docker group
    print_info "将当前用户添加到 docker 组..."
    sudo usermod -aG docker "$USER"

    print_success "Docker 安装完成"
    print_warning "请注销并重新登录以使用户组生效"
    return 0
}

install_docker_fedora() {
    print_info "检测到 Fedora 系统"

    # Install dependencies
    print_info "安装依赖..."
    sudo dnf -y install dnf-plugins-core

    # Determine which mirror to use
    local repo_file="/etc/yum.repos.d/docker-ce.repo"
    local official_repo="https://download.docker.com/linux/fedora/docker-ce.repo"
    local aliyun_repo="https://mirrors.aliyun.com/docker-ce/linux/fedora/docker-ce.repo"
    local use_mirror="${DOCKER_INSTALL_MIRROR:-}"

    # If no explicit setting, ask user in interactive mode
    if [ -z "$use_mirror" ] && [ "$NON_INTERACTIVE" = false ]; then
        echo ""
        echo "请选择 Docker 软件源:"
        echo "  1) 官方源 (download.docker.com) - 海外网络推荐"
        echo "  2) 阿里云镜像 (mirrors.aliyun.com) - 国内网络推荐"
        echo ""
        prompt_input "请选择" "2" mirror_choice
        case "$mirror_choice" in
            1) use_mirror="official" ;;
            2|*) use_mirror="aliyun" ;;
        esac
    fi

    # Default to aliyun for non-interactive mode if not specified
    if [ -z "$use_mirror" ]; then
        use_mirror="aliyun"
    fi

    # Remove existing repo file to avoid conflicts
    sudo rm -f "$repo_file"

    # Add Docker repository
    print_info "添加 Docker 软件源..."
    case "$use_mirror" in
        official)
            print_info "使用官方源..."
            if ! sudo dnf config-manager --add-repo "$official_repo" 2>/dev/null; then
                print_warning "官方源添加失败，切换到阿里云镜像..."
                sudo dnf config-manager --add-repo "$aliyun_repo"
            fi
            ;;
        aliyun|*)
            print_info "使用阿里云镜像..."
            sudo dnf config-manager --add-repo "$aliyun_repo"
            ;;
    esac

    # Install Docker with retry on SSL failure
    print_info "安装 Docker..."
    local packages="docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin"
    local install_output=""
    local install_success=false

    install_output=$(sudo dnf install -y $packages 2>&1) && install_success=true || install_success=false

    if [ "$install_success" = false ]; then
        # Check if it's SSL/network error
        if echo "$install_output" | grep -qE "SSL connect error|Curl error.*35|连接被对方重设|Cannot download|All mirrors were already tried"; then
            print_warning "检测到 SSL 连接错误，切换到阿里云镜像重试..."

            # Clean up and switch to Aliyun mirror
            sudo rm -f "$repo_file"
            sudo dnf clean all

            print_info "添加阿里云镜像源..."
            sudo dnf config-manager --add-repo "$aliyun_repo"

            print_info "重新尝试安装..."
            if sudo dnf install -y --nogpgcheck $packages; then
                install_success=true
            fi
        else
            # Non-SSL error, try --nogpgcheck
            print_warning "安装失败，尝试跳过 GPG 检查..."
            if sudo dnf install -y --nogpgcheck $packages; then
                install_success=true
            fi
        fi
    fi

    if [ "$install_success" = false ]; then
        print_error "Docker 安装失败，请检查网络或手动安装"
        print_info "手动安装参考: https://docs.docker.com/engine/install/fedora/"
        return 1
    fi

    # Start Docker service
    print_info "启动 Docker 服务..."
    sudo systemctl start docker
    sudo systemctl enable docker

    # Add current user to docker group
    print_info "将当前用户添加到 docker 组..."
    sudo usermod -aG docker "$USER"

    print_success "Docker 安装完成"
    print_warning "请注销并重新登录以使用户组生效"
    return 0
}

install_docker() {
    local os_type=$(detect_os)

    print_header "安装 Docker"

    case "$os_type" in
        macos)
            install_docker_macos
            ;;
        debian)
            install_docker_debian
            ;;
        redhat|centos)
            install_docker_redhat
            ;;
        fedora)
            install_docker_fedora
            ;;
        *)
            print_error "不支持的操作系统: $OSTYPE"
            print_info "请手动安装 Docker: https://docs.docker.com/get-docker/"
            return 1
            ;;
    esac
}

# ============================================================================
# Node.js Installation Functions
# ============================================================================

NODEJS_VERSION="${NODEJS_VERSION:-20}"
MIN_NODE_VERSION="${MIN_NODE_VERSION:-18}"

# Check if Node.js and npm are installed with required version
check_nodejs() {
    if ! command -v node &>/dev/null; then
        return 1
    fi

    local node_version=$(node --version 2>/dev/null || echo "unknown")
    local npm_version=$(npm --version 2>/dev/null || echo "unknown")

    # Check version requirement for frontend build
    if [ "$node_version" != "unknown" ]; then
        local major_version=$(echo "$node_version" | sed 's/^v//' | cut -d. -f1)
        if [ "$major_version" -lt "$MIN_NODE_VERSION" ]; then
            print_warning "Node.js 版本过低: $node_version (需要 >= v$MIN_NODE_VERSION)"
            print_warning "前端构建依赖需要 Node.js >= v$MIN_NODE_VERSION"
            return 1
        fi
    fi

    print_success "Node.js 已安装: $node_version"
    print_success "npm 已安装: $npm_version"
    return 0
}

install_nodejs_macos() {
    print_info "检测到 macOS 系统"

    # Check if Homebrew is installed
    if ! command -v brew &>/dev/null; then
        print_warning "Homebrew 未安装"
        prompt_yesno "是否安装 Homebrew?" "y" install_brew
        if [ "$install_brew" = "yes" ]; then
            print_info "安装 Homebrew..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
            print_success "Homebrew 安装完成"
        else
            print_error "需要 Homebrew 来安装 Node.js"
            return 1
        fi
    fi

    print_info "通过 Homebrew 安装 Node.js..."
    brew install node

    # Check if installed version meets requirement
    local installed_version=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
    if [ "$installed_version" -lt "$MIN_NODE_VERSION" ]; then
        print_warning "Homebrew 安装的版本过低: v$installed_version (需要 >= v$MIN_NODE_VERSION)"
        print_info "尝试升级 Node.js..."
        brew upgrade node
        installed_version=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
        if [ "$installed_version" -lt "$MIN_NODE_VERSION" ]; then
            print_error "无法安装满足要求的 Node.js 版本"
            return 1
        fi
    fi

    print_success "Node.js 安装完成"
    print_info "Node.js 版本: $(node --version)"
    return 0
}

install_nodejs_debian() {
    print_info "检测到 Debian/Ubuntu 系统"

    # Install dependencies
    print_info "安装依赖..."
    sudo apt-get update
    sudo apt-get install -y curl

    # Try official NodeSource first, fallback to domestic mirror
    print_info "添加 Node.js 软件源..."
    local nodesource_url="https://deb.nodesource.com/setup_${NODEJS_VERSION}.x"
    local domestic_mirror_url="https://mirrors.tuna.tsinghua.edu.cn/nodesource/deb/setup_${NODEJS_VERSION}.x"

    if ! curl -fsSL "$nodesource_url" | sudo -E bash 2>/dev/null; then
        print_warning "官方源连接失败，尝试使用清华镜像..."
        if ! curl -fsSL "$domestic_mirror_url" | sudo -E bash 2>/dev/null; then
            print_warning "镜像源也失败，尝试直接安装系统自带版本..."
            sudo apt-get install -y nodejs npm
            if [ $? -eq 0 ]; then
                # Check if installed version meets requirement
                local installed_version=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
                if [ "$installed_version" -lt "$MIN_NODE_VERSION" ]; then
                    print_warning "系统自带版本过低: v$installed_version (需要 >= v$MIN_NODE_VERSION)"
                    print_info "建议手动安装更高版本:"
                    print_info "  方法1: 使用 nvm (推荐)"
                    print_info "    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash"
                    print_info "    source ~/.bashrc"
                    print_info "    nvm install $NODEJS_VERSION"
                    print_info "  方法2: 从官网下载二进制包"
                    print_info "    https://nodejs.org/dist/v$NODEJS_VERSION.0/"
                    return 1
                fi
                print_success "Node.js 安装完成 (系统自带版本)"
                return 0
            fi
            print_error "无法安装 Node.js"
            print_info "请检查网络连接或手动安装: https://nodejs.org/"
            return 1
        fi
    fi

    # Install Node.js
    print_info "安装 Node.js..."
    sudo apt-get install -y nodejs

    print_success "Node.js 安装完成"
    print_info "Node.js 版本: $(node --version)"
    print_info "npm 版本: $(npm --version)"
    return 0
}

install_nodejs_redhat() {
    print_info "检测到 RHEL/CentOS 系统"

    # Install dependencies
    print_info "安装依赖..."
    sudo yum install -y curl

    # Try official NodeSource first, fallback to domestic mirror
    print_info "添加 Node.js 软件源..."
    local nodesource_url="https://rpm.nodesource.com/pub_${NODEJS_VERSION}.x/nodistro/repo/nodesource-nodistro.repo"
    local domestic_mirror_url="https://mirrors.tuna.tsinghua.edu.cn/nodesource/rpm/pub_${NODEJS_VERSION}.x/nodistro/repo/nodesource-nodistro.repo"

    # Create repo directory
    sudo mkdir -p /etc/yum.repos.d

    if ! sudo curl -fsSL "$nodesource_url" -o /etc/yum.repos.d/nodesource.repo 2>/dev/null; then
        print_warning "官方源连接失败，尝试使用清华镜像..."
        if ! sudo curl -fsSL "$domestic_mirror_url" -o /etc/yum.repos.d/nodesource.repo 2>/dev/null; then
            print_warning "镜像源也失败，尝试使用系统模块化安装..."

            # Try dnf module install for Rocky/RHEL 9+ (supports nodejs 18/20/22/24)
            if command -v dnf &>/dev/null; then
                # Check available nodejs modules
                print_info "检查可用的 Node.js 模块版本..."
                local module_versions=$(dnf module list nodejs --quiet 2>/dev/null | grep -E "^\s*nodejs" | awk '{print $2}' | sort -V)

                if [ -n "$module_versions" ]; then
                    print_info "可用版本: $module_versions"

                    # Try to install the target version first
                    for target_version in "$NODEJS_VERSION" "20" "18"; do
                        if echo "$module_versions" | grep -q "^${target_version}"; then
                            print_info "尝试安装 Node.js $target_version 模块..."
                            # Reset any existing module stream first
                            sudo dnf module reset nodejs -y 2>/dev/null
                            sudo dnf module install nodejs:${target_version} -y
                            if [ $? -eq 0 ] && command -v node &>/dev/null; then
                                local installed_version=$(node --version | sed 's/v//')
                                local major_version=$(echo "$installed_version" | cut -d. -f1)
                                if [ "$major_version" -ge "$MIN_NODE_VERSION" ]; then
                                    print_success "Node.js 安装完成 (模块版本 $target_version)"
                                    print_info "Node.js 版本: $(node --version)"
                                    print_info "npm 版本: $(npm --version)"
                                    return 0
                                fi
                                print_warning "模块版本仍过低: v$installed_version"
                            fi
                        fi
                    done
                fi
            fi

            # Fallback to yum install (may install older version)
            print_warning "模块安装失败，尝试系统自带版本..."
            sudo yum install -y nodejs npm
            if [ $? -eq 0 ]; then
                # Check installed version
                if command -v node &>/dev/null; then
                    local installed_version=$(node --version | sed 's/v//')
                    local major_version=$(echo "$installed_version" | cut -d. -f1)
                    if [ "$major_version" -ge "$MIN_NODE_VERSION" ]; then
                        print_success "Node.js 安装完成 (系统自带版本)"
                        print_info "Node.js 版本: $(node --version)"
                        print_info "npm 版本: $(npm --version)"
                        return 0
                    else
                        print_warning "系统自带版本过低: v$installed_version (需要 >= v$MIN_NODE_VERSION)"
                        print_info "请手动安装更高版本:"
                        print_info "  方法1: 使用 nvm (推荐)"
                        print_info "    curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash"
                        print_info "    source ~/.bashrc"
                        print_info "    nvm install $NODEJS_VERSION"
                        print_info "  方法2: 下载官方二进制包"
                        print_info "    https://nodejs.org/dist/v$NODEJS_VERSION.0/"
                        return 1
                    fi
                fi
                print_success "Node.js 安装完成"
                return 0
            fi
            print_error "无法安装 Node.js"
            print_info "请检查网络连接或手动安装: https://nodejs.org/"
            return 1
        fi
    fi

    # Install Node.js
    print_info "安装 Node.js..."
    sudo yum install -y nodejs

    print_success "Node.js 安装完成"
    print_info "Node.js 版本: $(node --version)"
    print_info "npm 版本: $(npm --version)"
    return 0
}

install_nodejs_fedora() {
    print_info "检测到 Fedora 系统"

    # Install Node.js from Fedora repositories
    print_info "安装 Node.js..."
    sudo dnf install -y nodejs npm

    # Check if installed version meets requirement
    local installed_version=$(node --version 2>/dev/null | sed 's/^v//' | cut -d. -f1)
    if [ "$installed_version" -lt "$MIN_NODE_VERSION" ]; then
        print_warning "Fedora 系统自带版本过低: v$installed_version (需要 >= v$MIN_NODE_VERSION)"
        print_info "建议使用 nvm 安装更高版本:"
        print_info "  curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.0/install.sh | bash"
        print_info "  source ~/.bashrc"
        print_info "  nvm install $NODEJS_VERSION"
        return 1
    fi

    print_success "Node.js 安装完成"
    print_info "Node.js 版本: $(node --version)"
    print_info "npm 版本: $(npm --version)"
    return 0
}

install_nodejs() {
    local os_type=$(detect_os)

    print_header "安装 Node.js"

    case "$os_type" in
        macos)
            install_nodejs_macos
            ;;
        debian)
            install_nodejs_debian
            ;;
        redhat|centos)
            install_nodejs_redhat
            ;;
        fedora)
            install_nodejs_fedora
            ;;
        *)
            print_error "不支持的操作系统: $OSTYPE"
            print_info "请手动安装 Node.js: https://nodejs.org/"
            return 1
            ;;
    esac
}

# ============================================================================
# Docker Hub Mirror Configuration
# ============================================================================

configure_docker_mirror() {
    local os_type=$(detect_os)

    # Skip on macOS (Docker Desktop handles this)
    if [[ "$os_type" == "macos" ]]; then
        print_info "macOS Docker Desktop 可通过设置界面配置镜像加速器"
        return 0
    fi

    print_header "配置 Docker Hub 镜像加速器"

    # Check if Docker daemon is running
    if ! docker info &>/dev/null; then
        print_warning "Docker daemon 未运行，跳过镜像加速器配置"
        return 0
    fi

    # Check if mirror is already configured
    local daemon_json="/etc/docker/daemon.json"
    if [ -f "$daemon_json" ]; then
        if grep -q "registry-mirrors" "$daemon_json" 2>/dev/null; then
            print_success "镜像加速器已配置"
            print_info "当前配置: $(grep 'registry-mirrors' "$daemon_json")"
            return 0
        fi
    fi

    # Prompt to configure mirror
    prompt_yesno "是否配置 Docker Hub 镜像加速器 (国内网络建议配置)?" "y" configure_mirror
    if [ "$configure_mirror" != "yes" ]; then
        print_info "跳过镜像加速器配置"
        return 0
    fi

    # Create or update daemon.json
    print_info "配置镜像加速器..."

    # Create directory if not exists
    sudo mkdir -p /etc/docker

    # Default mirror list (domestic mirrors for China)
    # Note: mirrors_json contains proper JSON array string
    local mirrors_json='["https://docker.1ms.run","https://docker.xuanyuan.me"]'

    # Check existing config
    if [ -f "$daemon_json" ]; then
        # Merge with existing config
        local existing_config=$(cat "$daemon_json" 2>/dev/null || echo "{}")
        # Simple merge - add registry-mirrors to existing config
        print_info "更新现有 Docker 配置..."
        # Use printf to properly inject the mirrors_json variable into Python code
        if printf '%s\n' "$existing_config" | python3 -c "import json,sys; c=json.load(sys.stdin); c['registry-mirrors']=json.loads('$mirrors_json'); json.dump(c,sys.stdout,indent=2)" > /tmp/daemon.json.new 2>/dev/null; then
            sudo mv /tmp/daemon.json.new "$daemon_json"
        else
            # Fallback: create new config with mirrors only
            print_warning "无法合并配置，创建新配置文件..."
            printf '%s\n' "{\"registry-mirrors\": $mirrors_json}" | sudo tee "$daemon_json" > /dev/null
        fi
    else
        # Create new config - use printf with double quotes for variable expansion
        printf '%s\n' "{\"registry-mirrors\": $mirrors_json}" | sudo tee "$daemon_json" > /dev/null
    fi

    # Restart Docker daemon
    print_info "重启 Docker 服务以应用配置..."
    sudo systemctl restart docker

    # Wait for Docker to be ready
    sleep 3
    if docker info &>/dev/null; then
        print_success "Docker Hub 镜像加速器配置完成"
        print_info "已配置镜像源:"
        print_info "  - docker.1ms.run"
        print_info "  - docker.xuanyuan.me"
    else
        print_warning "Docker 重启后未恢复，请手动检查"
    fi

    return 0
}

# ============================================================================
# Firewall Configuration
# ============================================================================

configure_firewall() {
    local port="$1"
    local os_type=$(detect_os)

    # Skip on macOS (no firewall configuration needed typically)
    if [[ "$os_type" == "macos" ]]; then
        print_info "macOS 通常不需要配置防火墙"
        return 0
    fi

    print_header "配置防火墙"

    # Check if we have sudo access
    if ! sudo -n true 2>/dev/null; then
        print_warning "需要 sudo 权限配置防火墙"
        return 0
    fi

    # Try firewalld first (CentOS/RHEL/Fedora)
    if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld; then
        print_info "检测到 firewalld"

        # Check if port is already open
        if sudo firewall-cmd --list-ports | grep -q "${port}/tcp"; then
            print_success "端口 $port 已开放"
            return 0
        fi

        # Open port
        print_info "开放端口 $port/tcp..."
        sudo firewall-cmd --permanent --add-port=${port}/tcp
        sudo firewall-cmd --reload

        print_success "防火墙端口 $port 已开放"
        return 0
    fi

    # Try ufw (Ubuntu/Debian)
    if command -v ufw &>/dev/null; then
        print_info "检测到 ufw"

        # Check if ufw is active
        if ! sudo ufw status | grep -q "Status: active"; then
            print_warning "ufw 未启用，跳过防火墙配置"
            print_info "如需启用，请运行: sudo ufw enable"
            return 0
        fi

        # Check if port is already open
        if sudo ufw status | grep -q "${port}/tcp"; then
            print_success "端口 $port 已开放"
            return 0
        fi

        # Open port
        print_info "开放端口 $port/tcp..."
        sudo ufw allow ${port}/tcp

        print_success "防火墙端口 $port 已开放"
        return 0
    fi

    # Try iptables as fallback
    if command -v iptables &>/dev/null; then
        print_info "检测到 iptables"

        # Check if iptables is actually being used (has active rules)
        # If iptables has no active rules, firewall is likely disabled
        local iptables_rules=$(sudo iptables -L INPUT -n 2>/dev/null | grep -v "^Chain\|^target" | wc -l)
        if [ "$iptables_rules" -le 2 ]; then
            # Very few or no rules means iptables is not actively managing firewall
            print_info "iptables 未启用或无活跃规则，跳过防火墙配置"
            print_info "端口默认开放，如需启用防火墙请手动配置"
            return 0
        fi

        # Check if port is already open
        if sudo iptables -L INPUT -n | grep -q "dpt:${port}"; then
            print_success "端口 $port 已开放"
            return 0
        fi

        # Open port
        print_info "开放端口 $port/tcp..."
        sudo iptables -I INPUT -p tcp --dport ${port} -j ACCEPT

        # Try to save iptables rules (create directory if needed)
        if command -v iptables-save &>/dev/null; then
            # Try Debian/Ubuntu path first
            if [ -d "/etc/iptables" ]; then
                sudo iptables-save > /etc/iptables/rules.v4 2>/dev/null || true
            elif [ -d "/etc/sysconfig" ]; then
                # Try RHEL/CentOS path
                sudo iptables-save > /etc/sysconfig/iptables 2>/dev/null || true
            else
                # No standard save directory, just warn
                print_warning "iptables 规则已添加但未持久化保存"
                print_info "请手动保存规则或配置防火墙持久化"
            fi
        fi

        print_success "防火墙端口 $port 已开放"
        print_warning "请注意: iptables 规则可能需要手动保存以持久化"
        return 0
    fi

    # No firewall detected
    print_warning "未检测到防火墙 (firewalld/ufw/iptables)"
    print_info "如果服务器有其他防火墙，请手动开放端口 $port"
    return 0
}

# Configure firewall for a range of ports (for multi-user workspace mode)
configure_firewall_range() {
    local start_port="$1"
    local end_port="$2"
    local os_type=$(detect_os)

    # Skip on macOS
    if [[ "$os_type" == "macos" ]]; then
        print_info "macOS 通常不需要配置防火墙"
        return 0
    fi

    print_header "配置防火墙端口范围"

    # Check if we have sudo access
    if ! sudo -n true 2>/dev/null; then
        print_warning "需要 sudo 权限配置防火墙"
        return 0
    fi

    # Try firewalld first (CentOS/RHEL/Fedora)
    if command -v firewall-cmd &>/dev/null && systemctl is-active --quiet firewalld; then
        print_info "检测到 firewalld"
        print_info "开放端口范围 ${start_port}-${end_port}/tcp..."
        sudo firewall-cmd --permanent --add-port=${start_port}-${end_port}/tcp
        sudo firewall-cmd --reload
        print_success "防火墙端口范围 ${start_port}-${end_port} 已开放"
        return 0
    fi

    # Try ufw (Ubuntu/Debian)
    if command -v ufw &>/dev/null; then
        print_info "检测到 ufw"
        if sudo ufw status | grep -q "Status: active"; then
            print_info "开放端口范围 ${start_port}:${end_port}/tcp..."
            sudo ufw allow ${start_port}:${end_port}/tcp
            print_success "防火墙端口范围 ${start_port}-${end_port} 已开放"
        else
            print_warning "ufw 未启用，跳过防火墙配置"
        fi
        return 0
    fi

    # Try iptables as fallback
    if command -v iptables &>/dev/null; then
        print_info "检测到 iptables"
        print_info "开放端口范围 ${start_port}-${end_port}/tcp..."
        # Use multiport for efficiency
        sudo iptables -I INPUT -p tcp -m multiport --dports ${start_port}:${end_port} -j ACCEPT 2>/dev/null || {
            # Fallback: add each port individually (slower but more compatible)
            for port in $(seq $start_port $end_port); do
                sudo iptables -I INPUT -p tcp --dport ${port} -j ACCEPT
            done
        }
        print_success "防火墙端口范围 ${start_port}-${end_port} 已开放"
        return 0
    fi

    print_warning "未检测到防火墙 (firewalld/ufw/iptables)"
    print_info "如果服务器有其他防火墙，请手动开放端口范围 ${start_port}-${end_port}"
    return 0
}

# ============================================================================
# Check Prerequisites
# ============================================================================

check_prerequisites() {
    print_header "检查系统环境"

    # Check Docker
    if ! command -v docker &>/dev/null; then
        print_warning "Docker 未安装"
        prompt_yesno "是否自动安装 Docker?" "y" install_docker_confirm
        if [ "$install_docker_confirm" = "yes" ]; then
            install_docker

            # Check if installation was successful
            if ! command -v docker &>/dev/null; then
                print_error "Docker 安装失败"
                exit 1
            fi

            # On macOS, Docker Desktop needs to be started manually
            if [[ "$OSTYPE" == "darwin"* ]]; then
                print_info "请启动 Docker Desktop 后重新运行此脚本"
                exit 0
            fi

            # Configure Docker Hub mirror after installation
            configure_docker_mirror
        else
            print_info "请手动安装 Docker: https://docs.docker.com/get-docker/"
            exit 1
        fi
    else
        print_success "Docker 已安装: $(docker --version)"

        # Offer to configure Docker Hub mirror even if Docker is already installed
        if [ "$NON_INTERACTIVE" = false ]; then
            configure_docker_mirror
        fi
    fi

    # Check Docker Compose
    if ! docker compose version &>/dev/null; then
        print_error "Docker Compose 未安装"
        print_info "请先安装 Docker Compose"
        exit 1
    fi
    print_success "Docker Compose 已安装: $(docker compose version)"

    # Check if Docker daemon is running
    if ! docker info &>/dev/null; then
        print_warning "Docker daemon 未运行"

        # Try to start Docker service on Linux
        if [[ "$OSTYPE" != "darwin"* ]] && command -v systemctl &>/dev/null; then
            print_info "尝试启动 Docker 服务..."
            sudo systemctl start docker 2>/dev/null || true
            sleep 3
        fi

        # Check again
        if ! docker info &>/dev/null; then
            print_error "Docker daemon 未运行"
            print_info "请启动 Docker Desktop 或运行: sudo systemctl start docker"
            exit 1
        fi
    fi
    print_success "Docker daemon 运行中"

    # Check Node.js (optional but recommended for multi-user mode and local build)
    if ! check_nodejs; then
        print_warning "Node.js 未安装"
        print_info "Node.js 用于:"
        print_info "  - 多用户模式: 安装 qwen-code-webui"
        print_info "  - 本地构建镜像: 构建前端"
        prompt_yesno "是否自动安装 Node.js?" "y" install_nodejs_confirm
        if [ "$install_nodejs_confirm" = "yes" ]; then
            install_nodejs
        else
            print_info "可稍后手动安装: https://nodejs.org/"
        fi
    fi

    # Check qwen-code (optional, for workspace functionality)
    # Only check if workspace is enabled or user wants to use it
    print_info "检查 qwen-code..."
    if ! check_qwen_code; then
        print_warning "qwen-code 检查失败，但不影响基本部署"
        print_info "如需使用 Workspace 功能，请确保安装 qwen-code"
    fi

    # Check/load Docker image
    build_docker_image
}

build_docker_image() {
    print_info "检查 Docker 镜像..."

    # Check if image already exists
    if docker image inspect "$IMAGE_NAME" &>/dev/null; then
        print_success "镜像 $IMAGE_NAME 已存在"
        return 0
    fi

    # Image not found - prompt to load/build/pull
    print_warning "镜像 $IMAGE_NAME 不存在"
    echo ""
    echo "请选择:"
    echo "  1) 加载镜像文件 (包含应用和 PostgreSQL)"
    echo "  2) 从 Docker Hub 拉取镜像"
    echo "  3) 本地构建镜像 (自动构建前端)"
    echo "  4) 跳过 (稍后手动处理)"
    echo ""

    prompt_input "请选择" "1" build_choice

    case "$build_choice" in
        1)
            prompt_input "镜像文件路径" "open-ace-images.tar.gz" image_file
            if [ -z "$image_file" ]; then
                image_file="open-ace-images.tar.gz"
            fi
            if [ -f "$image_file" ]; then
                print_info "加载镜像文件: $image_file"

                # Check if file is compressed (gzip)
                if file "$image_file" | grep -q "gzip compressed"; then
                    print_info "检测到 gzip 压缩文件，解压并加载..."
                    gunzip -c "$image_file" | docker load
                else
                    docker load -i "$image_file"
                fi

                # Check if both images are loaded
                local images_loaded=0
                if docker image inspect "$IMAGE_NAME" &>/dev/null; then
                    print_success "应用镜像加载完成: $IMAGE_NAME"
                    images_loaded=$((images_loaded + 1))
                fi

                if docker image inspect "postgres:15-alpine" &>/dev/null; then
                    print_success "PostgreSQL 镜像加载完成: postgres:15-alpine"
                    images_loaded=$((images_loaded + 1))
                fi

                if [ $images_loaded -ge 1 ]; then
                    print_success "镜像加载完成 (共 $images_loaded 个)"
                    return 0
                else
                    print_error "镜像加载失败，请检查文件是否正确"
                    return 1
                fi
            else
                print_error "文件不存在: $image_file"
                print_info "请先将镜像文件拷贝到服务器"
                print_info "导出命令: ./scripts/export-image.sh --compress"
                return 1
            fi
            ;;
        2)
            print_info "从 Docker Hub 拉取镜像..."

            # Pull application image
            print_info "拉取镜像: $IMAGE_NAME"
            if docker pull "$IMAGE_NAME"; then
                print_success "应用镜像拉取完成: $IMAGE_NAME"
            else
                print_error "镜像拉取失败"
                print_info "请检查:"
                print_info "  1. Docker Hub 镜像加速器是否已配置"
                print_info "  2. 网络连接是否正常"
                print_info "  3. 镜像名称是否正确"
                return 1
            fi

            # Pull PostgreSQL image
            local postgres_image="postgres:15-alpine"
            print_info "拉取 PostgreSQL 镜像: $postgres_image"
            if docker pull "$postgres_image"; then
                print_success "PostgreSQL 镜像拉取完成: $postgres_image"
            else
                print_warning "PostgreSQL 镜像拉取失败，后续部署时会再次尝试"
            fi

            return 0
            ;;
        3)
            print_info "本地构建镜像..."

            # Find Open ACE source directory
            local source_dir=""
            # Try to find from script location
            if [ -f "$0" ]; then
                local script_dir=$(cd "$(dirname "$0")" && pwd)
                # script is at scripts/install-central/docker-method/install.sh
                # source dir is at parent/parent/parent
                local possible_source=$(cd "$script_dir/../../../.." && pwd)
                if [ -f "$possible_source/Dockerfile" ] && [ -d "$possible_source/frontend" ]; then
                    source_dir="$possible_source"
                fi
            fi

            # Try current directory
            if [ -z "$source_dir" ]; then
                if [ -f "Dockerfile" ] && [ -d "frontend" ]; then
                    source_dir="$(pwd)"
                fi
            fi

            # Try common paths
            if [ -z "$source_dir" ]; then
                for path in "/opt/open-ace" "/root/open-ace" "/home/open-ace/open-ace" "/tools/open-ace"; do
                    if [ -f "$path/Dockerfile" ] && [ -d "$path/frontend" ]; then
                        source_dir="$path"
                        break
                    fi
                done
            fi

            if [ -z "$source_dir" ]; then
                print_error "未找到 Open ACE 源码目录"
                print_info "请指定源码目录路径:"
                prompt_input "源码目录路径" "" source_dir
                if [ -z "$source_dir" ] || [ ! -f "$source_dir/Dockerfile" ]; then
                    print_error "无效的源码目录或缺少 Dockerfile"
                    return 1
                fi
            fi

            print_success "找到源码目录: $source_dir"

            # Check Node.js for frontend build
            if ! check_nodejs; then
                print_warning "Node.js 未安装，需要先安装才能构建前端"
                prompt_yesno "是否自动安装 Node.js?" "y" install_nodejs_now
                if [ "$install_nodejs_now" = "yes" ]; then
                    install_nodejs
                    if ! check_nodejs; then
                        print_error "Node.js 安装失败"
                        return 1
                    fi
                else
                    print_error "构建镜像需要 Node.js，请手动安装后重新运行"
                    return 1
                fi
            fi

            # Build frontend
            print_info "构建前端..."
            cd "$source_dir/frontend"
            if [ ! -d "node_modules" ]; then
                print_info "安装前端依赖..."
                npm install
                if [ $? -ne 0 ]; then
                    print_error "前端依赖安装失败"
                    return 1
                fi
            fi

            print_info "执行前端构建..."
            npm run build
            if [ $? -ne 0 ]; then
                print_error "前端构建失败"
                return 1
            fi
            print_success "前端构建完成"

            # Check Docker Hub access before building
            print_info "检测 Docker 镜像拉取能力..."
            if ! check_docker_image_access; then
                print_warning "Docker Hub 无法访问"
                select_docker_image_source
                local source_result=$?
                if [ $source_result -eq 2 ]; then
                    # User chose to manually import
                    print_info "请导入以下基础镜像后重新运行:"
                    print_info "  - python:3.11-slim"
                    print_info "  - postgres:15-alpine"
                    return 1
                elif [ $source_result -ne 0 ]; then
                    return 1
                fi
            fi

            # Pre-pull base images with retry from domestic mirrors
            local base_images=("python:3.11-slim" "postgres:15-alpine")
            for base_image in "${base_images[@]}"; do
                if ! docker image inspect "$base_image" &>/dev/null; then
                    pull_base_image_with_retry "$base_image"
                    if [ $? -ne 0 ]; then
                        print_warning "基础镜像 $base_image 拉取失败，构建可能失败"
                        print_info "您可以手动导入该镜像后继续"
                        prompt_yesno "是否继续尝试构建?" "n" continue_build
                        if [ "$continue_build" != "yes" ]; then
                            return 1
                        fi
                    fi
                fi
            done

            # Build Docker image
            cd "$source_dir"
            print_info "构建 Docker 镜像..."
            docker build -t "$IMAGE_NAME" --target production .
            if [ $? -ne 0 ]; then
                print_error "Docker 镜像构建失败"
                return 1
            fi
            print_success "Docker 镜像构建完成: $IMAGE_NAME"

            # Also pull PostgreSQL image if needed
            local postgres_image="postgres:15-alpine"
            if ! docker image inspect "$postgres_image" &>/dev/null; then
                print_info "拉取 PostgreSQL 镜像: $postgres_image"
                docker pull "$postgres_image" || print_warning "PostgreSQL 镜像拉取失败，后续部署时会再次尝试"
            fi

            return 0
            ;;
        4)
            print_info "请手动处理镜像后重新运行此脚本"
            print_info "选项:"
            print_info "  - 加载镜像: docker load -i open-ace-images.tar.gz"
            print_info "  - 拉取镜像: docker pull $IMAGE_NAME"
            print_info "  - 构建镜像: cd <source-dir> && docker build -t $IMAGE_NAME --target production ."
            return 1
            ;;
        *)
            print_error "无效选择"
            return 1
            ;;
    esac
}


# ============================================================================
# Upgrade Functions
# ============================================================================

# Detect existing Open ACE deployment
# Supports two container names:
#   - open-ace: current version (install.sh generated deployment)
#   - open-ace-web: legacy version (source directory docker-compose.yml)
# Returns: 0 if deployment exists, 1 if not
detect_existing_deployment() {
    local found_container=""

    # Check for existing containers with either name
    for container_name in "open-ace" "open-ace-web"; do
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${container_name}$"; then
            found_container="$container_name"
            break
        fi
    done

    # If no container found, try to detect deployment by config file
    # This handles the case where containers were removed (docker compose down)
    # but config files and data volumes still exist
    if [ -z "$found_container" ]; then
        print_info "未检测到容器，检查是否存在配置文件..."

        # Scan common deployment directories for config.json
        local found_config=false
        for scan_dir in \
            "/home/open-ace/open-ace" \
            "/home/ivyent/open-ace" \
            "/opt/open-ace" \
            "/root/open-ace" \
            "/tools/open-ace" \
            "$DEPLOY_DIR"; do
            if [ -f "$scan_dir/config/config.json" ]; then
                DEPLOY_DIR="$scan_dir"
                print_success "检测到配置文件: $scan_dir/config/config.json"
                found_config=true
                break
            fi
        done

        if [ "$found_config" = false ]; then
            # No existing deployment found
            return 1
        fi

        # Verify the deployment directory exists
        if [ ! -d "$DEPLOY_DIR" ]; then
            print_warning "部署目录不存在: $DEPLOY_DIR"
            return 1
        fi

        return 0
    fi

    # Get deployment directory from container mount info
    # The container mounts config directory at /root/.open-ace (read-only)
    # We need to find the mount where Destination is /root/.open-ace
    local deploy_config_dir=$(docker inspect ${found_container} --format \
        '{{ range .Mounts }}{{ if eq .Destination "/root/.open-ace" }}{{ .Source }}{{ end }}{{ end }}' 2>/dev/null)

    if [ -z "$deploy_config_dir" ]; then
        print_warning "无法从容器获取配置目录"
        return 1
    fi

    # The Source is the config directory itself (e.g., /home/ivyent/open-ace/config)
    # We need to get the parent directory (the deploy directory)
    DEPLOY_DIR=$(dirname "$deploy_config_dir")

    # Verify the deployment directory exists
    if [ ! -d "$DEPLOY_DIR" ]; then
        print_warning "部署目录不存在: $DEPLOY_DIR"
        return 1
    fi

    # Verify config.json exists
    if [ ! -f "$DEPLOY_DIR/config/config.json" ]; then
        print_warning "配置文件不存在: $DEPLOY_DIR/config/config.json"
        return 1
    fi

    return 0
}

# Read configuration from existing deployment
read_existing_config() {
    local config_file="$DEPLOY_DIR/config/config.json"
    local env_file="$DEPLOY_DIR/.env"
    local compose_file="$DEPLOY_DIR/docker-compose.yml"

    # Check if jq is available
    if ! command -v jq &>/dev/null; then
        print_error "jq 未安装，无法读取配置文件"
        print_info "请安装 jq: yum install jq 或 apt install jq"
        return 1
    fi

    # Read from config.json
    if [ -f "$config_file" ]; then
        HOST_NAME=$(jq -r '.host_name' "$config_file" 2>/dev/null || echo "")
        WEB_PORT=$(jq -r '.server.web_port' "$config_file" 2>/dev/null || echo "5000")
        WORKSPACE_ENABLED=$(jq -r '.workspace.enabled' "$config_file" 2>/dev/null || echo "true")
        WORKSPACE_URL=$(jq -r '.workspace.url' "$config_file" 2>/dev/null || echo "http://localhost:3000")
        WORKSPACE_MULTI_USER_MODE=$(jq -r '.workspace.multi_user_mode' "$config_file" 2>/dev/null || echo "false")
        WORKSPACE_PORT_RANGE_START=$(jq -r '.workspace.port_range_start' "$config_file" 2>/dev/null || echo "3100")
        WORKSPACE_PORT_RANGE_END=$(jq -r '.workspace.port_range_end' "$config_file" 2>/dev/null || echo "3200")
        WORKSPACE_MAX_INSTANCES=$(jq -r '.workspace.max_instances' "$config_file" 2>/dev/null || echo "30")
        WORKSPACE_IDLE_TIMEOUT=$(jq -r '.workspace.idle_timeout_minutes' "$config_file" 2>/dev/null || echo "30")
        WORKSPACE_TOKEN_SECRET=$(jq -r '.workspace.token_secret' "$config_file" 2>/dev/null || echo "")
        OPENCLAW_ENABLED=$(jq -r '.tools.openclaw.enabled' "$config_file" 2>/dev/null || echo "true")
        OPENCLAW_GATEWAY_URL=$(jq -r '.tools.openclaw.gateway_url' "$config_file" 2>/dev/null || echo "http://localhost:18789")
        CLAUDE_ENABLED=$(jq -r '.tools.claude.enabled' "$config_file" 2>/dev/null || echo "true")
        QWEN_ENABLED=$(jq -r '.tools.qwen.enabled' "$config_file" 2>/dev/null || echo "true")
    fi

    # Read from .env file
    if [ -f "$env_file" ]; then
        # Source the .env file to get variables
        RUN_USER=$(grep "^RUN_USER=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "$RUN_USER")
        DB_USER=$(grep "^DB_USER=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "$DB_USER")
        DB_PASSWORD=$(grep "^DB_PASSWORD=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "")
        DB_NAME=$(grep "^DB_NAME=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "$DB_NAME")
        SECRET_KEY=$(grep "^SECRET_KEY=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "")
        UPLOAD_AUTH_KEY=$(grep "^UPLOAD_AUTH_KEY=" "$env_file" 2>/dev/null | cut -d'=' -f2 || echo "")

        # If RUN_USER is empty, try to get from docker-compose.yml
        if [ -z "$RUN_USER" ] && [ -f "$compose_file" ]; then
            # Try to extract from volumes path (e.g., /home/ivyent/.qwen)
            local volume_path=$(grep -E '\.qwen:' "$compose_file" 2>/dev/null | head -1 | sed 's/.*\(-[^:]*:\).*/\1/' | cut -d':' -f1 || echo "")
            if [ -n "$volume_path" ]; then
                RUN_USER=$(basename "$(dirname "$volume_path")" 2>/dev/null || echo "open-ace")
            fi
        fi
    fi

    # Extract ports from URLs
    if [ "$WORKSPACE_ENABLED" = "true" ]; then
        WORKSPACE_PORT=$(echo "$WORKSPACE_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
        [ -z "$WORKSPACE_PORT" ] && WORKSPACE_PORT="3000"
    fi
    if [ "$OPENCLAW_ENABLED" = "true" ]; then
        OPENCLAW_PORT=$(echo "$OPENCLAW_GATEWAY_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
        [ -z "$OPENCLAW_PORT" ] && OPENCLAW_PORT="18789"
    fi

    # Get RUN_USER_UID from existing user
    if [ -n "$RUN_USER" ] && id "$RUN_USER" &>/dev/null; then
        RUN_USER_UID=$(id -u "$RUN_USER")
    fi

    # Check existing SSH config (Issue #1122)
    check_existing_ssh_config

    return 0
}

# Show upgrade summary
show_upgrade_summary() {
    print_header "升级摘要"

    print_info "检测到已存在的 Open ACE 部署"
    print_info "  部署目录: $DEPLOY_DIR"
    echo ""

    echo -e "${YELLOW}当前配置:${NC}"
    echo "  运行用户: $RUN_USER (UID: ${RUN_USER_UID:-auto})"
    echo "  主机名: $HOST_NAME"
    echo "  Web 端口: $WEB_PORT"
    echo "  数据库用户: $DB_USER"
    echo "  数据库名称: $DB_NAME"
    echo ""

    echo -e "${YELLOW}工具配置:${NC}"
    echo "  OpenClaw: $OPENCLAW_ENABLED $( [ "$OPENCLAW_ENABLED" = "true" ] && echo "($OPENCLAW_GATEWAY_URL)" )"
    echo "  Claude: $CLAUDE_ENABLED"
    echo "  Qwen: $QWEN_ENABLED"
    echo "  Workspace: $WORKSPACE_ENABLED $( [ "$WORKSPACE_ENABLED" = "true" ] && echo "($WORKSPACE_URL)" )"
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "    - 多用户模式: 启用"
        echo "    - 端口池: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
        echo "    - 最大实例数: $WORKSPACE_MAX_INSTANCES"
        echo "    - 空闲超时: $WORKSPACE_IDLE_TIMEOUT 分钟"
    fi
    echo ""

    print_info "升级操作:"
    print_info "  1. 构建前端 (npm run build)"
    print_info "  2. 重建 Docker 镜像"
    print_info "  3. 更新 docker-compose.yml 和 sudoers"
    print_info "  4. 保留 config.json 不覆盖"
    print_info "  5. 只重建 open-ace 容器 (PostgreSQL 不重启)"
    echo ""
}

# Execute upgrade deployment
upgrade_deployment() {
    print_header "执行升级"

    # Stop and remove old containers before upgrade
    # This handles both current (open-ace) and legacy (open-ace-web) container names
    # to avoid port conflicts during upgrade
    print_info "清理旧容器..."
    for old_container in "open-ace" "open-ace-web"; do
        if docker ps -a --format '{{.Names}}' 2>/dev/null | grep -q "^${old_container}$"; then
            print_info "停止容器: $old_container"
            docker stop "$old_container" 2>/dev/null || true

            # Data migration for multi-user mode (Issue #1205)
            # Export container /home data to host ./data/home before container removal
            # This is a one-time migration when upgrading from version without volume mapping
            if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
                print_info "检查容器内 home 目录数据..."
                local home_exists=$(docker exec "$old_container" test -d /home 2>/dev/null && echo "yes" || echo "no")

                if [ "$home_exists" = "yes" ]; then
                    # Exclude default system users (Issue #1209 review)
                    local home_files=$(docker exec "$old_container" ls -1 /home 2>/dev/null | grep -v -E "^(open-ace|openace|root)$" || true)

                    if [ -n "$home_files" ]; then
                        print_info "发现容器内 home 目录数据，正在迁移..."
                        mkdir -p "$DEPLOY_DIR/data/home"
                        # Set permissions for /home directory (Issue #1249)
                        chmod 755 "$DEPLOY_DIR/data/home"

                        for user_dir in $home_files; do
                            local target_dir="$DEPLOY_DIR/data/home/$user_dir"

                            # Skip if target already exists to avoid overwriting (Issue #1209 review)
                            if [ -d "$target_dir" ]; then
                                print_warning "  跳过已存在目录: ./data/home/$user_dir"
                            else
                                print_info "  迁移: /home/$user_dir -> ./data/home/$user_dir"
                                docker cp "$old_container:/home/$user_dir" "$DEPLOY_DIR/data/home/" 2>/dev/null || true
                            fi
                        done

                        print_success "home 目录数据迁移完成: ./data/home"
                    else
                        print_info "容器内 /home 目录为空，无需迁移"
                    fi
                fi
            fi

            print_info "删除容器: $old_container"
            docker rm "$old_container" 2>/dev/null || true
        fi
    done
    print_success "旧容器清理完成"

    # Get source directory from script location
    # Script is at scripts/install-central/docker-method/install.sh
    # Source dir is at root (3 levels up)
    local script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
    local SOURCE_DIR=$(cd "$script_dir/../../.." && pwd)

    # Verify source directory
    if [ ! -f "$SOURCE_DIR/Dockerfile" ] || [ ! -d "$SOURCE_DIR/frontend" ]; then
        print_error "无法找到源码目录: $SOURCE_DIR"
        print_info "请确保脚本位于源码目录的 scripts/install-central/docker-method/ 下"
        return 1
    fi

    print_info "源码目录: $SOURCE_DIR"

    # 1. Build frontend in source directory
    print_info "构建前端..."
    cd "$SOURCE_DIR/frontend"
    if [ -f "package.json" ]; then
        # Check if npm is available
        if ! command -v npm &>/dev/null; then
            print_error "npm 未安装，无法构建前端"
            return 1
        fi

        # Install dependencies if needed
        if [ ! -d "node_modules" ]; then
            print_info "安装前端依赖..."
            npm install
        fi

        # Build frontend
        print_info "执行 npm run build..."
        npm run build
        if [ $? -ne 0 ]; then
            print_error "前端构建失败"
            return 1
        fi
        print_success "前端构建完成"
    else
        print_warning "未找到 package.json，跳过前端构建"
    fi

    # 2. Build Docker image in source directory
    cd "$SOURCE_DIR"
    print_info "重建 Docker 镜像..."
    docker build -t "$IMAGE_NAME" --target production .
    if [ $? -ne 0 ]; then
        print_error "Docker 镜像构建失败"
        return 1
    fi
    print_success "Docker 镜像重建完成: $IMAGE_NAME"

    # 3. Update docker-compose.yml and .env
    cd "$DEPLOY_DIR"
    print_info "更新 docker-compose.yml 和 .env..."
    # Call create_docker_compose and create_env_file but they will overwrite, which is fine
    # We need to temporarily set a flag to avoid prompting
    local old_non_interactive="$NON_INTERACTIVE"
    NON_INTERACTIVE=true
    create_docker_compose
    create_env_file
    NON_INTERACTIVE="$old_non_interactive"

    # 3.5. Ensure new directories exist for volume mounts (Issue #1205)
    print_info "创建持久化目录..."
    mkdir -p "$DEPLOY_DIR"/logs
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        mkdir -p "$DEPLOY_DIR"/data/home
        # Set permissions for /home directory (Issue #1249)
        chmod 755 "$DEPLOY_DIR"/data/home
    fi
    print_success "持久化目录创建完成"

    # 4. Update sudoers if multi-user mode is enabled
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        print_info "更新 sudoers 配置..."
        stop_webui_systemd_service
        configure_sudoers
        if [ $? -ne 0 ]; then
            print_warning "Sudoers 配置失败，但继续升级"
        fi
    fi

    # 5. config.json is preserved (not overwritten)

    # 5.5. Grant sequence permissions to DB_USER if needed
    # PostgreSQL sequences have independent owners (pg_class.relowner).
    # When DB_USER is not a superuser, permissions must be granted by a superuser.
    # We query for actual superuser rather than assuming 'postgres' exists.
    print_info "检查数据库序列权限..."

    # Check if DB_USER is a superuser
    local is_superuser=""
    is_superuser=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c \
        "SELECT rolsuper FROM pg_roles WHERE rolname = '$DB_USER';" 2>/dev/null | tr -d '[:space:]')

    if [ "$is_superuser" = "t" ]; then
        print_info "$DB_USER 是超级用户，无需额外授权"
    else
        # Find a superuser to grant permissions
        local superuser=""
        superuser=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c \
            "SELECT rolname FROM pg_roles WHERE rolsuper = 't' LIMIT 1;" 2>/dev/null | tr -d '[:space:]')

        if [ -n "$superuser" ]; then
            print_info "授予序列权限给 $DB_USER（使用超级用户 $superuser）..."
            local grant_output=""
            grant_output=$(docker compose exec -T postgres psql -U "$superuser" -d "$DB_NAME" -c \
                "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO \"$DB_USER\";" 2>&1)

            if echo "$grant_output" | grep -q "GRANT"; then
                print_success "序列权限授予成功"
            else
                print_warning "序列权限授予失败: $grant_output"
                print_info "如登录失败请手动执行:"
                print_info "  docker compose exec postgres psql -U $superuser -d $DB_NAME -c 'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO $DB_USER;'"
            fi
        else
            print_warning "无法找到超级用户，跳过序列权限授权"
            print_info "如登录失败请手动检查数据库权限配置"
        fi
    fi

    # 5.6. Check alembic_version table before recreating container (Issue #1192)
    # This detects older databases that still need the baseline cutover path.
    print_info "检查数据库 migration 状态..."
    local alembic_exists=""
    alembic_exists=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c \
        "SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version';" 2>/dev/null | tr -d '[:space:]')

    if [ "$alembic_exists" = "1" ]; then
        print_success "alembic_version 表存在，migration 系统正常"
        export ALEMBIC_VERSION_EXISTS="yes"
    else
        print_warning "alembic_version 表不存在"
        print_info "数据库可能是旧版本升级，需要在容器重启后执行 baseline cutover 和 Alembic upgrade"
        export ALEMBIC_VERSION_EXISTS="no"
    fi

    # 6. Recreate only open-ace container (PostgreSQL not restarted)
    print_info "重建 open-ace 容器..."
    docker compose up -d --force-recreate open-ace
    if [ $? -ne 0 ]; then
        print_error "容器重建失败"
        return 1
    fi

    # Wait for application to be ready
    print_info "等待应用启动..."
    sleep 3

    local max_attempts=30
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if curl -s "http://localhost:$WEB_PORT/health" | grep -q "healthy"; then
            print_success "应用已就绪"
            break
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done
    echo ""

    if [ $attempt -gt $max_attempts ]; then
        print_warning "应用启动中，请稍后检查状态"
    fi

    # 7. Fix alembic_version if missing (Issue #1192)
    # For legacy databases, docker-entrypoint.sh now runs the baseline cutover
    # helper before Alembic upgrade. We verify that the migration metadata
    # exists after container restart and offer a manual recovery path if needed.
    if [ "$ALEMBIC_VERSION_EXISTS" = "no" ]; then
        print_info "验证 baseline cutover 是否已由 entrypoint 自动处理..."
        local alembic_fixed=""
        alembic_fixed=$(docker compose exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -t -c \
            "SELECT 1 FROM information_schema.tables WHERE table_name = 'alembic_version';" 2>/dev/null | tr -d '[:space:]')

        if [ "$alembic_fixed" = "1" ]; then
            print_success "alembic_version 表已自动创建"
        else
            print_warning "alembic_version 表仍然缺失，尝试手动修复..."
            docker compose exec -T open-ace sh -c \
                "python3 scripts/cutover_alembic_baseline.py && alembic upgrade head" 2>&1 || {
                print_error "baseline cutover / alembic upgrade 失败，请检查日志"
                print_info "可能需要手动执行: docker compose exec open-ace sh -c 'python3 scripts/cutover_alembic_baseline.py && alembic upgrade head'"
            }
        fi
    fi

    return 0
}

# ============================================================================
# Deployment Functions
# ============================================================================

create_directories() {
    print_header "创建目录结构"

    # Display UID info: show specified UID or "auto" for system assignment
    local uid_info="${RUN_USER_UID:-auto}"
    print_info "运行用户: $RUN_USER (UID: $uid_info)"
    print_info "部署目录: $DEPLOY_DIR"

    # Create user if not exists (Issue #1116: UID auto-assignment)
    if ! id "$RUN_USER" &>/dev/null; then
        if [ -n "$RUN_USER_UID" ]; then
            # User specified UID: check for conflict
            if getent passwd "$RUN_USER_UID" >/dev/null 2>&1; then
                print_warning "UID $RUN_USER_UID 已被占用，使用系统自动分配"
                print_info "创建用户: $RUN_USER"
                useradd -r -m -d "/home/$RUN_USER" -s /bin/bash "$RUN_USER"
            else
                print_info "创建用户: $RUN_USER (UID: $RUN_USER_UID)"
                useradd -r -u "$RUN_USER_UID" -m -d "/home/$RUN_USER" -s /bin/bash "$RUN_USER"
            fi
        else
            # No UID specified: let system auto-assign (recommended)
            print_info "创建用户: $RUN_USER"
            useradd -r -m -d "/home/$RUN_USER" -s /bin/bash "$RUN_USER"
        fi
        # Display actual UID after creation
        print_info "实际 UID: $(id -u "$RUN_USER")"
    fi

    local user_home="/home/$RUN_USER"

    # Create deployment config directory
    mkdir -p "$DEPLOY_DIR"/config

    # Create workspace directory for Docker volume mount (Issue #1083)
    # This ensures project files persist across container restarts
    mkdir -p "$DEPLOY_DIR"/workspace
    print_info "  - $DEPLOY_DIR/workspace"

    # Create workspace directory
    mkdir -p "$user_home/workspace"
    print_info "  - $user_home/workspace"

    # Create tool directories based on configuration
    # Warn if directories already exist (may contain user settings)
    if [ "$QWEN_ENABLED" = "true" ]; then
        if [ -d "$user_home/.qwen" ]; then
            print_warning ".qwen 目录已存在: $user_home/.qwen"
            print_info "  该目录可能包含 settings.json 等用户配置，请注意保护"
        else
            mkdir -p "$user_home/.qwen"
        fi
        print_info "  - $user_home/.qwen"
    fi

    if [ "$CLAUDE_ENABLED" = "true" ]; then
        if [ -d "$user_home/.claude" ]; then
            print_warning ".claude 目录已存在: $user_home/.claude"
            print_info "  该目录可能包含 settings.json 等用户配置，请注意保护"
        else
            mkdir -p "$user_home/.claude"
        fi
        print_info "  - $user_home/.claude"
    fi

    if [ "$OPENCLAW_ENABLED" = "true" ]; then
        if [ -d "$user_home/.openclaw" ]; then
            print_warning ".openclaw 目录已存在: $user_home/.openclaw"
            print_info "  该目录可能包含 openclaw.json 等用户配置，请注意保护"
        else
            mkdir -p "$user_home/.openclaw"
        fi
        print_info "  - $user_home/.openclaw"
    fi

    # Set ownership to run user
    chown -R "$RUN_USER:$RUN_USER" "$DEPLOY_DIR"
    chown -R "$RUN_USER:$RUN_USER" "$user_home/workspace"
    [ "$QWEN_ENABLED" = "true" ] && chown -R "$RUN_USER:$RUN_USER" "$user_home/.qwen"
    [ "$CLAUDE_ENABLED" = "true" ] && chown -R "$RUN_USER:$RUN_USER" "$user_home/.claude"
    [ "$OPENCLAW_ENABLED" = "true" ] && chown -R "$RUN_USER:$RUN_USER" "$user_home/.openclaw"

    chmod -R 755 "$DEPLOY_DIR"

    # Create logs directory for Docker volume mount (Issue #1205)
    # This ensures application logs persist across container restarts
    mkdir -p "$DEPLOY_DIR"/logs
    print_info "  - $DEPLOY_DIR/logs (应用日志目录)"

    # Create multi-user home directory for Docker volume mount (Issue #1205)
    # This ensures user home directories persist across container restarts
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        mkdir -p "$DEPLOY_DIR"/data/home
        # Set permissions for /home directory (Issue #1249)
        # /home should be 755 (enterable by all users)
        # Individual /home/<user> directories will be 700 (private, set by useradd -m)
        chmod 755 "$DEPLOY_DIR"/data/home
        print_info "  - $DEPLOY_DIR/data/home (多用户 home 目录, 权限 755)"
    fi

    print_success "目录创建完成"
    print_info "  - $DEPLOY_DIR/config"
    print_info "  - 所有者: $RUN_USER:$RUN_USER"
}

create_config() {
    print_header "创建配置文件"

    local config_file="$DEPLOY_DIR/config/config.json"

    if [ -f "$config_file" ]; then
        print_warning "配置文件已存在: $config_file"
        # 默认不覆盖，保护已有配置（非交互模式下也会保留）
        prompt_yesno "是否覆盖?" "n" overwrite_config
        if [ "$overwrite_config" = "no" ]; then
            print_info "保留现有配置文件"
            return
        fi

        # 备份现有配置文件
        local backup_timestamp=$(date +"%Y%m%d_%H%M%S")
        local backup_file="${config_file}.bak.${backup_timestamp}"
        print_info "备份现有配置文件到: $backup_file"
        if cp "$config_file" "$backup_file"; then
            # 设置备份文件权限为 600（仅 owner 可读写），保护敏感信息
            chmod 600 "$backup_file"
            print_success "配置文件备份成功"
            # 清理超过 7 天的旧备份文件
            find "$DEPLOY_DIR/config" -name "config.json.bak.*" -mtime +7 -delete 2>/dev/null || true
        else
            print_error "配置文件备份失败，停止覆盖操作"
            return 1
        fi
    fi

    # Get server hostname if not set
    if [ -z "$HOST_NAME" ]; then
        HOST_NAME=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "localhost")
    fi
    local server_url="http://${HOST_NAME}:${WEB_PORT}"

    # Get server IP address for URL conversion (more reliable than hostname)
    local server_ip=$(hostname -I 2>/dev/null | awk '{print $1}')
    if [ -z "$server_ip" ]; then
        server_ip=$(ip route get 1 2>/dev/null | awk '{print $7; exit}')
    fi
    if [ -z "$server_ip" ]; then
        server_ip="$HOST_NAME"  # Fallback to hostname
    fi

    # Create config directory
    mkdir -p "$DEPLOY_DIR/config"

    # Convert localhost URLs to server IP for frontend access
    # Note: These URLs are used by the browser (frontend), not the container
    # Browsers cannot resolve host.docker.internal, so we use the actual server IP
    local workspace_url_config="$WORKSPACE_URL"
    local openclaw_url_config="$OPENCLAW_GATEWAY_URL"

    if [ "$WORKSPACE_ENABLED" = "true" ]; then
        # Replace localhost/127.0.0.1 with server IP
        workspace_url_config=$(echo "$WORKSPACE_URL" | sed "s|://localhost:|://$server_ip:|g" | sed "s|://127.0.0.1:|://$server_ip:|g")
        if [ "$workspace_url_config" != "$WORKSPACE_URL" ]; then
            print_info "  - Workspace URL: $workspace_url_config (已转换为服务器 IP)"
        fi
    fi

    if [ "$OPENCLAW_ENABLED" = "true" ]; then
        # Replace localhost/127.0.0.1 with server IP
        openclaw_url_config=$(echo "$OPENCLAW_GATEWAY_URL" | sed "s|://localhost:|://$server_ip:|g" | sed "s|://127.0.0.1:|://$server_ip:|g")
        if [ "$openclaw_url_config" != "$OPENCLAW_GATEWAY_URL" ]; then
            print_info "  - OpenClaw URL: $openclaw_url_config (已转换为服务器 IP)"
        fi
    fi

    # Generate token secret if not provided and multi-user mode is enabled
    local workspace_token_secret="$WORKSPACE_TOKEN_SECRET"
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ] && [ -z "$workspace_token_secret" ]; then
        workspace_token_secret=$(openssl rand -hex 32)
        print_info "  - 生成 Workspace Token Secret: $workspace_token_secret"
    fi

    # Build workspace config
    local workspace_config=""
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        workspace_config=$(cat << EOF
  "workspace": {
    "enabled": $WORKSPACE_ENABLED,
    "url": "$workspace_url_config",
    "multi_user_mode": true,
    "port_range_start": $WORKSPACE_PORT_RANGE_START,
    "port_range_end": $WORKSPACE_PORT_RANGE_END,
    "max_instances": $WORKSPACE_MAX_INSTANCES,
    "idle_timeout_minutes": $WORKSPACE_IDLE_TIMEOUT,
    "token_secret": "$workspace_token_secret"
  }
EOF
)
    else
        workspace_config=$(cat << EOF
  "workspace": {
    "enabled": $WORKSPACE_ENABLED,
    "url": "$workspace_url_config"
  }
EOF
)
    fi

    # Create config file
    cat > "$config_file" << EOF
{
  "host_name": "$HOST_NAME",
  "database": {
    "type": "postgresql",
    "url": "postgresql://$DB_USER:$DB_PASSWORD@postgres:5432/$DB_NAME"
  },
  "server": {
    "upload_auth_key": "$UPLOAD_AUTH_KEY",
    "server_url": "$server_url",
    "web_port": $WEB_PORT,
    "web_host": "0.0.0.0"
  },
$workspace_config,
  "tools": {
    "openclaw": {
      "enabled": $OPENCLAW_ENABLED,
      "token_env": "OPENCLAW_TOKEN",
      "gateway_url": "$openclaw_url_config",
      "hostname": "$HOST_NAME"
    },
    "claude": {
      "enabled": $CLAUDE_ENABLED,
      "hostname": "$HOST_NAME"
    },
    "qwen": {
      "enabled": $QWEN_ENABLED,
      "hostname": "$HOST_NAME"
    }
  },
  "cron": {
    "enabled": true,
    "run_time": "00:30"
  },
  "insights": {
    "model": "glm-5",
    "temperature": 0.3,
    "max_tokens": 4096
  }
}
EOF

    print_success "配置文件创建完成: $config_file"
    print_info "  - 主机名: $HOST_NAME"
    print_info "  - 服务地址: $server_url"
    print_info "  - 数据库: PostgreSQL ($DB_NAME)"
    print_info "  - OpenClaw: $OPENCLAW_ENABLED"
    print_info "  - Claude: $CLAUDE_ENABLED"
    print_info "  - Qwen: $QWEN_ENABLED"
    print_info "  - Workspace: $WORKSPACE_ENABLED"
}

create_docker_compose() {
    print_header "创建 Docker Compose 配置"

    local compose_file="$DEPLOY_DIR/docker-compose.yml"

    # Detect platform for Docker images
    local docker_platform=$(get_docker_platform)
    local arch=$(detect_arch)
    print_info "检测到平台: $docker_platform (架构: $arch)"

    # Build ports section - WEB_PORT and workspace port range for multi-user mode
    local ports_section="      - \"$WEB_PORT:$INTERNAL_WEB_PORT\""


    # Add workspace port range for multi-user mode
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        ports_section="$ports_section
      - "$WORKSPACE_PORT_RANGE_START-$WORKSPACE_PORT_RANGE_END:$WORKSPACE_PORT_RANGE_START-$WORKSPACE_PORT_RANGE_END""
        print_info "  - 多用户模式端口池: $WORKSPACE_PORT_RANGE_START-$WORKSPACE_PORT_RANGE_END"
    fi
    # Build volumes section based on enabled tools
    local volumes_section="      - ./config:/root/.open-ace:ro"

    # Add logs directory mount (Issue #1205)
    volumes_section="$volumes_section
      - ./logs:/app/logs"

    # Handle CLI/home mapping based on multi-user mode (Issue #1205)
    # Multi-user mode: Use unified ./data/home:/home mount for all users
    # Single-user mode: Use per-tool CLI directory mounts (Issue #1192: map to /home/$RUN_USER)
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        volumes_section="$volumes_section
      - ./data/home:/home"
        print_info "  - 映射多用户 home 目录: ./data/home:/home"
    else
        local user_home="/home/$RUN_USER"

        if [ "$QWEN_ENABLED" = "true" ]; then
            # Issue #1192: Map to /home/$RUN_USER instead of /home/open-ace
            # This ensures fetch scripts can find data under /home/$RUN_USER/.qwen/projects
            volumes_section="$volumes_section
      - $user_home/.qwen:/home/$RUN_USER/.qwen"
            print_info "  - 映射 .qwen 目录 ($RUN_USER -> $RUN_USER)"
        fi

        if [ "$CLAUDE_ENABLED" = "true" ]; then
            volumes_section="$volumes_section
      - $user_home/.claude:/home/$RUN_USER/.claude"
            print_info "  - 映射 .claude 目录 ($RUN_USER -> $RUN_USER)"
        fi

        if [ "$OPENCLAW_ENABLED" = "true" ]; then
            volumes_section="$volumes_section
      - $user_home/.openclaw:/home/$RUN_USER/.openclaw"
            print_info "  - 映射 .openclaw 目录 ($RUN_USER -> $RUN_USER)"
        fi
    fi

    # Add workspace volume mount (Issue #1083)
    # This ensures project files persist across container restarts
    volumes_section="$volumes_section
      - ./workspace:/workspace"
    print_info "  - 映射 workspace 目录"

    # SSH key mount for remote host access (Issue #1122)
    if [ "$SSH_ENABLED" = "yes" ]; then
        volumes_section="$volumes_section
      - $SSH_MOUNT_SOURCE:/root/.ssh:ro"
        print_info "  - 映射 SSH 密钥目录: $SSH_MOUNT_SOURCE"
    fi

    # Note: We don't specify 'platform' in docker-compose.yml to allow using locally loaded images
    # The platform detection is just for information purposes
    cat > "$compose_file" << EOF
# Open ACE - Docker Compose Configuration
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Detected Platform: $docker_platform

services:
  open-ace:
    image: $IMAGE_NAME
    container_name: open-ace
    restart: unless-stopped
    ports:
$ports_section
    environment:
      - SECRET_KEY=$SECRET_KEY
      - UPLOAD_AUTH_KEY=$UPLOAD_AUTH_KEY
      - DATABASE_URL=postgresql://$DB_USER:$DB_PASSWORD@postgres:5432/$DB_NAME
      - WORKSPACE_MULTI_USER_MODE=$WORKSPACE_MULTI_USER_MODE
      - WORKSPACE_BASE_DIR=/workspace
      - OPENACE_SYSTEM_ACCOUNT=$RUN_USER
      # Data fetch: container runs as root, use venv Python (Issue #1121)
      - FETCH_USE_SUDO=false
    volumes:
$volumes_section
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - |
          import urllib.request, os
          urllib.request.urlopen('http://localhost:$INTERNAL_WEB_PORT/health')
          db_url = os.environ.get('DATABASE_URL')
          if db_url:
              import psycopg2
              conn = psycopg2.connect(db_url)
              conn.close()
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 30s
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

  postgres:
    image: postgres:15-alpine
    container_name: open-ace-postgres
    restart: unless-stopped
    environment:
      - POSTGRES_USER=$DB_USER
      - POSTGRES_PASSWORD=$DB_PASSWORD
      - POSTGRES_DB=$DB_NAME
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U $DB_USER -d $DB_NAME"]
      interval: 10s
      timeout: 5s
      retries: 5
    logging:
      driver: "json-file"
      options:
        max-size: "10m"
        max-file: "3"

volumes:
  postgres-data:
    driver: local
EOF

    print_success "Docker Compose 配置创建完成"
}

create_env_file() {
    # Create .env file for future reference
    local env_file="$DEPLOY_DIR/.env"

    cat > "$env_file" << EOF
# Open ACE Environment Variables
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Keep this file secure!

RUN_USER=$RUN_USER
RUN_USER_UID=${RUN_USER_UID:-}
DEPLOY_DIR=$DEPLOY_DIR
IMAGE_NAME=$IMAGE_NAME
WEB_PORT=$WEB_PORT
INTERNAL_WEB_PORT=$INTERNAL_WEB_PORT
DB_USER=$DB_USER
DB_PASSWORD=$DB_PASSWORD
DB_NAME=$DB_NAME
DB_FILE=$DB_FILE
SECRET_KEY=$SECRET_KEY
UPLOAD_AUTH_KEY=$UPLOAD_AUTH_KEY
WORKSPACE_ENABLED=$WORKSPACE_ENABLED
WORKSPACE_URL=$WORKSPACE_URL
WORKSPACE_PORT=$WORKSPACE_PORT
WORKSPACE_MULTI_USER_MODE=$WORKSPACE_MULTI_USER_MODE
WORKSPACE_PORT_RANGE_START=$WORKSPACE_PORT_RANGE_START
WORKSPACE_PORT_RANGE_END=$WORKSPACE_PORT_RANGE_END
WORKSPACE_MAX_INSTANCES=$WORKSPACE_MAX_INSTANCES
WORKSPACE_IDLE_TIMEOUT=$WORKSPACE_IDLE_TIMEOUT
OPENCLAW_ENABLED=$OPENCLAW_ENABLED
OPENCLAW_GATEWAY_URL=$OPENCLAW_GATEWAY_URL
OPENCLAW_PORT=$OPENCLAW_PORT
SSH_ENABLED=$SSH_ENABLED
EOF

    chmod 600 "$env_file"
    print_success "环境变量文件创建完成: $env_file"
    print_warning "请妥善保管此文件，包含敏感信息！"
}

start_postgres() {
    print_header "启动 PostgreSQL"

    cd "$DEPLOY_DIR"

    # Check if PostgreSQL image exists locally
    local postgres_image="postgres:15-alpine"
    if ! docker image inspect "$postgres_image" &>/dev/null; then
        print_error "PostgreSQL 镜像不存在: $postgres_image"
        print_info "请先加载镜像文件:"
        print_info "  gunzip -c open-ace-images.tar.gz | docker load"
        print_info "或从有网络的环境拉取镜像后重新打包"
        return 1
    fi
    print_success "PostgreSQL 镜像已存在: $postgres_image"

    # Check if PostgreSQL data volume already exists
    local volume_name="open-ace_postgres-data"
    local existing_volume=$(docker volume ls -q --filter "name=$volume_name" 2>/dev/null || true)

    if [ -n "$existing_volume" ]; then
        print_warning "检测到已存在的 PostgreSQL 数据卷: $volume_name"
        echo ""
        echo "如果之前部署过且数据库密码已更改，需要删除旧数据卷才能使用新密码。"
        echo ""

        # In non-interactive mode, try to use old password from .env
        if [ "$NON_INTERACTIVE" = true ]; then
            local old_password=""
            if [ -f "$DEPLOY_DIR/.env" ]; then
                old_password=$(grep "^DB_PASSWORD=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d'=' -f2 || true)
            fi

            if [ -n "$old_password" ]; then
                print_info "非交互模式: 使用 .env 中的旧密码"
                DB_PASSWORD="$old_password"
            else
                print_warning "非交互模式: 未找到旧密码，删除旧数据卷"
                docker compose down -v 2>/dev/null || true
                docker volume rm "$volume_name" 2>/dev/null || true
                print_success "旧数据卷已删除"
            fi
        else
            # Interactive mode
            while true; do
                echo "选项:"
                echo "  1) 删除旧数据卷并重新创建 (会丢失所有数据)"
                echo "  2) 保留旧数据卷 (输入旧密码)"
                echo "  3) 取消部署"
                echo ""

                prompt_input "请选择" "2" volume_choice

                case "$volume_choice" in
                    1)
                        print_info "停止现有容器..."
                        docker compose down -v 2>/dev/null || true
                        print_info "删除旧数据卷..."
                        docker volume rm "$volume_name" 2>/dev/null || true
                        print_success "旧数据卷已删除"
                        break
                        ;;
                    2)
                        # Try to get old password from .env first
                        local old_password=""
                        if [ -f "$DEPLOY_DIR/.env" ]; then
                            old_password=$(grep "^DB_PASSWORD=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d'=' -f2 || true)
                        fi

                        echo ""
                        if [ -n "$old_password" ]; then
                            print_info "检测到 .env 中的旧密码"
                            prompt_yesno "使用此密码?" "y" use_old_password
                            if [ "$use_old_password" = "yes" ]; then
                                DB_PASSWORD="$old_password"
                                break
                            else
                                old_password=""
                            fi
                        fi

                        if [ -z "$old_password" ]; then
                            echo ""
                            echo "请输入之前部署时使用的数据库密码:"
                            echo "(如果不知道密码，请选择其他选项)"
                            echo ""
                            prompt_input "旧密码" "" old_password
                        fi

                        if [ -z "$old_password" ]; then
                            print_warning "未输入密码，请重新选择"
                            echo ""
                            continue
                        fi

                        # Update password
                        DB_PASSWORD="$old_password"
                        break
                        ;;
                    3)
                        print_info "部署已取消"
                        exit 0
                        ;;
                    *)
                        print_warning "无效选择，请重新输入"
                        echo ""
                        continue
                        ;;
                esac
            done
        fi
        echo ""
    fi

    # Start PostgreSQL only
    docker compose up -d postgres

    print_info "等待 PostgreSQL 启动..."
    sleep 5

    # Wait for PostgreSQL to be ready
    local max_attempts=30
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if docker compose exec -T postgres pg_isready -U "$DB_USER" -d "$DB_NAME" &>/dev/null; then
            print_success "PostgreSQL 已就绪"
            return 0
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done

    print_error "PostgreSQL 启动超时"
    return 1
}

init_auth_database() {
    print_header "初始化认证数据库"

    cd "$DEPLOY_DIR"

    # Wait for application to be ready
    local max_attempts=30
    local attempt=1
    local is_healthy=false
    while [ $attempt -le $max_attempts ]; do
        if curl -s "http://localhost:$WEB_PORT/health" | grep -q "healthy"; then
            is_healthy=true
            break
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done
    echo ""

    if [ "$is_healthy" = false ]; then
        print_warning "应用健康检查超时，尝试初始化..."
    fi

    # Initialize database and create default admin user
    # Issue #1192: Use RUN_USER as system_account (consistent with binary install)
    # This ensures workspace paths match the host user's home directory
    local system_account="${OPENACE_SYSTEM_ACCOUNT:-$RUN_USER}"
    print_info "使用 system_account=$system_account (与宿主机运行用户一致)"
    if docker compose exec -T -e OPENACE_SYSTEM_ACCOUNT=$system_account open-ace python scripts/init_db.py; then
        print_success "数据库初始化完成"
        print_info "默认管理员: admin / admin123 (system_account=$system_account)"
    else
        print_warning "数据库初始化失败，可能已存在用户"
    fi
}

start_application() {
    print_header "启动应用"

    cd "$DEPLOY_DIR"

    docker compose up -d

    print_info "等待应用启动..."
    sleep 3

    # Check if application is healthy
    local max_attempts=30
    local attempt=1
    while [ $attempt -le $max_attempts ]; do
        if curl -s "http://localhost:$WEB_PORT/health" | grep -q "healthy"; then
            print_success "应用已就绪"
            return 0
        fi
        echo -n "."
        sleep 1
        attempt=$((attempt + 1))
    done

    print_warning "应用启动中，请稍后检查状态"
    return 0
}

# ============================================================================
# Backup Functions (Issue #1192)
# ============================================================================

create_backup_scripts() {
    print_header "创建备份脚本"

    local backup_script="$DEPLOY_DIR/backup.sh"
    local restore_script="$DEPLOY_DIR/restore.sh"

    cat > "$backup_script" << 'BACKUP_EOF'
#!/bin/bash
# Open ACE Backup Script
# Backs up PostgreSQL data, config, and workspace

set -e

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$DEPLOY_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
BACKUP_NAME="open-ace-backup-$TIMESTAMP"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Open ACE - Backup${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""

mkdir -p "$BACKUP_DIR/$BACKUP_NAME"

echo -e "${YELLOW}[1/4] 备份 PostgreSQL 数据...${NC}"
DB_USER_VAL=$(grep "^DB_USER=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2)
DB_NAME_VAL=$(grep "^DB_NAME=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2)
if cd "$DEPLOY_DIR" && docker compose exec -T postgres pg_dump -U "$DB_USER_VAL" "$DB_NAME_VAL" > "$BACKUP_DIR/$BACKUP_NAME/database.sql" 2>/dev/null; then
    echo -e "${GREEN}✓ PostgreSQL 数据备份完成${NC}"
else
    echo -e "${RED}✗ PostgreSQL 数据备份失败${NC}"
    echo -e "${YELLOW}  提示: 请确保容器正在运行${NC}"
fi

echo -e "${YELLOW}[2/4] 备份配置文件...${NC}"
if cp -r "$DEPLOY_DIR/config" "$BACKUP_DIR/$BACKUP_NAME/" 2>/dev/null; then
    echo -e "${GREEN}✓ 配置文件备份完成${NC}"
else
    echo -e "${YELLOW}⚠ 配置文件备份失败${NC}"
fi

echo -e "${YELLOW}[3/4] 备份 Workspace 数据...${NC}"
if [ -d "$DEPLOY_DIR/workspace" ] && [ "$(ls -A "$DEPLOY_DIR/workspace" 2>/dev/null)" ]; then
    if tar -czf "$BACKUP_DIR/$BACKUP_NAME/workspace.tar.gz" -C "$DEPLOY_DIR" workspace 2>/dev/null; then
        echo -e "${GREEN}✓ Workspace 数据备份完成${NC}"
    else
        echo -e "${YELLOW}⚠ Workspace 数据备份失败${NC}"
    fi
else
    echo -e "${YELLOW}⚠ Workspace 目录为空，跳过备份${NC}"
fi

echo -e "${YELLOW}[4/4] 备份环境变量文件...${NC}"
if cp "$DEPLOY_DIR/.env" "$BACKUP_DIR/$BACKUP_NAME/.env" 2>/dev/null; then
    echo -e "${GREEN}✓ 环境变量文件备份完成${NC}"
else
    echo -e "${YELLOW}⚠ 环境变量文件备份失败${NC}"
fi

echo ""
echo -e "${GREEN}备份完成！${NC}"
echo "备份位置: $BACKUP_DIR/$BACKUP_NAME"
echo "备份大小: $(du -sh "$BACKUP_DIR/$BACKUP_NAME" | cut -f1)"
echo ""

echo -e "${YELLOW}清理旧备份（保留最近 5 个）...${NC}"
ls -dt "$BACKUP_DIR"/open-ace-backup-* 2>/dev/null | tail -n +6 | xargs rm -rf 2>/dev/null || true
BACKUP_EOF
    chmod +x "$backup_script"
    print_success "备份脚本创建完成: $backup_script"

    cat > "$restore_script" << 'RESTORE_EOF'
#!/bin/bash
# Open ACE Restore Script
# Restores PostgreSQL data, config, and workspace from a backup

set -e

DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="$DEPLOY_DIR/backups"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

if [ ! -d "$BACKUP_DIR" ] || [ -z "$(ls -A "$BACKUP_DIR" 2>/dev/null)" ]; then
    echo -e "${RED}没有找到备份文件${NC}"
    echo -e "${YELLOW}请先运行 backup.sh 创建备份${NC}"
    exit 1
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Open ACE - Restore${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo "可用的备份:"
ls -dt "$BACKUP_DIR"/open-ace-backup-* 2>/dev/null | while read backup; do
    echo "  $(basename "$backup") ($(du -sh "$backup" | cut -f1))"
done
echo ""

LATEST_BACKUP=$(ls -dt "$BACKUP_DIR"/open-ace-backup-* 2>/dev/null | head -1)
echo -e "${YELLOW}请输入要恢复的备份名称（默认: $(basename "$LATEST_BACKUP")）:${NC}"
read -r backup_name
if [ -z "$backup_name" ]; then
    SELECTED_BACKUP="$LATEST_BACKUP"
else
    SELECTED_BACKUP="$BACKUP_DIR/$backup_name"
fi

if [ ! -d "$SELECTED_BACKUP" ]; then
    echo -e "${RED}备份不存在: $SELECTED_BACKUP${NC}"
    exit 1
fi

echo -e "${YELLOW}⚠️  恢复操作将覆盖当前数据！${NC}"
echo -e "${YELLOW}确认恢复备份: $(basename "$SELECTED_BACKUP")? [y/N]:${NC}"
read -r confirm
if [ "$confirm" != "y" ] && [ "$confirm" != "yes" ]; then
    echo "恢复已取消"
    exit 0
fi

DB_USER_VAL=$(grep "^DB_USER=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2)
DB_NAME_VAL=$(grep "^DB_NAME=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d= -f2)

echo -e "${YELLOW}[1/4] 恢复 PostgreSQL 数据...${NC}"
if cd "$DEPLOY_DIR" && docker compose exec -T postgres psql -U "$DB_USER_VAL" "$DB_NAME_VAL" < "$SELECTED_BACKUP/database.sql" 2>/dev/null; then
    echo -e "${GREEN}✓ PostgreSQL 数据恢复完成${NC}"
else
    echo -e "${RED}✗ PostgreSQL 数据恢复失败${NC}"
fi

echo -e "${YELLOW}[2/4] 恢复配置文件...${NC}"
if cp -r "$SELECTED_BACKUP/config" "$DEPLOY_DIR/" 2>/dev/null; then
    echo -e "${GREEN}✓ 配置文件恢复完成${NC}"
fi

echo -e "${YELLOW}[3/4] 恢复 Workspace 数据...${NC}"
if [ -f "$SELECTED_BACKUP/workspace.tar.gz" ]; then
    if tar -xzf "$SELECTED_BACKUP/workspace.tar.gz" -C "$DEPLOY_DIR" 2>/dev/null; then
        echo -e "${GREEN}✓ Workspace 数据恢复完成${NC}"
    fi
fi

echo -e "${YELLOW}[4/4] 恢复环境变量文件...${NC}"
if cp "$SELECTED_BACKUP/.env" "$DEPLOY_DIR/.env" 2>/dev/null; then
    echo -e "${GREEN}✓ 环境变量文件恢复完成${NC}"
fi

echo -e "${YELLOW}重启应用容器...${NC}"
cd "$DEPLOY_DIR" && docker compose restart open-ace

echo ""
echo -e "${GREEN}恢复完成！${NC}"
RESTORE_EOF
    chmod +x "$restore_script"
    print_success "恢复脚本创建完成: $restore_script"
}

show_deployment_info() {
    print_header "部署完成"

    # Get server IP
    local server_ip=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

    echo -e "${GREEN}Open ACE 已成功部署！${NC}"
    echo ""
    echo "访问地址:"
    echo -e "  ${BLUE}http://$server_ip:$WEB_PORT${NC}"
    echo ""
    echo "默认登录凭据:"
    echo -e "  ${YELLOW}用户名: admin${NC}"
    echo -e "  ${YELLOW}密码: admin123${NC}"
    echo -e "  ${RED}请登录后立即修改密码！${NC}"
    echo ""
    echo "管理命令:"
    echo "  cd $DEPLOY_DIR"
    echo "  docker compose ps          # 查看状态"
    echo "  docker compose logs -f     # 查看日志"
    echo "  docker compose restart     # 重启服务"
    echo "  docker compose down        # 停止服务"
    echo ""
    echo "数据库信息:"
    echo "  类型: PostgreSQL (容器内)"
    echo "  用户: $DB_USER"
    echo "  数据库: $DB_NAME"
    echo ""
    echo "运行用户:"
    echo "  用户: $RUN_USER (UID: ${RUN_USER_UID:-auto})"
    echo "  家目录: /home/$RUN_USER"
    echo "  部署目录: $DEPLOY_DIR"
    echo ""
    echo "配置文件:"
    echo "  $DEPLOY_DIR/config/config.json"
    echo "  $DEPLOY_DIR/.env"
    echo ""

    # Tool configuration hints
    local tool_config_needed=false
    if [ "$QWEN_ENABLED" = "true" ] || [ "$CLAUDE_ENABLED" = "true" ] || [ "$OPENCLAW_ENABLED" = "true" ]; then
        tool_config_needed=true
    fi

    if [ "$tool_config_needed" = true ]; then
        echo -e "${YELLOW}工具配置 (重要):${NC}"
        echo "  请在运行用户家目录下正确设置配置文件:"
        echo ""
        if [ "$QWEN_ENABLED" = "true" ]; then
            echo "  Qwen:"
            echo "    目录: ~$RUN_USER/.qwen/"
            echo "    配置: settings.json (包含 API key 等设置)"
            echo ""
        fi
        if [ "$CLAUDE_ENABLED" = "true" ]; then
            echo "  Claude:"
            echo "    目录: ~$RUN_USER/.claude/"
            echo "    配置: settings.json (包含 API key 等设置)"
            echo ""
        fi
        if [ "$OPENCLAW_ENABLED" = "true" ]; then
            echo "  OpenClaw:"
            echo "    目录: ~$RUN_USER/.openclaw/"
            echo "    配置: openclaw.json (包含 token 等设置)"
            echo ""
        fi
    fi

    echo "防火墙:"
    echo "  端口 $WEB_PORT 已开放"
    if [ "$OPENCLAW_ENABLED" = "true" ] && [ -n "$OPENCLAW_PORT" ]; then
        echo "  端口 $OPENCLAW_PORT 已开放 (OpenClaw)"
    fi
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "  Workspace 端口范围: $WORKSPACE_PORT_RANGE_START-$WORKSPACE_PORT_RANGE_END (多用户模式)"
    fi
    echo "  如需开放其他端口，请手动配置防火墙"
    echo ""
    print_warning "请妥善保管 $DEPLOY_DIR/.env 文件中的敏感信息！"
}

# Check for existing config files and warn user before deployment
check_existing_config() {
    local config_file="$DEPLOY_DIR/config/config.json"
    local user_home="/home/$RUN_USER"
    local qwen_settings="$user_home/.qwen/settings.json"
    local claude_settings="$user_home/.claude/settings.json"
    local openclaw_settings="$user_home/.openclaw/openclaw.json"
    local has_existing_config=false

    if [ -f "$config_file" ]; then
        print_warning "检测到已存在的配置文件: $config_file"
        print_info "  重新运行安装脚本时，默认不会覆盖已有配置"
        has_existing_config=true
    fi

    if [ "$QWEN_ENABLED" = "true" ] && [ -f "$qwen_settings" ]; then
        print_warning "检测到已存在的 Qwen 配置: $qwen_settings"
        print_info "  该文件包含 API key 等敏感信息，请注意保护"
        has_existing_config=true
    fi

    if [ "$CLAUDE_ENABLED" = "true" ] && [ -f "$claude_settings" ]; then
        print_warning "检测到已存在的 Claude 配置: $claude_settings"
        has_existing_config=true
    fi

    if [ "$OPENCLAW_ENABLED" = "true" ] && [ -f "$openclaw_settings" ]; then
        print_warning "检测到已存在的 OpenClaw 配置: $openclaw_settings"
        has_existing_config=true
    fi

    if [ "$has_existing_config" = true ]; then
        echo ""
        print_info "提示: 如需覆盖配置，请在后续步骤中明确选择覆盖选项"
        echo ""
    fi
}

# ============================================================================
# Main
# ============================================================================

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --non-interactive|-n)
            NON_INTERACTIVE=true
            shift
            ;;
        --deploy-dir)
            DEPLOY_DIR="$2"
            shift 2
            ;;
        --image)
            IMAGE_NAME="$2"
            shift 2
            ;;
        --port)
            WEB_PORT="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            ;;
        *)
            print_error "未知选项: $1"
            echo "运行 '$0 --help' 查看帮助"
            exit 1
            ;;
    esac
done

# Main execution
print_header "Open ACE - 快速部署"

# Check for existing config files and warn user
check_existing_config() {
    local config_file="$DEPLOY_DIR/config/config.json"
    local qwen_settings="/home/$RUN_USER/.qwen/settings.json"
    local has_existing_config=false

    if [ -f "$config_file" ]; then
        print_warning "检测到已存在的配置文件: $config_file"
        print_info "  重新运行安装脚本时，默认不会覆盖已有配置"
        has_existing_config=true
    fi

    if [ -f "$qwen_settings" ]; then
        print_warning "检测到已存在的 Qwen 配置: $qwen_settings"
        has_existing_config=true
    fi

    if [ "$has_existing_config" = true ]; then
        echo ""
        print_info "提示: 如需覆盖配置，请在后续步骤中明确选择覆盖选项"
        echo ""
    fi
}

# Check prerequisites
check_prerequisites

# Detect existing deployment and offer upgrade mode
if [ "$NON_INTERACTIVE" = false ]; then
    if detect_existing_deployment; then
        read_existing_config
        show_upgrade_summary

        echo "请选择操作:"
        echo "  1) 升级 (使用当前配置，保留数据库和配置文件)"
        echo "  2) 重新配置 (重新填写所有参数)"
        echo "  3) 取消"
        echo ""

        prompt_input "请选择" "1" mode_choice

        case "$mode_choice" in
            1|"")
                print_info "开始升级..."
                upgrade_deployment
                if [ $? -eq 0 ]; then
                    print_header "升级完成"
                    print_success "Open ACE 已成功升级！"
                    print_info "访问地址: http://localhost:$WEB_PORT"
                    print_info "管理命令:"
                    print_info "  cd $DEPLOY_DIR && docker compose ps"
                    print_info "  cd $DEPLOY_DIR && docker compose logs -f"
                else
                    print_error "升级失败，请检查日志"
                fi
                exit 0
                ;;
            2)
                print_info "进入重新配置模式..."
                # Continue with normal installation flow
                ;;
            3)
                print_info "操作已取消"
                exit 0
                ;;
            *)
                print_warning "无效选择，进入重新配置模式..."
                ;;
        esac
    fi
fi

# Check for existing configs after RUN_USER and DEPLOY_DIR are set
check_existing_config

# Interactive configuration
if [ "$NON_INTERACTIVE" = false ]; then
    echo -e "${YELLOW}配置部署参数 (回车使用默认值)${NC}"
    echo ""

    # Basic settings
    echo -e "${BLUE}=== 基本设置 ===${NC}"
    prompt_input "运行用户" "$RUN_USER" RUN_USER
    # Update defaults based on RUN_USER
    DEPLOY_DIR="/home/$RUN_USER/open-ace"
    DB_USER="$RUN_USER"
    prompt_input "部署目录" "$DEPLOY_DIR" DEPLOY_DIR
    prompt_input "Web 端口" "$WEB_PORT" WEB_PORT

    # Host name
    default_hostname=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "localhost")
    prompt_input "主机名 (用于配置文件)" "$default_hostname" HOST_NAME
    [ -z "$HOST_NAME" ] && HOST_NAME="$default_hostname"

    echo ""
    echo -e "${BLUE}=== 数据库设置 ===${NC}"
    prompt_input "数据库用户" "$DB_USER" DB_USER
    prompt_input "数据库名称" "$DB_NAME" DB_NAME

    # SSH configuration (Issue #1122)
    prompt_ssh_config

    echo ""
    echo -e "${BLUE}=== 工具配置 ===${NC}"
    prompt_yesno "启用 OpenClaw 工具?" "y" enable_openclaw
    OPENCLAW_ENABLED=$([ "$enable_openclaw" = "yes" ] && echo "true" || echo "false")
    if [ "$OPENCLAW_ENABLED" = "true" ]; then
        prompt_input "OpenClaw 网关地址" "$OPENCLAW_GATEWAY_URL" OPENCLAW_GATEWAY_URL
        # Extract port from URL (e.g., http://localhost:18789 -> 18789)
        OPENCLAW_PORT=$(echo "$OPENCLAW_GATEWAY_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
        if [ -z "$OPENCLAW_PORT" ]; then
            OPENCLAW_PORT="18789"
        fi
    fi

    prompt_yesno "启用 Claude 工具?" "y" enable_claude
    CLAUDE_ENABLED=$([ "$enable_claude" = "yes" ] && echo "true" || echo "false")

    prompt_yesno "启用 Qwen 工具?" "y" enable_qwen
    QWEN_ENABLED=$([ "$enable_qwen" = "yes" ] && echo "true" || echo "false")

    echo ""
    echo -e "${BLUE}=== Workspace 配置 ===${NC}"
    prompt_yesno "启用 Workspace?" "y" enable_workspace
    WORKSPACE_ENABLED=$([ "$enable_workspace" = "yes" ] && echo "true" || echo "false")
    if [ "$WORKSPACE_ENABLED" = "true" ]; then
        prompt_input "Workspace URL" "$WORKSPACE_URL" WORKSPACE_URL
        # Extract port from URL (e.g., http://localhost:3000 -> 3000)
        WORKSPACE_PORT=$(echo "$WORKSPACE_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
        if [ -z "$WORKSPACE_PORT" ]; then
            # Default port if not specified in URL
            WORKSPACE_PORT="3000"
        fi

        # Multi-user mode configuration
        echo ""
        echo -e "${BLUE}=== 多用户模式配置 ===${NC}"
        echo -e "${YELLOW}多用户模式会为每个用户启动独立的 qwen-code-webui 进程${NC}"
        echo -e "${YELLOW}需要配置 sudo 和安装 qwen-code-webui，详见部署文档${NC}"
        prompt_yesno "启用多用户模式?" "y" enable_multi_user
        WORKSPACE_MULTI_USER_MODE=$([ "$enable_multi_user" = "yes" ] && echo "true" || echo "false")
        if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
            prompt_input "端口池起始端口" "$WORKSPACE_PORT_RANGE_START" WORKSPACE_PORT_RANGE_START
            prompt_input "端口池结束端口" "$WORKSPACE_PORT_RANGE_END" WORKSPACE_PORT_RANGE_END
            prompt_input "最大实例数" "$WORKSPACE_MAX_INSTANCES" WORKSPACE_MAX_INSTANCES
            prompt_input "空闲超时时间(分钟)" "$WORKSPACE_IDLE_TIMEOUT" WORKSPACE_IDLE_TIMEOUT
            # Generate token secret automatically
            WORKSPACE_TOKEN_SECRET=$(openssl rand -hex 32)
            print_info "自动生成 Token Secret: $WORKSPACE_TOKEN_SECRET"
        fi
    fi

    echo ""
fi

# Extract ports from URLs if not already set
if [ "$WORKSPACE_ENABLED" = "true" ] && [ -z "$WORKSPACE_PORT" ]; then
    WORKSPACE_PORT=$(echo "$WORKSPACE_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
    [ -z "$WORKSPACE_PORT" ] && WORKSPACE_PORT="3000"
fi

if [ "$OPENCLAW_ENABLED" = "true" ] && [ -z "$OPENCLAW_PORT" ]; then
    OPENCLAW_PORT=$(echo "$OPENCLAW_GATEWAY_URL" | sed -n 's/.*:\([0-9]*\).*/\1/p')
    [ -z "$OPENCLAW_PORT" ] && OPENCLAW_PORT="18789"
fi

# Confirm deployment
echo -e "${YELLOW}部署配置:${NC}"
echo "  运行用户: $RUN_USER (UID: ${RUN_USER_UID:-auto})"
echo "  部署目录: $DEPLOY_DIR"
echo "  Docker 镜像: $IMAGE_NAME"
echo "  主机名: $HOST_NAME"
echo "  Web 端口: $WEB_PORT"
echo "  数据库用户: $DB_USER"
echo "  数据库名称: $DB_NAME"
echo ""
echo -e "${YELLOW}工具配置:${NC}"
echo "  OpenClaw: $OPENCLAW_ENABLED $( [ "$OPENCLAW_ENABLED" = "true" ] && echo "($OPENCLAW_GATEWAY_URL)" )"
echo "  Claude: $CLAUDE_ENABLED"
echo "  Qwen: $QWEN_ENABLED"
echo "  Workspace: $WORKSPACE_ENABLED $( [ "$WORKSPACE_ENABLED" = "true" ] && echo "($WORKSPACE_URL)" )"
if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
    echo "    - 多用户模式: 启用"
    echo "    - 端口池: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
    echo "    - 最大实例数: $WORKSPACE_MAX_INSTANCES"
    echo "    - 空闲超时: $WORKSPACE_IDLE_TIMEOUT 分钟"
fi
echo ""

if [ "$NON_INTERACTIVE" = false ]; then
    prompt_yesno "确认部署?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "部署已取消"
        exit 0
    fi
fi

# Configure sudoers for multi-user workspace mode
if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
    # Stop existing qwen-code-webui systemd service first
    stop_webui_systemd_service
    configure_sudoers
    if [ $? -ne 0 ]; then
        print_warning "Sudoers 配置失败，多用户模式可能无法正常工作"
        print_info "请手动配置后重试，或使用单用户模式"
    fi
fi

# Execute deployment
# Check for existing config files and warn user before deployment
check_existing_config
create_directories

# Check for existing PostgreSQL data volume BEFORE creating config files
# This is important to preserve the old password if upgrading
VOLUME_NAME="open-ace_postgres-data"
EXISTING_VOLUME=$(docker volume ls -q --filter "name=$VOLUME_NAME" 2>/dev/null || true)
OLD_DB_PASSWORD=""

if [ -n "$EXISTING_VOLUME" ] && [ -f "$DEPLOY_DIR/.env" ]; then
    # Extract old password from existing .env before we overwrite it
    OLD_DB_PASSWORD=$(grep "^DB_PASSWORD=" "$DEPLOY_DIR/.env" 2>/dev/null | cut -d'=' -f2 || true)
    if [ -n "$OLD_DB_PASSWORD" ]; then
        print_info "检测到已存在的 PostgreSQL 数据卷，保存旧密码以便后续使用"
        # Keep the old password for PostgreSQL connection
        DB_PASSWORD="$OLD_DB_PASSWORD"
    fi
fi

create_config
create_docker_compose
create_env_file
configure_firewall "$WEB_PORT"
if [ "$OPENCLAW_ENABLED" = "true" ] && [ -n "$OPENCLAW_PORT" ]; then
    configure_firewall "$OPENCLAW_PORT"
fi
# Open firewall for multi-user workspace port range
if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
    print_info "配置多用户模式端口范围防火墙: $WORKSPACE_PORT_RANGE_START-$WORKSPACE_PORT_RANGE_END"
    configure_firewall_range "$WORKSPACE_PORT_RANGE_START" "$WORKSPACE_PORT_RANGE_END"
fi
start_postgres
start_application
init_auth_database
create_backup_scripts
show_deployment_info
