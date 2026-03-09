#!/bin/bash
#
# AI Token Analyzer - Installation Script
#
# This script installs or upgrades AI Token Analyzer on local or remote machines.
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
INSTALL_MODE=""  # "local" or "remote"

# Local deployment defaults
LOCAL_USER="${USER}"
LOCAL_PATH="$HOME/ai-token-analyzer"
LOCAL_INTERVAL="30"  # minutes

# Remote deployment defaults
REMOTE_HOST=""
REMOTE_USER=""
REMOTE_PATH="/home/\${REMOTE_USER}/ai-token-analyzer"
REMOTE_INTERVAL="30"  # minutes
REMOTE_SCHEDULER="systemd"  # "cron" or "systemd"

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
    
    # Validate required settings
    if [ -z "$INSTALL_MODE" ]; then
        print_error "INSTALL_MODE not set in config file"
        exit 1
    fi
    
    if [ "$INSTALL_MODE" = "remote" ]; then
        if [ -z "$REMOTE_HOST" ] || [ -z "$REMOTE_USER" ]; then
            print_error "REMOTE_HOST and REMOTE_USER required for remote installation"
            exit 1
        fi
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
    echo "  2) Remote (install on a remote machine)"
    echo ""
    prompt_input "Enter choice" "1" mode_choice
    
    case $mode_choice in
        1)
            INSTALL_MODE="local"
            configure_local
            ;;
        2)
            INSTALL_MODE="remote"
            configure_remote
            ;;
        *)
            print_error "Invalid choice"
            exit 1
            ;;
    esac
}

configure_local() {
    print_header "Local Installation Configuration"
    
    echo -e "${YELLOW}Configuring local deployment...${NC}"
    echo ""
    
    prompt_input "Deployment user" "$LOCAL_USER" LOCAL_USER
    prompt_input "Deployment path" "$LOCAL_PATH" LOCAL_PATH
    prompt_input "Data collection interval (minutes)" "$LOCAL_INTERVAL" LOCAL_INTERVAL
    
    echo ""
    print_info "Local Configuration Summary:"
    echo "  User: $LOCAL_USER"
    echo "  Path: $LOCAL_PATH"
    echo "  Interval: $LOCAL_INTERVAL minutes"
    echo ""
    
    prompt_yesno "Proceed with installation?" "y" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Installation cancelled."
        exit 0
    fi
}

configure_remote() {
    print_header "Remote Installation Configuration"

    echo -e "${YELLOW}Configuring remote deployment...${NC}"
    echo ""

    prompt_input "Remote host IP" "" REMOTE_HOST
    if [ -z "$REMOTE_HOST" ]; then
        print_error "Remote host is required"
        exit 1
    fi

    prompt_input "Remote user" "openclaw" REMOTE_USER
    prompt_input "Remote deployment path" "/home/$REMOTE_USER/ai-token-analyzer" REMOTE_PATH
    prompt_input "Data collection interval (minutes)" "$REMOTE_INTERVAL" REMOTE_INTERVAL

    # Ask for scheduler type
    echo ""
    echo -e "${BLUE}Select scheduler type:${NC}"
    echo "  1) systemd timer (recommended for Linux servers)"
    echo "  2) cron job (simpler, works on all Unix systems)"
    echo ""
    prompt_input "Enter choice" "1" scheduler_choice

    case $scheduler_choice in
        1)
            REMOTE_SCHEDULER="systemd"
            ;;
        2)
            REMOTE_SCHEDULER="cron"
            ;;
        *)
            REMOTE_SCHEDULER="systemd"
            ;;
    esac

    echo ""
    print_info "Remote Configuration Summary:"
    echo "  Host: $REMOTE_HOST"
    echo "  User: $REMOTE_USER"
    echo "  Path: $REMOTE_PATH"
    echo "  Interval: $REMOTE_INTERVAL minutes"
    echo "  Scheduler: $REMOTE_SCHEDULER"
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
    
    local target_path="$LOCAL_PATH"
    local config_dir="$HOME/.ai-token-analyzer"
    
    # Check if already installed
    if [ -d "$target_path" ]; then
        print_warning "Existing installation found at: $target_path"
        prompt_yesno "Upgrade existing installation?" "y" upgrade
        
        if [ "$upgrade" = "yes" ]; then
            upgrade_local "$target_path" "$config_dir"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    else
        fresh_install_local "$target_path" "$config_dir"
    fi
    
    # Setup cron job
    setup_cron_local
    
    print_header "Local Installation Complete!"
    print_info "Installation path: $target_path"
    print_info "Config directory: $config_dir"
    print_info "Data collection interval: $LOCAL_INTERVAL minutes"
    echo ""
    echo "To start the web server:"
    echo "  cd $target_path && python3 web.py"
    echo ""
    echo "To collect data manually:"
    echo "  cd $target_path && python3 scripts/fetch_openclaw.py --days 1"
    echo ""
}

