#!/bin/bash
#
# Open ACE - AI Computing Explorer - Installation Script
#
# This script installs or upgrades Open ACE.
# Supports both local installation and remote deployment via SSH.
#
# Usage:
#   ./install.sh                      # Interactive mode
#   ./install.sh --config install.conf  # Use config file
#   ./install.sh --help               # Show help
#

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# SOURCE_DIR should be the package root (where cli.py, web.py, etc. are located)
# Package structure: package_root/scripts/install-central/package-method/install.sh
# So we need to go up 3 levels from SCRIPT_DIR to reach package_root
# SCRIPT_DIR = scripts/install-central/package-method
# LEVEL 1 up = scripts/install-central
# LEVEL 2 up = scripts
# LEVEL 3 up = package_root (where web.py, cli.py are)
SOURCE_DIR="$(cd "$SCRIPT_DIR/../../.." && pwd)"

# Validate SOURCE_DIR - must contain web.py or cli.py
validate_source_dir() {
    if [ ! -f "$SOURCE_DIR/web.py" ] && [ ! -f "$SOURCE_DIR/cli.py" ]; then
        print_error "SOURCE_DIR is invalid: $SOURCE_DIR"
        print_error "Expected to find web.py or cli.py in package root"
        print_info "Please ensure you're running install.sh from a valid Open ACE package"
        print_info "Current SCRIPT_DIR: $SCRIPT_DIR"
        exit 1
    fi
    print_info "Source directory validated: $SOURCE_DIR"
}

# Default values
CONFIG_FILE=""
INSTALL_MODE=""  # "local" or "deploy"

# Deployment settings (for both local and deploy modes)
DEPLOY_HOST=""        # Empty for local mode, required for deploy mode
DEPLOY_USER=""        # Will be set after checking for openace user
DEPLOY_PATH=""        # Will be set based on DEPLOY_USER

# ============================================================================
# User Detection and Creation
# ============================================================================

# Check if openace user exists on the system
check_openace_user_exists() {
    if id "openace" &>/dev/null; then
        return 0  # User exists
    else
        return 1  # User does not exist
    fi
}

# Check if openace user exists on remote system
check_openace_user_exists_remote() {
    local remote="$1"
    if ssh "$remote" "id openace &>/dev/null"; then
        return 0  # User exists
    else
        return 1  # User does not exist
    fi
}

# Create openace user on local system
create_openace_user() {
    print_info "Creating openace system user..."

    # Check if running as root
    if [ "$EUID" -ne 0 ]; then
        print_warning "Root privileges required to create openace user"
        print_info "Please run: sudo $0"
        return 1
    fi

    # Detect OS and create user accordingly
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        # Check if the user already exists in local directory
        if dscl . list /Users | grep -q "^openace"; then
            print_success "openace user already exists"
            return 0
        fi

        # Create user on macOS
        local last_uid=$(dscl . list /Users UniqueID | sort -nr | head -1 | awk '{print $2}')
        local new_uid=$((last_uid + 1))

        # Create the user
        dscl . -create /Users/openace
        dscl . -create /Users/openace UserShell /bin/bash
        dscl . -create /Users/openace RealName "Open ACE Service"
        dscl . -create /Users/openace UniqueID "$new_uid"
        dscl . -create /Users/openace PrimaryGroupID 20  # Staff group

        # Create home directory
        createhomedir -c -u openace > /dev/null 2>&1 || true

        print_success "Created openace user (UID: $new_uid)"
        print_info "Home directory: /Users/openace"

    elif [[ "$OSTYPE" == "linux-gnu"* ]]; then
        # Linux
        if command -v useradd &>/dev/null; then
            # Create system user with home directory
            useradd -r -m -s /bin/bash openace || {
                # If -r fails (some systems don't support it), try without
                useradd -m -s /bin/bash openace
            }
            print_success "Created openace system user"
            print_info "Home directory: /home/openace"
        elif command -v adduser &>/dev/null; then
            # Debian-style adduser
            adduser --system --home /home/openace --shell /bin/bash openace || {
                # If --system fails, try regular adduser
                adduser --disabled-password --gecos "Open ACE Service" openace
            }
            print_success "Created openace system user"
        else
            print_error "Cannot create user: useradd/adduser not found"
            return 1
        fi
    else
        print_error "Unsupported OS: $OSTYPE"
        print_info "Please create the openace user manually"
        return 1
    fi

    return 0
}

