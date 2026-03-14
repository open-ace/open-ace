#!/bin/bash
#
# AI Token Analyzer - Installation Script
#
# This script installs or upgrades AI Token Analyzer.
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
SOURCE_DIR="$(dirname "$SCRIPT_DIR")"

# Default values
CONFIG_FILE=""
INSTALL_MODE=""  # "local" or "deploy"

# Deployment settings (for both local and deploy modes)
DEPLOY_HOST=""        # Empty for local mode, required for deploy mode
DEPLOY_USER="${USER}"
DEPLOY_PATH="$HOME/ai-token-analyzer"

# Data directories to preserve during upgrade
DATA_DIRS=(
    "data"
    "logs"
)

# Data files to preserve (in ~/.ai-token-analyzer/)
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

    # Determine install mode based on DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        INSTALL_MODE="local"
    else
        INSTALL_MODE="deploy"
    fi
}

# ============================================================================
# Interactive Configuration
# ============================================================================

interactive_config() {
    print_header "AI Token Analyzer - Installation Configuration"

    # Ask for installation mode
    echo -e "${BLUE}Select installation mode:${NC}"
    echo "  1) Local (install on this machine)"
    echo "  2) Deploy (deploy to a remote machine via SSH)"
    echo ""
    prompt_input "Enter choice" "1" mode_choice

    case $mode_choice in
        1)
            INSTALL_MODE="local"
            configure_local
            ;;
        2)
            INSTALL_MODE="deploy"
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

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Local (this machine)"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi
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
    prompt_input "Deployment path" "/home/$DEPLOY_USER/ai-token-analyzer" DEPLOY_PATH

    echo ""
    print_info "Configuration Summary:"
    echo "  Mode: Deploy (via SSH)"
    echo "  Host: $DEPLOY_HOST"
    echo "  User: $DEPLOY_USER"
    echo "  Path: $DEPLOY_PATH"
    echo ""

    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi
}

# ============================================================================
# Local Installation
# ============================================================================

install_local() {
    print_header "Installing on Local Machine"

    local target_path="$DEPLOY_PATH"
    local config_dir="$HOME/.ai-token-analyzer"

    # Check if already installed
    if [ -d "$target_path" ]; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade

        if [ "$upgrade" = "yes" ]; then
            do_upgrade "$target_path" "$config_dir"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    else
        do_fresh_install "$target_path" "$config_dir"
    fi

    print_header "Local Installation Complete!"
    print_info "Installation path: $target_path"
    print_info "Config directory: $config_dir"
    echo ""
    echo "To start the web server:"
    echo "  cd $target_path && python3 web.py"
    echo ""
}

# ============================================================================
# Remote Deployment
# ============================================================================

install_deploy() {
    print_header "Deploying to Remote Machine"

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

    # Check if already installed
    if ssh "$remote" "[ -d '$target_path' ]"; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade

        if [ "$upgrade" = "yes" ]; then
            do_upgrade_remote "$remote" "$target_path"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    else
        do_fresh_install_remote "$remote" "$target_path"
    fi

    print_header "Remote Deployment Complete!"
    print_info "Remote host: $DEPLOY_HOST"
    print_info "Installation path: $target_path"
    echo ""
}

# ============================================================================
# Installation Functions
# ============================================================================

do_fresh_install() {
    local target_path="$1"
    local config_dir="$2"

    print_info "Performing fresh installation..."

    # Create directories
    mkdir -p "$target_path"
    mkdir -p "$target_path/logs"
    mkdir -p "$config_dir"

    # Copy files
    print_info "Copying files..."
    cp -r "$SOURCE_DIR"/* "$target_path/"

    # Set permissions
    chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
    chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true

    # Create default config if not exists
    if [ ! -f "$config_dir/config.json" ]; then
        if [ -f "$target_path/config/config.json.sample" ]; then
            cp "$target_path/config/config.json.sample" "$config_dir/config.json"
            print_info "Created config file: $config_dir/config.json"
            print_warning "Please edit the config file with your settings."
        fi
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
                pip3 install --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            elif command -v pip &>/dev/null; then
                pip install --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            fi
        else
            # Install from network
            if command -v pip3 &>/dev/null; then
                pip3 install -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip3"
            elif command -v pip &>/dev/null; then
                pip install -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip"
            fi
        fi
    fi

    print_success "Fresh installation completed"
}

do_upgrade() {
    local target_path="$1"
    local config_dir="$2"

    print_info "Upgrading existing installation..."

    # Backup data files
    local backup_dir="/tmp/ai-token-analyzer-backup-$(date +%Y%m%d%H%M%S)"
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
                pip3 install --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            elif command -v pip &>/dev/null; then
                pip install --no-index --find-links="$target_path/vendor" -r "$target_path/requirements.txt" && print_success "Dependencies installed from vendor"
            fi
        else
            # Install from network
            if command -v pip3 &>/dev/null; then
                pip3 install -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip3"
            elif command -v pip &>/dev/null; then
                pip install -r "$target_path/requirements.txt" && print_success "Dependencies installed with pip"
            fi
        fi
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
    ssh "$remote" "mkdir -p '~/.ai-token-analyzer'"

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
                pip3 install --no-index --find-links=vendor -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install --no-index --find-links=vendor -r requirements.txt
            fi
        else
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install -r requirements.txt
            fi
        fi
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

    print_success "Fresh remote installation completed"
}

do_upgrade_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Upgrading remote installation..."

    # Backup data files
    local backup_dir="/tmp/ai-token-analyzer-backup-$(date +%Y%m%d%H%M%S)"
    ssh "$remote" "mkdir -p '$backup_dir'"

    # Backup config directory
    ssh "$remote" "if [ -d '~/.ai-token-analyzer' ]; then cp -r ~/.ai-token-analyzer '$backup_dir/'; fi"

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
                pip3 install --no-index --find-links=vendor -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install --no-index --find-links=vendor -r requirements.txt
            fi
        else
            if command -v pip3 >/dev/null 2>&1; then
                pip3 install -r requirements.txt
            elif command -v pip >/dev/null 2>&1; then
                pip install -r requirements.txt
            fi
        fi
    " || {
        print_error "Failed to install dependencies on remote."
        print_info "Please ensure pip is installed on the remote machine."
        exit 1
    }

    print_success "Remote upgrade completed"
    print_info "Backup saved to: $backup_dir on $DEPLOY_HOST"
}

# ============================================================================
# Main
# ============================================================================

show_help() {
    echo "AI Token Analyzer - Installation Script"
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
    echo "  DEPLOY_PATH=\$HOME/ai-token-analyzer"
    echo ""
    echo "  # Remote deployment (deploy via SSH)"
    echo "  DEPLOY_HOST=192.168.1.100"
    echo "  DEPLOY_USER=openclaw"
    echo "  DEPLOY_PATH=/home/openclaw/ai-token-analyzer"
    echo ""
    echo "Examples:"
    echo "  $0                              # Interactive mode"
    echo "  $0 --config install.conf        # Use config file"
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
print_header "AI Token Analyzer - Installer"

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