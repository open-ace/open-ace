#!/bin/bash
#
# Open ACE - AI Computing Explorer - Uninstallation Script
#
# This script uninstalls Open ACE.
# Supports both local uninstallation and remote uninstallation via SSH.
#
# Usage:
#   ./uninstall.sh                      # Interactive mode
#   ./uninstall.sh --config uninstall.conf  # Use config file
#   ./uninstall.sh --help               # Show help
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

# Default values
CONFIG_FILE=""
UNINSTALL_MODE=""  # "local" or "remote"

# Uninstallation settings
DEPLOY_HOST=""        # Empty for local mode, required for remote mode
DEPLOY_USER="${USER}"
DEPLOY_PATH="$HOME/open-ace"

# Data removal options
REMOVE_CONFIG=false
REMOVE_DATA=false

# Auto-detect installation from systemd service file
detect_existing_installation() {
    local service_file="/etc/systemd/system/open-ace.service"

    if [ -f "$service_file" ]; then
        print_info "Found existing systemd service: $service_file"

        # Extract WorkingDirectory from service file
        local working_dir=$(grep "^WorkingDirectory=" "$service_file" | cut -d= -f2)
        if [ -n "$working_dir" ]; then
            DEPLOY_PATH="$working_dir"
            print_info "Detected installation path: $DEPLOY_PATH"
        fi

        # Extract User from service file
        local service_user=$(grep "^User=" "$service_file" | cut -d= -f2)
        if [ -n "$service_user" ]; then
            DEPLOY_USER="$service_user"
            print_info "Detected service user: $DEPLOY_USER"
        fi

        return 0
    fi

    return 1
}

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
# Systemd Service Functions
# ============================================================================

uninstall_systemd_service() {
    local service_file="/etc/systemd/system/open-ace.service"

    # Check if systemd is available
    if ! command -v systemctl &>/dev/null; then
        print_info "systemctl not found. No systemd service to remove."
        return 0
    fi

    # Check if service exists
    if [ ! -f "$service_file" ]; then
        print_info "No systemd service file found."
        return 0
    fi

    # Check if running as root or with sudo
    if [ "$EUID" -ne 0 ]; then
        print_warning "Root privileges required to uninstall systemd service."
        print_info "Please run: sudo $0"
        return 1
    fi

    # Stop the service if running
    if systemctl is-active --quiet open-ace.service 2>/dev/null; then
        print_info "Stopping open-ace service..."
        systemctl stop open-ace.service
        print_success "Service stopped"
    fi

    # Disable the service
    if systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
        print_info "Disabling open-ace service..."
        systemctl disable open-ace.service
        print_success "Service disabled"
    fi

    # Remove the service file
    print_info "Removing systemd service file..."
    rm -f "$service_file"
    print_success "Service file removed"

    # Reload systemd daemon
    print_info "Reloading systemd daemon..."
    systemctl daemon-reload
    print_success "Systemd daemon reloaded"

    return 0
}

uninstall_systemd_service_remote() {
    local remote="$1"

    # Check if systemd is available on remote
    if ! ssh "$remote" "command -v systemctl &>/dev/null"; then
        print_info "systemctl not found on remote machine. No systemd service to remove."
        return 0
    fi

    # Check if service exists on remote
    if ! ssh "$remote" "[ -f /etc/systemd/system/open-ace.service ]"; then
        print_info "No systemd service file found on remote machine."
        return 0
    fi

    print_info "Removing systemd service on remote machine..."

    local result=$(ssh "$remote" "
        # Check if we have sudo access
        if ! sudo -n true 2>/dev/null; then
            echo 'SUDO_REQUIRED'
            exit 0
        fi

        # Stop the service if running
        if sudo systemctl is-active --quiet open-ace.service 2>/dev/null; then
            sudo systemctl stop open-ace.service
        fi

        # Disable the service
        if sudo systemctl is-enabled --quiet open-ace.service 2>/dev/null; then
            sudo systemctl disable open-ace.service
        fi

        # Remove the service file
        sudo rm -f /etc/systemd/system/open-ace.service

        # Reload systemd daemon
        sudo systemctl daemon-reload

        echo 'SERVICE_REMOVED'
    ")

    case "$result" in
        SERVICE_REMOVED)
            print_success "Systemd service removed from remote machine"
            ;;
        SUDO_REQUIRED)
            print_warning "Sudo privileges required on remote machine."
            print_info "Please run the following on the remote machine:"
            print_info "  sudo systemctl stop open-ace"
            print_info "  sudo systemctl disable open-ace"
            print_info "  sudo rm /etc/systemd/system/open-ace.service"
            print_info "  sudo systemctl daemon-reload"
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

    # Determine uninstall mode based on DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        UNINSTALL_MODE="local"
    else
        UNINSTALL_MODE="remote"
    fi
}

# ============================================================================
# Interactive Configuration
# ============================================================================