# Create openace user on remote system
create_openace_user_remote() {
    local remote="$1"

    print_info "Creating openace system user on remote..."

    # Detect remote OS and create user
    local os_type=$(ssh "$remote" "echo \$OSTYPE")

    ssh "$remote" "
        # Check if already exists
        if id openace &>/dev/null; then
            echo 'User openace already exists'
            exit 0
        fi

        # Check for sudo
        if ! sudo -n true 2>/dev/null; then
            echo 'SUDO_REQUIRED'
            exit 1
        fi

        # Create user based on OS
        if command -v useradd &>/dev/null; then
            sudo useradd -r -m -s /bin/bash openace 2>/dev/null || \
            sudo useradd -m -s /bin/bash openace
            echo 'USER_CREATED'
        elif command -v adduser &>/dev/null; then
            sudo adduser --system --home /home/openace --shell /bin/bash openace 2>/dev/null || \
            sudo adduser --disabled-password --gecos 'Open ACE Service' openace
            echo 'USER_CREATED'
        else
            echo 'USERADD_NOT_FOUND'
            exit 1
        fi
    "

    local result=$(ssh "$remote" "
        if id openace &>/dev/null; then
            echo 'USER_EXISTS'
        elif sudo -n true 2>/dev/null; then
            echo 'SUDO_OK'
        else
            echo 'SUDO_REQUIRED'
        fi
    ")

    case "$result" in
        USER_EXISTS)
            print_success "openace user exists on remote"
            return 0
            ;;
        SUDO_OK)
            # Try to create again
            ssh "$remote" "
                if ! id openace &>/dev/null && sudo -n true; then
                    if command -v useradd &>/dev/null; then
                        sudo useradd -r -m -s /bin/bash openace 2>/dev/null || sudo useradd -m -s /bin/bash openace
                    elif command -v adduser &>/dev/null; then
                        sudo adduser --system --home /home/openace --shell /bin/bash openace 2>/dev/null || sudo adduser --disabled-password --gecos 'Open ACE Service' openace
                    fi
                fi
            "
            if ssh "$remote" "id openace &>/dev/null"; then
                print_success "Created openace user on remote"
                return 0
            else
                print_error "Failed to create openace user on remote"
                return 1
            fi
            ;;
        SUDO_REQUIRED)
            print_warning "Sudo required on remote to create openace user"
            print_info "Please run on remote: sudo useradd -r -m -s /bin/bash openace"
            return 1
            ;;
    esac
}

# Initialize deployment user based on openace user availability
init_deploy_user() {
    local is_local="$1"  # "true" for local, "false" for remote

    # Determine default home directory based on OS
    local openace_home
    if [[ "$OSTYPE" == "darwin"* ]]; then
        openace_home="/Users/openace"
    else
        openace_home="/home/openace"
    fi

    if [ "$is_local" = "true" ]; then
        # Local installation
        if check_openace_user_exists; then
            DEPLOY_USER="openace"
            DEPLOY_PATH="$openace_home"
            print_info "Found openace user, using as deployment user"
        else
            # Check if running as root - can create user
            if [ "$EUID" -eq 0 ]; then
                print_info "Running as root, will create openace user"
                if create_openace_user; then
                    DEPLOY_USER="openace"
                    DEPLOY_PATH="$openace_home"
                else
                    print_warning "Failed to create openace user, using current user"
                    DEPLOY_USER="${USER}"
                    DEPLOY_PATH="$HOME/open-ace"
                fi
            else
                # Not root, use current user as default
                DEPLOY_USER="${USER}"
                DEPLOY_PATH="$HOME/open-ace"
            fi
        fi
    else
        # Remote deployment - will check later during SSH connection
        DEPLOY_USER="openace"
        DEPLOY_PATH="/home/openace"  # Most servers run Linux
    fi
}

# Systemd service settings
SERVICE_PORT=""       # Web server port (will be read from config or use default)
SERVICE_HOST="0.0.0.0" # Web server host

# Multi-user workspace mode settings
WORKSPACE_MULTI_USER_MODE="true"
WORKSPACE_PORT_RANGE_START="3100"
WORKSPACE_PORT_RANGE_END="3200"
WORKSPACE_MAX_INSTANCES="20"
WORKSPACE_IDLE_TIMEOUT="30"

# Data directories to preserve during upgrade
DATA_DIRS=(
    "data"
    "logs"
)

# Data files to preserve (in ~/.open-ace/)
DATA_FILES=(
    "usage.db"
    "config.json"
    "feishu_users.json"
    "upload_marker.json"
)

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

    if [ -n "$default" ]; then
        echo -ne "${BLUE}$prompt [${default}]: ${NC}"
    else
        echo -ne "${BLUE}$prompt: ${NC}"
    fi

    read -r value

    if [ -z "$value" ] && [ -n "$default" ]; then
        value="$default"
    fi

    eval "$var_name='$value'"
}