fresh_install_local() {
    local target_path="$1"
    local config_dir="$2"
    
    print_info "Performing fresh installation..."
    
    # Create directories
    mkdir -p "$target_path"
    mkdir -p "$target_path/logs"
    mkdir -p "$config_dir"
    
    # Copy files
    print_info "Copying files..."
    rsync -avz --exclude='.git' --exclude='.qwen' --exclude='.claude' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
        --exclude='dist' --exclude='screenshots' \
        "$SOURCE_DIR/" "$target_path/"
    
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
    
    print_success "Fresh installation completed"
}

upgrade_local() {
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
    
    # Update files (preserve data)
    print_info "Updating files..."
    rsync -avz --delete \
        --exclude='.git' --exclude='.qwen' --exclude='.claude' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
        --exclude='dist' --exclude='screenshots' \
        --exclude='logs/*' --exclude='*.db' --exclude='data/*' \
        "$SOURCE_DIR/" "$target_path/"
    
    # Set permissions
    chmod +x "$target_path/scripts/"*.py 2>/dev/null || true
    chmod +x "$target_path/scripts/"*.sh 2>/dev/null || true
    
    print_success "Upgrade completed"
    print_info "Backup saved to: $backup_dir"
}

setup_cron_local() {
    print_info "Setting up cron job for data collection..."
    
    local cron_cmd="cd $LOCAL_PATH && python3 scripts/fetch_openclaw.py --days 1 >> $LOCAL_PATH/logs/cron.log 2>&1"
    
    # Calculate cron schedule based on interval
    local cron_schedule=""
    if [ "$LOCAL_INTERVAL" -lt 60 ]; then
        cron_schedule="*/$LOCAL_INTERVAL * * * *"
    else
        local hours=$((LOCAL_INTERVAL / 60))
        cron_schedule="0 */$hours * * *"
    fi
    
    # Check if cron job already exists
    local existing_cron=$(crontab -l 2>/dev/null | grep -F "fetch_openclaw.py" || true)
    
    if [ -n "$existing_cron" ]; then
        print_warning "Cron job already exists:"
        echo "  $existing_cron"
        prompt_yesno "Update existing cron job?" "y" update_cron
        
        if [ "$update_cron" = "yes" ]; then
            # Remove old and add new
            (crontab -l 2>/dev/null | grep -v "fetch_openclaw.py" || true; echo "$cron_schedule $cron_cmd") | crontab -
            print_success "Cron job updated"
        fi
    else
        prompt_yesno "Add cron job for automatic data collection?" "y" add_cron
        if [ "$add_cron" = "yes" ]; then
            (crontab -l 2>/dev/null || true; echo "$cron_schedule $cron_cmd") | crontab -
            print_success "Cron job added: $cron_schedule"
        fi
    fi
}

# ============================================================================
# Remote Installation
# ============================================================================

install_remote() {
    print_header "Installing on Remote Machine"
    
    local remote="$REMOTE_USER@$REMOTE_HOST"
    local target_path="$REMOTE_PATH"
    
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
            upgrade_remote "$remote" "$target_path"
        else
            print_info "Installation cancelled."
            exit 0
        fi
    else
        fresh_install_remote "$remote" "$target_path"
    fi
    
    # Setup cron job
    setup_cron_remote "$remote" "$target_path"
    
    print_header "Remote Installation Complete!"
    print_info "Remote host: $REMOTE_HOST"
    print_info "Installation path: $target_path"
    print_info "Data collection interval: $REMOTE_INTERVAL minutes"
    echo ""
    echo "To collect data manually:"
    echo "  ssh $remote 'cd $target_path && python3 scripts/fetch_openclaw.py --days 1'"
    echo ""
}

