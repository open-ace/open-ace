#!/usr/bin/env bash
# Open ACE Remote Agent - Uninstall Script (Linux/macOS)

set -euo pipefail
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }

INSTALL_DIR="$HOME/.open-ace-agent"
KEEP_CONFIG=false; KEEP_DEPS=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --keep-config) KEEP_CONFIG=true; shift ;;
        --keep-deps) KEEP_DEPS=true; shift ;;
        --help) echo "Options: --keep-config --keep-deps"; exit 0 ;;
        *) shift ;;
    esac
done

log_info "Uninstalling Open ACE Remote Agent..."

# Stop service
if [[ "$(uname)" == "Darwin" ]]; then
    PLIST="$HOME/Library/LaunchAgents/com.open-ace.agent.plist"
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
    log_success "Stopped launchd service"
elif command -v systemctl &>/dev/null; then
    sudo systemctl stop open-ace-agent 2>/dev/null || true
    sudo systemctl disable open-ace-agent 2>/dev/null || true
    sudo rm -f /etc/systemd/system/open-ace-agent.service
    sudo systemctl daemon-reload
    log_success "Stopped systemd service"
else
    pkill -f "agent.py" 2>/dev/null || true
    log_success "Killed agent process"
fi

# Get Python path
PYTHON_PATH="python3"
if [[ -f "${INSTALL_DIR}/config.json" ]]; then
    PYTHON_PATH=$(grep -o '"python_path": *"[^"]*"' "${INSTALL_DIR}/config.json" 2>/dev/null | cut -d'"' -f4 || echo "python3")
fi

# Remove deps
if [[ "$KEEP_DEPS" == "false" ]]; then
    log_info "Removing Python dependencies..."
    for pkg in requests websocket-client; do
        "$PYTHON_PATH" -m pip uninstall -q -y "$pkg" 2>/dev/null || true
    done
    log_success "Dependencies removed"
fi

# Remove files
if [[ "$KEEP_CONFIG" == "true" ]]; then
    rm -f "${INSTALL_DIR}"/agent.py "${INSTALL_DIR}"/config.py "${INSTALL_DIR}"/executor.py \
          "${INSTALL_DIR}"/system_info.py "${INSTALL_DIR}"/requirements.txt "${INSTALL_DIR}"/*.log 2>/dev/null || true
    rm -rf "${INSTALL_DIR}/cli_adapters" 2>/dev/null || true
    log_success "Agent files removed (config preserved)"
else
    rm -rf "$INSTALL_DIR"
    log_success "Installation directory removed"
fi

echo ""
log_success "Uninstall complete!"
log_info "Delete machine record from server via web UI"