prompt_yesno() {
    local prompt="$1"
    local default="$2"
    local var_name="$3"

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

        # Stop the service (need sudo for system service)
        if sudo systemctl is-active --quiet "${service_name}.service" 2>/dev/null; then
            sudo systemctl stop "${service_name}.service"
            if [ $? -eq 0 ]; then
                print_success "已停止 ${service_name} 服务"
            else
                print_warning "停止 ${service_name} 服务失败"
            fi
        fi

        # Disable the service (need sudo for system service)
        if sudo systemctl is-enabled --quiet "${service_name}.service" 2>/dev/null; then
            sudo systemctl disable "${service_name}.service"
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

# Configure sudoers for multi-user workspace mode
configure_sudoers() {
    local run_user="$1"

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
        print_info "请先安装 qwen-code-webui:"
        print_info "  npm install -g @ivycomputing/qwen-code-webui"
        print_info ""
        print_info "安装完成后，手动配置 sudoers:"
        print_info "  sudo visudo -f /etc/sudoers.d/open-ace-webui"
        print_info "  添加: $run_user ALL=(ALL) NOPASSWD: /path/to/qwen-code-webui *"
        return 1
    fi

    print_success "找到 qwen-code-webui: $webui_path"

    # Create sudoers file
    local sudoers_file="/etc/sudoers.d/open-ace-webui"
    local sudoers_content="# Open ACE WebUI - Multi-user mode sudo configuration
# Generated by install.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Allows the service account to run qwen-code-webui as other users

$run_user ALL=(ALL) NOPASSWD: $webui_path *
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
        print_info "服务账号 '$run_user' 可以执行:"
        print_info "  sudo -u <username> $webui_path --port <port>"
    else
        print_error "Sudoers 语法错误，回滚..."
        rm -f "$sudoers_file"
        return 1
    fi

    return 0
}

# ============================================================================
# Systemd Service Functions
# ============================================================================

get_web_port_from_config() {
    local config_file="$HOME/.open-ace/config.json"
    local default_port="5000"

    if [ -f "$config_file" ]; then
        # Try to extract port from config.json
        local port=$(grep -o '"web_port"[[:space:]]*:[[:space:]]*[0-9]*' "$config_file" 2>/dev/null | grep -o '[0-9]*$')
        if [ -n "$port" ]; then
            echo "$port"
            return
        fi
    fi

    echo "$default_port"
}

# Run pip as a specific user (to avoid root warnings)
run_pip_as_user() {
    local user="$1"
    shift
    local pip_cmd="$@"

    if [ "$EUID" -eq 0 ] && [ -n "$user" ] && [ "$user" != "root" ]; then
        # Running as root but user specified - run pip as that user
        su - "$user" -c "$pip_cmd"
    else
        # Run pip directly
        $pip_cmd
    fi
}

install_systemd_service() {
    local target_path="$1"
    local user="$2"
    local port="${3:-5000}"
    local host="${4:-0.0.0.0}"

    local service_template="$SOURCE_DIR/scripts/open-ace.service"
    local service_file="/etc/systemd/system/open-ace.service"

    if [ ! -f "$service_template" ]; then
        print_error "Service template not found: $service_template"
        return 1
    fi

    # Check if systemd is available
    if ! command -v systemctl &>/dev/null; then
        print_warning "systemctl not found. Skipping systemd service installation."
        print_info "You can manually run the web server with: cd $target_path && python3 web.py"
        return 0
    fi

    # Check if running as root or with sudo
    if [ "$EUID" -ne 0 ]; then
        print_warning "Root privileges required to install systemd service."
        print_info "Please run: sudo $0 --config $CONFIG_FILE"
        print_info "Or manually install the service after installation."
        return 1
    fi

    # Get user's primary group
    local group=$(id -gn "$user")

    # Create service file from template
    print_info "Creating systemd service file..."
    sed -e "s|__USER__|$user|g" \
        -e "s|__GROUP__|$group|g" \
        -e "s|__INSTALL_PATH__|$target_path|g" \
        -e "s|__PORT__|$port|g" \
        -e "s|__HOST__|$host|g" \
        "$service_template" > "$service_file"

    if [ $? -ne 0 ]; then
        print_error "Failed to create service file"
        return 1
    fi

    # Reload systemd daemon
    print_info "Reloading systemd daemon..."
    systemctl daemon-reload

    # Enable the service
    print_info "Enabling open-ace service..."
    systemctl enable open-ace.service

    # Check if service is already running
    if systemctl is-active --quiet open-ace.service; then
        print_info "Restarting open-ace service..."
        systemctl restart open-ace.service
    else
        print_info "Starting open-ace service..."
        systemctl start open-ace.service
    fi

    # Check service status
    sleep 2
    if systemctl is-active --quiet open-ace.service; then
        print_success "Systemd service installed and started successfully"
        print_info "Service name: open-ace"
        print_info "Status: systemctl status open-ace"
        print_info "Logs: journalctl -u open-ace -f"
        print_info "Web interface: http://localhost:$port"
    else
        print_error "Service failed to start. Check logs with: journalctl -u open-ace -n 50"
        return 1
    fi

    return 0
}

install_systemd_service_remote() {
    local remote="$1"
    local target_path="$2"
    local user="$3"
    local port="${4:-5000}"
    local host="${5:-0.0.0.0}"

    # Check if systemd is available on remote
    if ! ssh "$remote" "command -v systemctl &>/dev/null"; then
        print_warning "systemctl not found on remote machine. Skipping systemd service installation."
        print_info "You can manually run the web server with: ssh $remote 'cd $target_path && python3 web.py'"
        return 0
    fi

    # Create service file on remote
    print_info "Creating systemd service on remote machine..."
    ssh "$remote" "
        # Check if we have sudo access
        if ! sudo -n true 2>/dev/null; then
            echo 'SUDO_REQUIRED'
            exit 0
        fi

        # Get user's primary group
        GROUP=\$(id -gn '$user')

        # Create service file
        sudo tee /etc/systemd/system/open-ace.service > /dev/null << 'EOFSERVICE'
[Unit]
Description=Open ACE - Web Dashboard for AI Token Usage
Documentation=https://github.com/open-ace/open-ace
After=network.target

[Service]
Type=simple
User=$user
Group=\$GROUP
WorkingDirectory=$target_path
ExecStart=/usr/bin/python3 $target_path/web.py
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# Environment variables
Environment=AI_TOKEN_WEB_PORT=$port
Environment=AI_TOKEN_WEB_HOST=$host

# Security settings
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOFSERVICE

        # Reload and enable service
        sudo systemctl daemon-reload
        sudo systemctl enable open-ace.service

        # Start or restart service
        if sudo systemctl is-active --quiet open-ace.service; then
            sudo systemctl restart open-ace.service
        else
            sudo systemctl start open-ace.service
        fi

        # Wait and check status
        sleep 2
        if sudo systemctl is-active --quiet open-ace.service; then
            echo 'SERVICE_STARTED'
        else
            echo 'SERVICE_FAILED'
        fi
    "

    local result=$(ssh "$remote" "
        if sudo -n true 2>/dev/null; then
            if sudo systemctl is-active --quiet open-ace.service; then
                echo 'SERVICE_STARTED'
            else
                echo 'SERVICE_FAILED'
            fi
        else
            echo 'SUDO_REQUIRED'
        fi
    ")

    case "$result" in
        SERVICE_STARTED)
            print_success "Systemd service installed and started on remote machine"
            print_info "Service name: open-ace"
            print_info "Status: ssh $remote 'sudo systemctl status open-ace'"
            print_info "Logs: ssh $remote 'sudo journalctl -u open-ace -f'"
            print_info "Web interface: http://$DEPLOY_HOST:$port"
            ;;
        SERVICE_FAILED)
            print_error "Service failed to start on remote machine"
            print_info "Check logs with: ssh $remote 'sudo journalctl -u open-ace -n 50'"
            return 1
            ;;
        SUDO_REQUIRED)
            print_warning "Sudo privileges required on remote machine."
            print_info "Please run the following on the remote machine:"
            print_info "  sudo systemctl enable open-ace"
            print_info "  sudo systemctl start open-ace"
            ;;
    esac

    return 0
}