fresh_install_remote() {
    local remote="$1"
    local target_path="$2"
    
    print_info "Performing fresh remote installation..."
    
    # Create directories
    ssh "$remote" "mkdir -p '$target_path' '$target_path/logs'"
    
    # Copy files
    print_info "Copying files to remote..."
    rsync -avz --exclude='.git' --exclude='.qwen' --exclude='.claude' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
        --exclude='dist' --exclude='screenshots' \
        "$SOURCE_DIR/" "$remote:$target_path/"
    
    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"
    
    # Create config directory
    ssh "$remote" "mkdir -p '~/.ai-token-analyzer'"
    
    print_success "Fresh remote installation completed"
}

upgrade_remote() {
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
    
    # Update files (preserve data)
    print_info "Updating remote files..."
    rsync -avz --delete \
        --exclude='.git' --exclude='.qwen' --exclude='.claude' \
        --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
        --exclude='dist' --exclude='screenshots' \
        --exclude='logs/*' --exclude='*.db' --exclude='data/*' \
        "$SOURCE_DIR/" "$remote:$target_path/"
    
    # Set permissions
    ssh "$remote" "chmod +x '$target_path/scripts/'*.py '$target_path/scripts/'*.sh 2>/dev/null || true"
    
    print_success "Remote upgrade completed"
    print_info "Backup saved to: $backup_dir on $REMOTE_HOST"
}

setup_cron_remote() {
    local remote="$1"
    local target_path="$2"

    if [ "$REMOTE_SCHEDULER" = "systemd" ]; then
        setup_systemd_remote "$remote" "$target_path"
    else
        setup_cron_job_remote "$remote" "$target_path"
    fi
}

setup_systemd_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Setting up systemd timer on remote machine..."

    # Check if systemd is available
    if ! ssh "$remote" "which systemctl" &>/dev/null; then
        print_warning "systemd not available on remote, falling back to cron"
        setup_cron_job_remote "$remote" "$target_path"
        return
    fi

    # Create service file with correct path
    local service_content="[Unit]
Description=AI Token Analyzer - OpenClaw Data Collection
After=network.target

[Service]
Type=simple
User=$REMOTE_USER
Group=$REMOTE_USER
WorkingDirectory=$target_path
Environment=\"PYTHONUNBUFFERED=1\"
ExecStart=/usr/bin/python3 $target_path/scripts/fetch_openclaw.py --days 1

[Install]
WantedBy=multi-user.target"

    # Create timer file
    local timer_content="[Unit]
Description=AI Token Analyzer - OpenClaw Data Collection Timer
Requires=fetch-openclaw.service

[Timer]
OnBootSec=60s
OnUnitActiveSec=${REMOTE_INTERVAL}min
AccuracySec=1s
Persistent=true

[Install]
WantedBy=timers.target"

    # Write service and timer files
    print_info "Creating systemd service files..."
    ssh "$remote" "cat > /tmp/fetch-openclaw.service << 'EOFSERVICE'
$service_content
EOFSERVICE"

    ssh "$remote" "cat > /tmp/fetch-openclaw.timer << 'EOFTIMER'
