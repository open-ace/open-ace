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
RUN_USER_UID="${RUN_USER_UID:-999}"
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

# Config defaults (can be overridden by environment variables)
HOST_NAME="${HOST_NAME:-}"
WORKSPACE_ENABLED="${WORKSPACE_ENABLED:-true}"
WORKSPACE_URL="${WORKSPACE_URL:-http://localhost:3000}"
WORKSPACE_PORT="${WORKSPACE_PORT:-}"
# Multi-user workspace mode defaults
WORKSPACE_MULTI_USER_MODE="${WORKSPACE_MULTI_USER_MODE:-true}"
WORKSPACE_PORT_RANGE_START="${WORKSPACE_PORT_RANGE_START:-3100}"
WORKSPACE_PORT_RANGE_END="${WORKSPACE_PORT_RANGE_END:-3200}"
WORKSPACE_MAX_INSTANCES="${WORKSPACE_MAX_INSTANCES:-20}"
WORKSPACE_IDLE_TIMEOUT="${WORKSPACE_IDLE_TIMEOUT:-30}"
WORKSPACE_TOKEN_SECRET="${WORKSPACE_TOKEN_SECRET:-}"
OPENCLAW_ENABLED="${OPENCLAW_ENABLED:-true}"
OPENCLAW_GATEWAY_URL="${OPENCLAW_GATEWAY_URL:-http://localhost:18789}"
OPENCLAW_PORT="${OPENCLAW_PORT:-}"
CLAUDE_ENABLED="${CLAUDE_ENABLED:-true}"
QWEN_ENABLED="${QWEN_ENABLED:-true}"

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
    local sudoers_content="# Open ACE WebUI - Multi-user mode sudo configuration
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Allows the service account to run qwen-code-webui as other users
# and perform file system operations as other users

$RUN_USER ALL=(ALL) NOPASSWD: $webui_path *
$RUN_USER ALL=(ALL) NOPASSWD: /usr/bin/test, /usr/bin/ls, /usr/bin/cat, /usr/bin/stat, /usr/bin/mkdir

# Preserve environment variables for sudo env_keep passing
Defaults env_keep += \"OPENAI_API_KEY OPENAI_BASE_URL BAILIAN_CODING_PLAN_API_KEY ANTHROPIC_API_KEY ANTHROPIC_BASE_URL GEMINI_API_KEY GEMINI_BASE_URL OPENCLAW_TOKEN OPENCLAW_GATEWAY_URL OPENACE_LOG_DIR SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS PATH\"
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
    echo "  RUN_USER_UID         UID for the run user (default: 999)"
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

    # Try official Docker source first, fallback to Aliyun mirror
    print_info "添加 Docker GPG 密钥..."
    if ! curl -fsSL https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]')/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg 2>/dev/null; then
        print_warning "官方源连接失败，尝试使用阿里云镜像..."
        curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]')/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg

        if [ $? -ne 0 ]; then
            print_error "无法添加 Docker GPG 密钥"
            print_info "请检查网络连接或手动安装 Docker"
            return 1
        fi

        # Use Aliyun mirror for repository
        print_info "添加 Docker 软件源 (阿里云镜像)..."
        echo \
            "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://mirrors.aliyun.com/docker-ce/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]') \
            $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    else
        # Add Docker repository (official)
        print_info "添加 Docker 软件源..."
        echo \
            "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/$(lsb_release -is | tr '[:upper:]' '[:lower:]') \
            $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
    fi

    # Install Docker
    print_info "安装 Docker..."
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

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

    # Try to add Docker repository
    print_info "添加 Docker 软件源..."
    if ! sudo yum-config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo 2>/dev/null; then
        print_warning "官方源添加失败，尝试使用阿里云镜像..."

        # Use Aliyun mirror as fallback
        sudo yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo

        if [ $? -ne 0 ]; then
            print_error "无法添加 Docker 软件源"
            print_info "请检查网络连接或手动安装 Docker"
            return 1
        fi
    fi

    # Install Docker (with --nogpgcheck in case of SSL issues)
    print_info "安装 Docker..."
    if ! sudo yum install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
        print_warning "安装失败，尝试跳过 GPG 检查..."
        sudo yum install -y --nogpgcheck docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
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

    # Try to add Docker repository
    print_info "添加 Docker 软件源..."
    if ! sudo dnf config-manager --add-repo https://download.docker.com/linux/fedora/docker-ce.repo 2>/dev/null; then
        print_warning "官方源添加失败，尝试使用阿里云镜像..."
        sudo dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/fedora/docker-ce.repo

        if [ $? -ne 0 ]; then
            print_error "无法添加 Docker 软件源"
            print_info "请检查网络连接或手动安装 Docker"
            return 1
        fi
    fi

    # Install Docker
    print_info "安装 Docker..."
    if ! sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin; then
        print_warning "安装失败，尝试跳过 GPG 检查..."
        sudo dnf install -y --nogpgcheck docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
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
# Deployment Functions
# ============================================================================