# ============================================================================
# Config File Parsing
# ============================================================================

parse_config_file() {
    if [ ! -f "$CONFIG_FILE" ]; then
        print_error "Config file not found: $CONFIG_FILE"
        exit 1
    fi

    print_info "Reading configuration from: $CONFIG_FILE"

    # Source the config file (it should be a shell script with variable assignments)
    source "$CONFIG_FILE"

    # Validate source directory
    validate_source_dir

    # Determine install mode based on DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        INSTALL_MODE="local"
    else
        INSTALL_MODE="deploy"
    fi

    # Set default service port if not specified
    if [ -z "$SERVICE_PORT" ]; then
        SERVICE_PORT=$(get_web_port_from_config)
    fi
}

# ============================================================================
# Interactive Configuration
# ============================================================================

interactive_config() {
    print_header "Open ACE - Installation Configuration"

    # Ask for installation mode
    echo -e "${BLUE}Select installation mode:${NC}"
    echo "  1) Local (install on this machine)"
    echo "  2) Deploy (deploy to a remote machine via SSH)"
    echo ""
    prompt_input "Enter choice" "1" mode_choice

    case $mode_choice in
        1)
            INSTALL_MODE="local"
            # Initialize deployment user based on openace user availability
            init_deploy_user "true"
            configure_local
            ;;
        2)
            INSTALL_MODE="deploy"
            # Initialize deployment user (remote will be checked later)
            init_deploy_user "false"
            configure_deploy
            ;;
        *)
            print_error "Invalid choice"
            exit 1
            ;;
    esac
}

configure_local() {
    echo ""
    echo -e "${YELLOW}Configuring deployment...${NC}"
    echo ""

    prompt_input "Deployment user" "$DEPLOY_USER" DEPLOY_USER
    prompt_input "Deployment path" "$DEPLOY_PATH" DEPLOY_PATH

    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        # Get default port from config or use 5000
        local default_port=$(get_web_port_from_config)
        prompt_input "Web server port" "$default_port" SERVICE_PORT
        prompt_input "Web server host" "$SERVICE_HOST" SERVICE_HOST
    fi

    # Ask about multi-user workspace mode
    echo ""
    echo -e "${BLUE}=== Workspace 多用户模式配置 ===${NC}"
    echo -e "${YELLOW}多用户模式会为每个用户启动独立的 qwen-code-webui 进程${NC}"
    prompt_yesno "启用多用户模式?" "y" enable_multi_user
    if [ "$enable_multi_user" = "yes" ]; then
        WORKSPACE_MULTI_USER_MODE="true"
        prompt_input "端口池起始端口" "$WORKSPACE_PORT_RANGE_START" WORKSPACE_PORT_RANGE_START
        prompt_input "端口池结束端口" "$WORKSPACE_PORT_RANGE_END" WORKSPACE_PORT_RANGE_END
        prompt_input "最大实例数" "$WORKSPACE_MAX_INSTANCES" WORKSPACE_MAX_INSTANCES
        prompt_input "空闲超时时间(分钟)" "$WORKSPACE_IDLE_TIMEOUT" WORKSPACE_IDLE_TIMEOUT
    fi

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Local (this machine)"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    if [ "$install_service" = "yes" ]; then
        echo "  Systemd service: Yes"
        echo "  Web port: $SERVICE_PORT"
        echo "  Web host: $SERVICE_HOST"
    else
        echo "  Systemd service: No"
    fi
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "  Multi-user workspace: Enabled"
        echo "    - Port range: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
        echo "    - Max instances: $WORKSPACE_MAX_INSTANCES"
        echo "    - Idle timeout: $WORKSPACE_IDLE_TIMEOUT min"
    fi
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi

    # Store service installation preference
    INSTALL_SERVICE="$install_service"
}