$timer_content
EOFTIMER"

    # Check if timer already exists
    local existing_timer=$(ssh "$remote" "systemctl is-active fetch-openclaw.timer 2>/dev/null || echo 'inactive'")

    if [ "$existing_timer" = "active" ]; then
        print_warning "systemd timer already running"
        prompt_yesno "Update existing timer?" "y" update_timer

        if [ "$update_timer" = "yes" ]; then
            # Stop timer, update files, restart
            ssh "$remote" "sudo systemctl stop fetch-openclaw.timer fetch-openclaw.service 2>/dev/null || true"
            ssh "$remote" "sudo mv /tmp/fetch-openclaw.service /etc/systemd/system/"
            ssh "$remote" "sudo mv /tmp/fetch-openclaw.timer /etc/systemd/system/"
            ssh "$remote" "sudo systemctl daemon-reload"
            ssh "$remote" "sudo systemctl enable fetch-openclaw.timer"
            ssh "$remote" "sudo systemctl start fetch-openclaw.timer"
            print_success "systemd timer updated"
        fi
    else
        prompt_yesno "Install systemd timer for automatic data collection?" "y" install_timer
        if [ "$install_timer" = "yes" ]; then
            # Remove old cron job if exists
            ssh "$remote" "(crontab -l 2>/dev/null | grep -v 'fetch_openclaw.py' || true) | crontab -" 2>/dev/null || true

            # Install systemd files
            ssh "$remote" "sudo mv /tmp/fetch-openclaw.service /etc/systemd/system/"
            ssh "$remote" "sudo mv /tmp/fetch-openclaw.timer /etc/systemd/system/"
            ssh "$remote" "sudo systemctl daemon-reload"
            ssh "$remote" "sudo systemctl enable fetch-openclaw.timer"
            ssh "$remote" "sudo systemctl start fetch-openclaw.timer"
            print_success "systemd timer installed and started"

            # Show status
            ssh "$remote" "sudo systemctl status fetch-openclaw.timer --no-pager" || true
        fi
    fi
}

setup_cron_job_remote() {
    local remote="$1"
    local target_path="$2"

    print_info "Setting up cron job on remote machine..."

    local cron_cmd="cd $target_path && python3 scripts/fetch_openclaw.py --days 1 >> $target_path/logs/cron.log 2>&1"

    # Calculate cron schedule
    local cron_schedule=""
    if [ "$REMOTE_INTERVAL" -lt 60 ]; then
        cron_schedule="*/$REMOTE_INTERVAL * * * *"
    else
        local hours=$((REMOTE_INTERVAL / 60))
        cron_schedule="0 */$hours * * *"
    fi

    # Check if cron job exists
    local existing_cron=$(ssh "$remote" "crontab -l 2>/dev/null | grep -F 'fetch_openclaw.py' || true")

    if [ -n "$existing_cron" ]; then
        print_warning "Cron job already exists on remote:"
        echo "  $existing_cron"
        prompt_yesno "Update existing cron job?" "y" update_cron

        if [ "$update_cron" = "yes" ]; then
            ssh "$remote" "(crontab -l 2>/dev/null | grep -v 'fetch_openclaw.py' || true; echo '$cron_schedule $cron_cmd') | crontab -"
            print_success "Remote cron job updated"
        fi
    else
        prompt_yesno "Add cron job for automatic data collection?" "y" add_cron
        if [ "$add_cron" = "yes" ]; then
            ssh "$remote" "(crontab -l 2>/dev/null || true; echo '$cron_schedule $cron_cmd') | crontab -"
            print_success "Remote cron job added: $cron_schedule"
        fi
    fi
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
    echo "  # For local installation"
    echo "  INSTALL_MODE=local"
    echo "  LOCAL_USER=\${USER}"
    echo "  LOCAL_PATH=\$HOME/ai-token-analyzer"
    echo "  LOCAL_INTERVAL=30"
    echo ""
    echo "  # For remote installation"
    echo "  INSTALL_MODE=remote"
    echo "  REMOTE_HOST=192.168.1.100"
    echo "  REMOTE_USER=openclaw"
    echo "  REMOTE_PATH=/home/openclaw/ai-token-analyzer"
    echo "  REMOTE_INTERVAL=30"
    echo "  REMOTE_SCHEDULER=systemd  # 'systemd' or 'cron'"
    echo ""
    echo "Scheduler Types:"
    echo "  systemd - Uses systemd timer (recommended for Linux servers)"
    echo "  cron    - Uses cron job (simpler, works on all Unix systems)"
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
    remote)
        install_remote
        ;;
    *)
        print_error "Invalid INSTALL_MODE: $INSTALL_MODE"
        exit 1
        ;;
esac