create_directories() {
    print_header "创建目录结构"

    print_info "运行用户: $RUN_USER (UID: ${RUN_USER_UID:-999})"
    print_info "部署目录: $DEPLOY_DIR"

    # Create user if not exists
    if ! id "$RUN_USER" &>/dev/null; then
        print_info "创建用户: $RUN_USER"
        useradd -r -u "${RUN_USER_UID:-999}" -m -d "/home/$RUN_USER" -s /bin/bash "$RUN_USER"
    fi

    local user_home="/home/$RUN_USER"

    # Create deployment config directory
    mkdir -p "$DEPLOY_DIR"/config

    # Create workspace directory
    mkdir -p "$user_home/workspace"
    print_info "  - $user_home/workspace"

    # Create tool directories based on configuration
    if [ "$QWEN_ENABLED" = "true" ]; then
        mkdir -p "$user_home/.qwen"
        print_info "  - $user_home/.qwen"
    fi

    if [ "$CLAUDE_ENABLED" = "true" ]; then
        mkdir -p "$user_home/.claude"
        print_info "  - $user_home/.claude"
    fi

    if [ "$OPENCLAW_ENABLED" = "true" ]; then
        mkdir -p "$user_home/.openclaw"
        print_info "  - $user_home/.openclaw"
    fi

    # Set ownership to run user
    chown -R "$RUN_USER:$RUN_USER" "$DEPLOY_DIR"
    chown -R "$RUN_USER:$RUN_USER" "$user_home/workspace"
    [ "$QWEN_ENABLED" = "true" ] && chown -R "$RUN_USER:$RUN_USER" "$user_home/.qwen"
    [ "$CLAUDE_ENABLED" = "true" ] && chown -R "$RUN_USER:$RUN_USER" "$user_home/.claude"
    [ "$OPENCLAW_ENABLED" = "true" ] && chown -R "$RUN_USER:$RUN_USER" "$user_home/.openclaw"

    chmod -R 755 "$DEPLOY_DIR"

    print_success "目录创建完成"
    print_info "  - $DEPLOY_DIR/config"
    print_info "  - 所有者: $RUN_USER:$RUN_USER"
}

create_config() {
    print_header "创建配置文件"

    local config_file="$DEPLOY_DIR/config/config.json"

    if [ -f "$config_file" ]; then
        print_warning "配置文件已存在: $config_file"
        prompt_yesno "是否覆盖?" "y" overwrite_config
        if [ "$overwrite_config" = "no" ]; then
            print_info "保留现有配置文件"
            return
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
  "auth": {
    "auth_type": "openai",
    "env": {
      "OPENAI_API_KEY": "<YOUR_API_KEY>",
      "OPENAI_BASE_URL": "https://api.openai.com/v1"
    }
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
    local user_home="/home/$RUN_USER"

    if [ "$QWEN_ENABLED" = "true" ]; then
        volumes_section="$volumes_section
      - $user_home/.qwen:/home/open-ace/.qwen"
        print_info "  - 映射 .qwen 目录"
    fi

    if [ "$CLAUDE_ENABLED" = "true" ]; then
        volumes_section="$volumes_section
      - $user_home/.claude:/home/open-ace/.claude"
        print_info "  - 映射 .claude 目录"
    fi

    if [ "$OPENCLAW_ENABLED" = "true" ]; then
        volumes_section="$volumes_section
      - $user_home/.openclaw:/home/open-ace/.openclaw"
        print_info "  - 映射 .openclaw 目录"
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
    volumes:
$volumes_section
    depends_on:
      postgres:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:$INTERNAL_WEB_PORT/health')"]
      interval: 30s
      timeout: 10s
      retries: 3
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
RUN_USER_UID=${RUN_USER_UID:-999}
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
    if docker compose exec -T -e OPENACE_SYSTEM_ACCOUNT=openace open-ace python scripts/init_db.py; then
        print_success "数据库初始化完成"
        print_info "默认管理员: admin / admin123 (system_account=openace)"
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
    echo "  用户: $RUN_USER (UID: ${RUN_USER_UID:-999})"
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
echo "  运行用户: $RUN_USER (UID: ${RUN_USER_UID:-999})"
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
show_deployment_info