configure_deploy() {
    echo ""
    echo -e "${YELLOW}Configuring remote deployment...${NC}"
    echo ""

    prompt_input "Remote host IP" "" DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        print_error "Remote host is required"
        exit 1
    fi

    prompt_input "Remote user" "$DEPLOY_USER" DEPLOY_USER
    prompt_input "Deployment path" "/home/$DEPLOY_USER/open-ace" DEPLOY_PATH

    # Ask about systemd service
    echo ""
    prompt_yesno "Install as systemd service on remote?" "y" install_service
    if [ "$install_service" = "yes" ]; then
        prompt_input "Web server port" "5000" SERVICE_PORT
        prompt_input "Web server host" "$SERVICE_HOST" SERVICE_HOST
    fi

    # Ask about multi-user workspace mode
    echo ""
    echo -e "${BLUE}=== Workspace 多用户模式配置 ===${NC}"
    echo -e "${YELLOW}多用户模式会为每个用户启动独立的 qwen-code-webui 进程${NC}"
    prompt_yesno "启用多用户模式?" "y" enable_multi_user
    if [ "$enable_multi_user" = "yes" ]; then
        WORKSPACE_MULTI_USER_MODE="true"
        prompt_input "端口池起始端口" "$WORKSPACE_PORT_RANGE_START" WORKSPACE_PORT_RANGE_START
        prompt_input "端口池结束端口" "$WORKSPACE_PORT_RANGE_END" WORKSPACE_PORT_RANGE_END
        prompt_input "最大实例数" "$WORKSPACE_MAX_INSTANCES" WORKSPACE_MAX_INSTANCES
        prompt_input "空闲超时时间(分钟)" "$WORKSPACE_IDLE_TIMEOUT" WORKSPACE_IDLE_TIMEOUT
    fi

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Deploy (via SSH)"
    echo "  Host: $DEPLOY_HOST"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    if [ "$install_service" = "yes" ]; then
        echo "  Systemd service: Yes"
        echo "  Web port: $SERVICE_PORT"
        echo "  Web host: $SERVICE_HOST"
    else
        echo "  Systemd service: No"
    fi
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        echo "  Multi-user workspace: Enabled"
        echo "    - Port range: $WORKSPACE_PORT_RANGE_START - $WORKSPACE_PORT_RANGE_END"
        echo "    - Max instances: $WORKSPACE_MAX_INSTANCES"
        echo "    - Idle timeout: $WORKSPACE_IDLE_TIMEOUT min"
    fi
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi

    # Store service installation preference
    INSTALL_SERVICE="$install_service"
}

# ============================================================================
# Local Installation
# ============================================================================

install_local() {
    print_header "Installing on Local Machine"

    # Validate source directory first
    validate_source_dir

    local target_path="$DEPLOY_PATH"
    local config_dir="$HOME/.open-ace"

    # Check if already installed (must have web.py to be considered valid installation)
    if [ -d "$target_path" ] && [ -f "$target_path/web.py" ]; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade

        if [ "$upgrade" = "yes" ]; then
            do_upgrade "$target_path" "$config_dir" "$DEPLOY_USER"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    elif [ -d "$target_path" ]; then
        # Directory exists but no valid installation
        print_warning "Directory exists at: $target_path but no valid installation found"
        print_info "Will perform fresh installation (existing directory contents will be preserved/merged)"
        do_fresh_install "$target_path" "$config_dir" "$DEPLOY_USER"
    else
        do_fresh_install "$target_path" "$config_dir" "$DEPLOY_USER"
    fi

    # Install systemd service if requested
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        print_header "Installing Systemd Service"
        install_systemd_service "$target_path" "$DEPLOY_USER" "$SERVICE_PORT" "$SERVICE_HOST"
    fi

    # Configure sudoers for multi-user workspace mode
    if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        # Stop existing qwen-code-webui systemd service first
        stop_webui_systemd_service
        configure_sudoers "$DEPLOY_USER"
        if [ $? -ne 0 ]; then
            print_warning "Sudoers 配置失败，多用户模式可能无法正常工作"
            print_info "请手动配置 /etc/sudoers.d/open-ace-webui"
        fi
    fi

    print_header "Local Installation Complete!"
    print_info "Installation path: $target_path"
    print_info "Config directory: $config_dir"
    echo ""
    if [ "$INSTALL_SERVICE" = "yes" ] && command -v systemctl &>/dev/null; then
        echo "Service management:"
        echo "  systemctl status open-ace"
        echo "  systemctl start open-ace"
        echo "  systemctl stop open-ace"
        echo "  systemctl restart open-ace"
        echo ""
        echo "View logs:"
        echo "  journalctl -u open-ace -f"
    else
        echo "To start the web server:"
        echo "  cd $target_path && python3 web.py"
    fi
    echo ""
}

# ============================================================================
# Remote Deployment
# ============================================================================