interactive_config() {
    print_header "Open ACE - Uninstallation Configuration"

    # Ask for uninstallation mode
    echo -e "${BLUE}Select uninstallation mode:${NC}"
    echo "  1) Local (uninstall from this machine)"
    echo "  2) Remote (uninstall from a remote machine via SSH)"
    echo ""
    prompt_input "Enter choice" "1" mode_choice

    case $mode_choice in
        1)
            UNINSTALL_MODE="local"
            configure_local
            ;;
        2)
            UNINSTALL_MODE="remote"
            configure_remote
            ;;
        *)
            print_error "Invalid choice"
            exit 1
            ;;
    esac
}

configure_local() {
    echo ""
    echo -e "${YELLOW}Configuring local uninstallation...${NC}"
    echo ""

    prompt_input "Installation path" "$DEPLOY_PATH" DEPLOY_PATH

    # Check if installation exists
    if [ ! -d "$DEPLOY_PATH" ]; then
        print_warning "Installation directory not found: $DEPLOY_PATH"
        prompt_yesno "Continue anyway?" "n" continue_anyway
        if [ "$continue_anyway" != "yes" ]; then
            echo "Uninstallation cancelled."
            exit 0
        fi
    fi

    # Ask about data removal
    echo ""
    echo -e "${YELLOW}Data removal options:${NC}"
    prompt_yesno "Remove config directory (~/.open-ace)?" "n" remove_config
    REMOVE_CONFIG="$remove_config"

    prompt_yesno "Remove data directory (usage.db, logs)?" "n" remove_data
    REMOVE_DATA="$remove_data"

    echo ""
    print_info "Uninstallation Summary:"
    echo "  Mode: Local (this machine)"
    echo "  Installation path: $DEPLOY_PATH"
    echo "  Remove config directory: $REMOVE_CONFIG"
    echo "  Remove data: $REMOVE_DATA"
    echo ""

    prompt_yesno "Proceed with uninstallation?" "n" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Uninstallation cancelled."
        exit 0
    fi
}

configure_remote() {
    echo ""
    echo -e "${YELLOW}Configuring remote uninstallation...${NC}"
    echo ""

    prompt_input "Remote host IP" "" DEPLOY_HOST
    if [ -z "$DEPLOY_HOST" ]; then
        print_error "Remote host is required"
        exit 1
    fi

    prompt_input "Remote user" "$DEPLOY_USER" DEPLOY_USER
    prompt_input "Installation path" "/home/$DEPLOY_USER/open-ace" DEPLOY_PATH

    # Ask about data removal
    echo ""
    echo -e "${YELLOW}Data removal options:${NC}"
    prompt_yesno "Remove config directory (~/.open-ace)?" "n" remove_config
    REMOVE_CONFIG="$remove_config"

    prompt_yesno "Remove data directory (usage.db, logs)?" "n" remove_data
    REMOVE_DATA="$remove_data"

    echo ""
    print_info "Uninstallation Summary:"
    echo "  Mode: Remote (via SSH)"
    echo "  Host: $DEPLOY_HOST"
    echo "  User: $DEPLOY_USER"
    echo "  Installation path: $DEPLOY_PATH"
    echo "  Remove config directory: $REMOVE_CONFIG"
    echo "  Remove data: $REMOVE_DATA"
    echo ""

    prompt_yesno "Proceed with uninstallation?" "n" confirm
    if [ "$confirm" != "yes" ]; then
        echo "Uninstallation cancelled."
        exit 0
    fi
}

# ============================================================================
# Local Uninstallation
# ============================================================================

uninstall_local() {
    print_header "Uninstalling from Local Machine"

    local target_path="$DEPLOY_PATH"

    # Infer DEPLOY_USER from DEPLOY_PATH if running as root
    # Example: DEPLOY_PATH=/home/openace -> DEPLOY_USER=openace
    if [ "$EUID" -eq 0 ] && [ -n "$DEPLOY_PATH" ]; then
        # Extract user from path like /home/openace or /home/openace/open-ace
        local path_user=$(echo "$DEPLOY_PATH" | sed -n 's|^/home/\([^/]*\).*|\1|p')
        if [ -n "$path_user" ] && [ "$path_user" != "root" ]; then
            DEPLOY_USER="$path_user"
        fi
    fi

    # Determine config directory based on deploy user
    # Same logic as install.sh: use DEPLOY_USER's home directory
    local config_dir
    if [ -n "$DEPLOY_USER" ] && [ "$DEPLOY_USER" != "root" ]; then
        # Use deploy user's home directory
        if [ -d "/Users/$DEPLOY_USER" ]; then
            config_dir="/Users/$DEPLOY_USER/.open-ace"
        else
            config_dir="/home/$DEPLOY_USER/.open-ace"
        fi
    elif [ "$EUID" -eq 0 ]; then
        # Running as root without specific deploy user
        config_dir="/root/.open-ace"
    else
        # Running as regular user
        config_dir="$HOME/.open-ace"
    fi

    # Remove systemd service first
    print_info "Removing systemd service..."
    uninstall_systemd_service || true

    # Remove installation directory
    if [ -d "$target_path" ]; then
        print_info "Removing installation directory: $target_path"
        rm -rf "$target_path"
        print_success "Installation directory removed"
    else
        print_info "Installation directory not found: $target_path"
    fi

    # Remove config directory if requested
    if [ "$REMOVE_CONFIG" = "yes" ]; then
        if [ -d "$config_dir" ]; then
            print_info "Removing config directory: $config_dir"
            rm -rf "$config_dir"
            print_success "Config directory removed"
        else
            print_info "Config directory not found: $config_dir"
        fi
    else
        print_info "Preserving config directory: $config_dir"
    fi

    # Remove data if requested
    if [ "$REMOVE_DATA" = "yes" ]; then
        # Remove usage.db if exists in config dir
        if [ -f "$config_dir/usage.db" ] && [ "$REMOVE_CONFIG" != "yes" ]; then
            print_info "Removing usage.db..."
            rm -f "$config_dir/usage.db"
            print_success "usage.db removed"
        fi
    else
        print_info "Preserving data files"
    fi

    print_header "Local Uninstallation Complete!"
    if [ "$REMOVE_CONFIG" != "yes" ] || [ "$REMOVE_DATA" != "yes" ]; then
        echo ""
        print_info "Some files were preserved. To remove them manually:"
        [ "$REMOVE_CONFIG" != "yes" ] && echo "  rm -rf $config_dir"
        [ "$REMOVE_DATA" != "yes" ] && [ -f "$config_dir/usage.db" ] && echo "  rm -f $config_dir/usage.db"
    fi
    echo ""
}