install_deploy() {
    print_header "Deploying to Remote Machine"

    # Validate source directory first
    validate_source_dir

    local remote="$DEPLOY_USER@$DEPLOY_HOST"
    local target_path="$DEPLOY_PATH"

    # Test SSH connection
    print_info "Testing SSH connection..."
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$remote" "echo 'Connection OK'" 2>/dev/null; then
        print_warning "SSH connection requires password or key setup"
        ssh -o ConnectTimeout=10 "$remote" "echo 'Connection OK'" || {
            print_error "Cannot connect to $remote"
            exit 1
        }
    fi
    print_success "SSH connection OK"

    # Check if already installed (must have web.py to be considered valid installation)
    if ssh "$remote" "[ -d '$target_path' ] && [ -f '$target_path/web.py' ]"; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade

        if [ "$upgrade" = "yes" ]; then
            do_upgrade_remote "$remote" "$target_path"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    elif ssh "$remote" "[ -d '$target_path' ]"; then
        # Directory exists but no valid installation
        print_warning "Directory exists at: $target_path but no valid installation found"
        print_info "Will perform fresh installation (existing directory contents will be preserved/merged)"
        do_fresh_install_remote "$remote" "$target_path"
    else
        do_fresh_install_remote "$remote" "$target_path"
    fi

    # Install systemd service if requested
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        print_header "Installing Systemd Service on Remote"
        install_systemd_service_remote "$remote" "$target_path" "$DEPLOY_USER" "$SERVICE_PORT" "$SERVICE_HOST"
    fi

    print_header "Remote Deployment Complete!"
    print_info "Remote host: $DEPLOY_HOST"
    print_info "Installation path: $target_path"
    echo ""
    if [ "$INSTALL_SERVICE" = "yes" ]; then
        echo "Service management on remote:"
        echo "  ssh $remote 'sudo systemctl status open-ace'"
        echo "  ssh $remote 'sudo systemctl start open-ace'"
        echo "  ssh $remote 'sudo systemctl stop open-ace'"
        echo "  ssh $remote 'sudo systemctl restart open-ace'"
        echo ""
        echo "View logs on remote:"
        echo "  ssh $remote 'sudo journalctl -u open-ace -f'"
    else
        echo "To start the web server on remote:"
        echo "  ssh $remote 'cd $target_path && python3 web.py'"
    fi
    echo ""
}

# ============================================================================
# Installation Functions
# ============================================================================