# ============================================================================
# Remote Uninstallation
# ============================================================================

uninstall_remote() {
    print_header "Uninstalling from Remote Machine"

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

    # Remove systemd service first
    print_info "Removing systemd service on remote..."
    uninstall_systemd_service_remote "$remote" || true

    # Remove installation directory
    print_info "Removing installation directory on remote..."
    ssh "$remote" "
        if [ -d '$target_path' ]; then
            rm -rf '$target_path'
            echo 'INSTALLATION_REMOVED'
        else
            echo 'INSTALLATION_NOT_FOUND'
        fi
    " | while read -r line; do
        case "$line" in
            INSTALLATION_REMOVED)
                print_success "Installation directory removed"
                ;;
            INSTALLATION_NOT_FOUND)
                print_info "Installation directory not found"
                ;;
        esac
    done

    # Remove config directory if requested
    if [ "$REMOVE_CONFIG" = "yes" ]; then
        print_info "Removing config directory on remote..."
        ssh "$remote" "
            if [ -d '~/.open-ace' ]; then
                rm -rf ~/.open-ace
                echo 'CONFIG_REMOVED'
            else
                echo 'CONFIG_NOT_FOUND'
            fi
        " | while read -r line; do
            case "$line" in
                CONFIG_REMOVED)
                    print_success "Config directory removed"
                    ;;
                CONFIG_NOT_FOUND)
                    print_info "Config directory not found"
                    ;;
            esac
        done
    else
        print_info "Preserving config directory on remote"
    fi

    print_header "Remote Uninstallation Complete!"
    if [ "$REMOVE_CONFIG" != "yes" ]; then
        echo ""
        print_info "Config directory was preserved on remote. To remove it manually:"
        echo "  ssh $remote 'rm -rf ~/.open-ace'"
    fi
    echo ""
}

# ============================================================================
# Main
# ============================================================================

show_help() {
    echo "Open ACE - Uninstallation Script"
    echo ""
    echo "Usage: $0 [OPTIONS]"
    echo ""
    echo "Options:"
    echo "  --config FILE   Use configuration file"
    echo "  --help, -h      Show this help message"
    echo ""
    echo "Configuration File Format (shell script):"
    echo ""
    echo "  # Local uninstallation (uninstall from this machine)"
    echo "  DEPLOY_USER=\${USER}"
    echo "  DEPLOY_PATH=\$HOME/open-ace"
    echo ""
    echo "  # Remote uninstallation (uninstall via SSH)"
    echo "  DEPLOY_HOST=192.168.1.100"
    echo "  DEPLOY_USER=openclaw"
    echo "  DEPLOY_PATH=/home/openclaw/open-ace"
    echo ""
    echo "  # Data removal options"
    echo "  REMOVE_CONFIG=yes    # Remove config directory (~/.open-ace)"
    echo "  REMOVE_DATA=yes      # Remove data files (usage.db, logs)"
    echo ""
    echo "Examples:"
    echo "  $0                              # Interactive mode"
    echo "  $0 --config uninstall.conf      # Use config file"
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
print_header "Open ACE - Uninstaller"

if [ -n "$CONFIG_FILE" ]; then
    parse_config_file
else
    # Auto-detect from systemd service if no config file
    if [ "$EUID" -eq 0 ]; then
        detect_existing_installation
    fi
    interactive_config
fi

# Perform uninstallation
case $UNINSTALL_MODE in
    local)
        uninstall_local
        ;;
    remote)
        uninstall_remote
        ;;
    *)
        print_error "Invalid UNINSTALL_MODE: $UNINSTALL_MODE"
        exit 1
        ;;
esac