do_fresh_install() {
    local target_path="$1"
    local config_dir="$2"
    local install_user="$3"

    print_info "Performing fresh installation..."

    # Check if source and target are the same directory
    local source_abs="$(cd "$SOURCE_DIR" 2>/dev/null && pwd)"
    local target_abs="$(cd "$target_path" 2>/dev/null && pwd 2>/dev/null || echo "$target_path")"

    if [ "$source_abs" = "$target_abs" ]; then
        print_warning "Source and target directories are the same: $target_path"
        print_info "Skipping file copy (running from installation directory)"
    else
        # Create directories
        mkdir -p "$target_path"
        mkdir -p "$target_path/logs"

        # Copy files
        print_info "Copying files..."
        cp -r "$SOURCE_DIR"/* "$target_path/"

        # Set permissions
        chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
        chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true
    fi

    # Ensure logs directory exists
    mkdir -p "$target_path/logs"
    mkdir -p "$config_dir"

    # Create default config if not exists
    if [ ! -f "$config_dir/config.json" ]; then
        if [ -f "$target_path/config/config.json.sample" ]; then
            cp "$target_path/config/config.json.sample" "$config_dir/config.json"
            print_info "Created config file: $config_dir/config.json"
            print_warning "Please edit the config file with your settings."
        fi
    fi

    # Fix ownership if running as root and a different user is specified
    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
        print_info "Setting ownership to $install_user..."
        chown -R "$install_user:$(id -gn "$install_user")" "$target_path"
        chown -R "$install_user:$(id -gn "$install_user")" "$config_dir"
    fi

    # Install Python dependencies
    print_info "Installing Python dependencies..."
    if [ -f "$target_path/requirements.txt" ]; then
        # Check if pip is available
        if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
            print_error "pip is not installed."
            print_info "Please run the following command as root to install pip:"
            if command -v dnf &>/dev/null; then
                print_info "  dnf install python3-pip"
            elif command -v yum &>/dev/null; then
                print_info "  yum install python3-pip"
            elif command -v apt-get &>/dev/null; then
                print_info "  apt-get install python3-pip"
            else
                print_info "  (install python3-pip using your package manager)"
            fi
            print_info "Then run this script again."
            exit 1
        fi
        
        # Install dependencies (prefer vendor directory for offline install)
        if [ -d "$target_path/vendor" ] && [ "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            print_info "Installing from vendor directory (offline mode)..."
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            fi
        else
            # Install from network
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip3"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip"
            fi
        fi
    fi

    # Create default admin user
    print_info "Creating default admin user..."
    if [ -f "$target_path/scripts/init_db.py" ]; then
        cd "$target_path"
        if python3 scripts/init_db.py; then
            print_success "Default admin user created"
        else
            print_warning "Failed to create default admin user. You may need to run scripts/init_db.py manually."
        fi
        cd - > /dev/null
    else
        print_warning "init_db.py not found, skipping default user creation"
    fi

    print_success "Fresh installation completed"
}

do_upgrade() {
    local target_path="$1"
    local config_dir="$2"
    local install_user="$3"

    print_info "Upgrading existing installation..."

    # Check if source and target are the same directory
    local source_abs="$(cd "$SOURCE_DIR" 2>/dev/null && pwd)"
    local target_abs="$(cd "$target_path" 2>/dev/null && pwd 2>/dev/null || echo "$target_path")"

    if [ "$source_abs" = "$target_abs" ]; then
        print_warning "Source and target directories are the same: $target_path"
        print_info "Skipping file copy (running from installation directory)"
    else
        # Backup data files
        local backup_dir="/tmp/open-ace-backup-$(date +%Y%m%d%H%M%S)"
        mkdir -p "$backup_dir"

        # Backup config directory
        if [ -d "$config_dir" ]; then
            print_info "Backing up config directory..."
            cp -r "$config_dir" "$backup_dir/"
        fi

        # Backup database in target path (if any)
        if [ -f "$target_path/usage.db" ]; then
            print_info "Backing up database..."
            cp "$target_path/usage.db" "$backup_dir/"
        fi

        # Update files (preserve logs and data)
        print_info "Updating files..."
        # Remove old files except logs and data
        find "$target_path" -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' -exec rm -rf {} +
        # Copy new files
        cp -r "$SOURCE_DIR"/* "$target_path/"

        # Set permissions
        chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
        chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true
    fi

    # Fix ownership if running as root and a different user is specified
    if [ "$EUID" -eq 0 ] && [ -n "$install_user" ] && [ "$install_user" != "root" ]; then
        print_info "Setting ownership to $install_user..."
        chown -R "$install_user:$(id -gn "$install_user")" "$target_path"
        chown -R "$install_user:$(id -gn "$install_user")" "$config_dir"
    fi

    # Install Python dependencies
    print_info "Installing Python dependencies..."
    if [ -f "$target_path/requirements.txt" ]; then
        # Check if pip is available
        if ! command -v pip3 &>/dev/null && ! command -v pip &>/dev/null; then
            print_error "pip is not installed."
            print_info "Please run the following command as root to install pip:"
            if command -v dnf &>/dev/null; then
                print_info "  dnf install python3-pip"
            elif command -v yum &>/dev/null; then
                print_info "  yum install python3-pip"
            elif command -v apt-get &>/dev/null; then
                print_info "  apt-get install python3-pip"
            else
                print_info "  (install python3-pip using your package manager)"
            fi
            print_info "Then run this script again."
            exit 1
        fi
        
        # Install dependencies (prefer vendor directory for offline install)
        if [ -d "$target_path/vendor" ] && [ "$(ls -A "$target_path/vendor" 2>/dev/null)" ]; then
            print_info "Installing from vendor directory (offline mode)..."
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            fi
        else
            # Install from network
            if command -v pip3 &>/dev/null; then
                run_pip_as_user "$install_user" pip3 install --user -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip3"
            elif command -v pip &>/dev/null; then
                run_pip_as_user "$install_user" pip install --user -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip"
            fi
        fi
    fi

    # Create default admin user (if not exists)
    print_info "Ensuring default admin user exists..."
    if [ -f "$target_path/scripts/init_db.py" ]; then
        cd "$target_path"
        if python3 scripts/init_db.py; then
            print_success "Default admin user ready"
        else
            print_warning "Failed to create default admin user. You may need to run scripts/init_db.py manually."
        fi
        cd - > /dev/null
    else
        print_warning "init_db.py not found, skipping default user creation"
    fi

    print_success "Upgrade completed"
    print_info "Backup saved to: $backup_dir"
}

do_fresh_install_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Performing fresh remote installation..."

    # Create directories
    ssh "$remote" "mkdir -p '$target_path' '$target_path/logs'"

    # Copy files
    print_info "Copying files to remote..."
    scp -r "$SOURCE_DIR"/* "$remote:$target_path/"

    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"

    # Create config directory
    ssh "$remote" "mkdir -p '~/.open-ace'"

    # Install Python dependencies
    print_info "Installing Python dependencies on remote..."
    ssh "$remote" "
        cd '$target_path'
        # Check if pip is available
        if ! command -v pip3 >/dev/null 2>&1 && ! command -v pip >/dev/null 2>&1; then
            echo 'ERROR: pip is not installed on remote machine.'
            echo 'Please install pip first:'
            if command -v dnf >/dev/null 2>&1; then
                echo '  dnf install python3-pip'
            elif command -v yum >/dev/null 2>&1; then
                echo '  yum install python3-pip'
            elif command -v apt-get >/dev/null 2>&1; then
                echo '  apt-get install python3-pip'
            else
                echo '  (install python3-pip using your package manager)'
            fi
            exit 1
        fi
        # Install dependencies (prefer vendor directory for offline install)
        if [ -d 'vendor' ] && [ \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing from vendor directory (offline mode)...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-index --find-links=vendor -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-index --find-links=vendor -r requirements.txt
            fi
        else
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install --user -r requirements.txt
            fi
        fi
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

    # Create default admin user
    print_info "Creating default admin user on remote..."
    ssh "$remote" "
        cd '$target_path'
        if [ -f 'scripts/init_db.py' ]; then
            if python3 scripts/init_db.py; then
                echo 'Default admin user created successfully'
            else
                echo 'Warning: Failed to create default admin user. You may need to run scripts/init_db.py manually.'
            fi
        else
            echo 'Warning: init_db.py not found, skipping default user creation'
        fi
    "

    print_success "Fresh remote installation completed"
}

do_upgrade_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Upgrading remote installation..."

    # Backup data files
    local backup_dir="/tmp/open-ace-backup-$(date +%Y%m%d%H%M%S)"
    ssh "$remote" "mkdir -p '$backup_dir'"

    # Backup config directory
    ssh "$remote" "if [ -d '~/.open-ace' ]; then cp -r ~/.open-ace '$backup_dir/'; fi"

    # Backup database
    ssh "$remote" "if [ -f '$target_path/usage.db' ]; then cp '$target_path/usage.db' '$backup_dir/'; fi"

    # Update files (preserve logs and data)
    print_info "Updating remote files..."
    ssh "$remote" "cd '$target_path' && find . -mindepth 1 -maxdepth 1 ! -name 'logs' ! -name 'data' -exec rm -rf {} +"
    scp -r "$SOURCE_DIR"/* "$remote:$target_path/"

    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"

    # Install Python dependencies
    print_info "Installing Python dependencies on remote..."
    ssh "$remote" "
        cd '$target_path'
        # Check if pip is available
        if ! command -v pip3 >/dev/null 2>&1 && ! command -v pip >/dev/null 2>&1; then
            echo 'ERROR: pip is not installed on remote machine.'
            echo 'Please install pip first:'
            if command -v dnf >/dev/null 2>&1; then
                echo '  dnf install python3-pip'
            elif command -v yum >/dev/null 2>&1; then
                echo '  yum install python3-pip'
            elif command -v apt-get >/dev/null 2>&1; then
                echo '  apt-get install python3-pip'
            else
                echo '  (install python3-pip using your package manager)'
            fi
            exit 1
        fi
        # Install dependencies (prefer vendor directory for offline install)
        if [ -d 'vendor' ] && [ \"\$(ls -A vendor 2>/dev/null)\" ]; then
            echo 'Installing from vendor directory (offline mode)...'
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user --no-index --find-links=vendor -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install --user --no-index --find-links=vendor -r requirements.txt
            fi
        else
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install --user -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install --user -r requirements.txt
            fi
        fi
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

    # Create default admin user (if not exists)
    print_info "Ensuring default admin user exists on remote..."
    ssh "$remote" "
        cd '$target_path'
        if [ -f 'scripts/init_db.py' ]; then
            if python3 scripts/init_db.py; then
                echo 'Default admin user ready'
            else
                echo 'Warning: Failed to create default admin user. You may need to run scripts/init_db.py manually.'
            fi
        else
            echo 'Warning: init_db.py not found, skipping default user creation'
        fi
    "

    print_success "Remote upgrade completed"
    print_info "Backup saved to: $backup_dir on $DEPLOY_HOST"
}

# ============================================================================
# Main
# ============================================================================

show_help() {
    echo "Open ACE - Installation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --config FILE   Use configuration file"
    echo "  --help, -h      Show this help message"
    echo ""
    echo "Configuration File Format (shell script):"
    echo ""
    echo "  # Local installation (install on this machine)"
    echo "  DEPLOY_USER=\${USER}"
    echo "  DEPLOY_PATH=\$HOME/open-ace"
    echo ""
    echo "  # Remote deployment (deploy via SSH)"
    echo "  DEPLOY_HOST=192.168.1.100"
    echo "  DEPLOY_USER=openclaw"
    echo "  DEPLOY_PATH=/home/openclaw/open-ace"
    echo ""
    echo "  # Systemd service configuration (optional)"
    echo "  INSTALL_SERVICE=yes              # Install as systemd service"
    echo "  SERVICE_PORT=5000                # Web server port"
    echo "  SERVICE_HOST=0.0.0.0             # Web server host"
    echo ""
    echo "  # Multi-user workspace mode (optional)"
    echo "  WORKSPACE_MULTI_USER_MODE=true   # Enable multi-user mode"
    echo "  WORKSPACE_PORT_RANGE_START=3100  # Port pool start"
    echo "  WORKSPACE_PORT_RANGE_END=3200    # Port pool end"
    echo "  WORKSPACE_MAX_INSTANCES=20       # Max concurrent instances"
    echo "  WORKSPACE_IDLE_TIMEOUT=30        # Idle timeout (minutes)"
    echo ""
    echo "Multi-User Workspace Mode:"
    echo "  Requires qwen-code-webui installed:"
    echo "    npm install -g @ivycomputing/qwen-code-webui"
    echo ""
    echo "  The installer will auto-configure sudoers for user switching."
    echo "  Each user needs a system account and ~/.qwen/ directory."
    echo ""
    echo "Examples:"
    echo "  $0                              # Interactive mode"
    echo "  $0 --config install.conf        # Use config file"
    echo ""
    echo "After installation with systemd service:"
    echo "  systemctl status open-ace   # Check service status"
    echo "  systemctl start open-ace    # Start service"
    echo "  systemctl stop open-ace     # Stop service"
    echo "  systemctl restart open-ace  # Restart service"
    echo "  journalctl -u open-ace -f   # View logs"
    echo ""
    exit 0
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --config|-c)
            CONFIG_FILE="$2"
            shift 2
            ;;
        --help|-h)
            show_help
            ;;
        *)
            print_error "Unknown option: $1"
            echo "Run '$0 --help' for usage information."
            exit 1
            ;;
    esac
done

# Main execution
print_header "Open ACE - Installer"

if [ -n "$CONFIG_FILE" ]; then
    parse_config_file
else
    interactive_config
fi

# Perform installation
case $INSTALL_MODE in
    local)
        install_local
        ;;
    deploy)
        install_deploy
        ;;
    *)
        print_error "Invalid INSTALL_MODE: $INSTALL_MODE"
        exit 1
        ;;
esac