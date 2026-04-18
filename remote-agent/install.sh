#!/usr/bin/env bash
#
# Open ACE Remote Agent - Install Script
#
# Usage:
#   curl -fsSL https://<server>/api/remote/agent/install.sh | bash -s -- --server https://<server> --token <registration-token>
#
# Options:
#   --server URL         Open ACE server URL (required)
#   --token TOKEN        Registration token from admin (required)
#   --name NAME          Machine display name (default: hostname)
#   --install-cli TOOL   Install a CLI tool: qwen-code-cli, claude-code (default: qwen-code-cli)
#   --dir DIR            Installation directory (default: ~/.open-ace-agent)
#   --help               Show this help
#

set -euo pipefail

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }
log_success() { echo -e "${GREEN}[OK]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_error() { echo -e "${RED}[ERROR]${NC} $1"; }

# Defaults
SERVER_URL=""
REGISTRATION_TOKEN=""
MACHINE_NAME=$(hostname)
INSTALL_CLI="qwen-code-cli"
INSTALL_DIR="$HOME/.open-ace-agent"
AGENT_VERSION="1.0.0"

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --server)
            SERVER_URL="$2"
            shift 2
            ;;
        --token)
            REGISTRATION_TOKEN="$2"
            shift 2
            ;;
        --name)
            MACHINE_NAME="$2"
            shift 2
            ;;
        --install-cli)
            INSTALL_CLI="$2"
            shift 2
            ;;
        --dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --help)
            head -20 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *)
            log_error "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [[ -z "$SERVER_URL" ]]; then
    log_error "--server is required"
    exit 1
fi

if [[ -z "$REGISTRATION_TOKEN" ]]; then
    log_error "--token is required"
    exit 1
fi

# Remove trailing slash from server URL
SERVER_URL="${SERVER_URL%/}"

log_info "Open ACE Remote Agent Installer"
log_info "================================"
log_info "Server: $SERVER_URL"
log_info "Machine name: $MACHINE_NAME"
log_info "Install CLI: $INSTALL_CLI"
log_info "Install dir: $INSTALL_DIR"
echo ""

# Step 1: Check prerequisites
log_info "Checking prerequisites..."

# Check Python 3
if ! command -v python3 &>/dev/null; then
    log_error "Python 3 is not installed. Please install Python 3.8+ first."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log_success "Python $PYTHON_VERSION found"

# Check pip
if ! python3 -m pip --version &>/dev/null; then
    log_warn "pip not found. Installing pip..."
    python3 -m ensurepip --upgrade 2>/dev/null || {
        log_error "Failed to install pip"
        exit 1
    }
fi
log_success "pip found"

# Step 2: Create installation directory
log_info "Creating installation directory..."
mkdir -p "$INSTALL_DIR"
log_success "Directory created: $INSTALL_DIR"

# Step 3: Download agent files
log_info "Downloading agent files..."

AGENT_URL="${SERVER_URL}/api/remote/agent/files"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"

# If running from curl, download files; if running from source, copy
if [[ -f "${SCRIPT_DIR}/agent.py" ]]; then
    # Running from source directory
    log_info "Installing from source directory..."
    for file in agent.py config.py executor.py system_info.py requirements.txt; do
        if [[ -f "${SCRIPT_DIR}/$file" ]]; then
            cp "${SCRIPT_DIR}/$file" "$INSTALL_DIR/"
        fi
    done
    if [[ -d "${SCRIPT_DIR}/cli_adapters" ]]; then
        mkdir -p "$INSTALL_DIR/cli_adapters"
        cp -r "${SCRIPT_DIR}/cli_adapters/"* "$INSTALL_DIR/cli_adapters/"
    fi
else
    # Download from server
    log_info "Downloading from server..."
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" "${AGENT_URL}/agent.py" 2>/dev/null || echo "000")

    if [[ "$HTTP_CODE" == "200" ]]; then
        for file in agent.py config.py executor.py system_info.py requirements.txt; do
            curl -fsSL "${AGENT_URL}/${file}" -o "${INSTALL_DIR}/${file}" 2>/dev/null || {
                log_warn "Could not download ${file}"
            }
        done
        # Download CLI adapters
        mkdir -p "${INSTALL_DIR}/cli_adapters"
        for file in __init__.py base.py qwen_code.py claude_code.py openclaw.py; do
            curl -fsSL "${AGENT_URL}/cli_adapters/${file}" -o "${INSTALL_DIR}/cli_adapters/${file}" 2>/dev/null || {
                log_warn "Could not download cli_adapters/${file}"
            }
        done
    else
        log_warn "Cannot download from server. Creating minimal agent..."
        # Create a minimal agent that will be updated on first connection
    fi
fi

# Create __init__.py for the package
touch "${INSTALL_DIR}/__init__.py"

log_success "Agent files installed"

# Step 4: Install Python dependencies
log_info "Installing Python dependencies..."
if [[ -f "${INSTALL_DIR}/requirements.txt" ]]; then
    python3 -m pip install -q -r "${INSTALL_DIR}/requirements.txt" 2>/dev/null || {
        log_warn "Some dependencies may not have installed correctly"
    }
fi
log_success "Dependencies installed"

# Step 5: Optionally install CLI tool
if [[ -n "$INSTALL_CLI" ]]; then
    log_info "Installing CLI tool: $INSTALL_CLI..."
    if command -v npm &>/dev/null; then
        case "$INSTALL_CLI" in
            qwen-code-cli)
                npm install -g @qwen-code/qwen-code@latest 2>/dev/null && \
                    log_success "qwen-code-cli installed" || \
                    log_warn "Failed to install qwen-code-cli. You can install it manually later."
                ;;
            claude-code)
                npm install -g @anthropic-ai/claude-code@latest 2>/dev/null && \
                    log_success "Claude Code installed" || \
                    log_warn "Failed to install Claude Code. You can install it manually later."
                ;;
            *)
                log_warn "Unknown CLI tool: $INSTALL_CLI. Skipping."
                ;;
        esac
    else
        log_warn "npm not found. Skipping CLI installation. Install it manually."
    fi
fi

# Step 6: Generate machine ID and save config
log_info "Generating configuration..."
MACHINE_ID=$(python3 -c "import uuid; print(uuid.uuid4())")

cat > "${INSTALL_DIR}/config.json" << EOF
{
    "server_url": "${SERVER_URL}",
    "machine_id": "${MACHINE_ID}",
    "machine_name": "${MACHINE_NAME}",
    "registration_token": "${REGISTRATION_TOKEN}",
    "cli_tool": "${INSTALL_CLI}",
    "heartbeat_interval": 60,
    "reconnect_backoff_max": 60
}
EOF

log_success "Configuration saved"

# Step 7: Register with server
log_info "Registering with Open ACE server..."

OS_TYPE=$(uname -s 2>/dev/null | tr '[:upper:]' '[:lower:]' || echo "unknown")
OS_VERSION=$(uname -r 2>/dev/null || echo "unknown")

CAPABILITIES=$(python3 -c "
import json, os, platform
caps = {
    'os': platform.system().lower(),
    'os_version': platform.release(),
    'cpu_cores': os.cpu_count() or 0,
    'python_version': platform.python_version(),
}
try:
    import shutil
    total, used, free = shutil.disk_usage('/')
    caps['disk_total_gb'] = round(total / (1024**3), 1)
    caps['disk_free_gb'] = round(free / (1024**3), 1)
except:
    pass
try:
    caps['memory_gb'] = round(os.sysconf('SC_PAGE_SIZE') * os.sysconf('SC_PHYS_PAGES') / (1024**3), 1)
except:
    pass
# Check installed CLIs
for cli in ['qwen-code', 'claude', 'openclaw']:
    caps[f'{cli}_installed'] = shutil.which(cli) is not None
print(json.dumps(caps))
" 2>/dev/null || echo "{}")

REGISTER_RESPONSE=$(curl -s -X POST "${SERVER_URL}/api/remote/agent/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"registration_token\": \"${REGISTRATION_TOKEN}\",
        \"machine_id\": \"${MACHINE_ID}\",
        \"machine_name\": \"${MACHINE_NAME}\",
        \"hostname\": \"$(hostname 2>/dev/null || echo 'unknown')\",
        \"os_type\": \"${OS_TYPE}\",
        \"os_version\": \"${OS_VERSION}\",
        \"capabilities\": ${CAPABILITIES},
        \"agent_version\": \"${AGENT_VERSION}\"
    }" 2>/dev/null || echo '{"success": false}')

if echo "$REGISTER_RESPONSE" | python3 -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" 2>/dev/null; then
    log_success "Machine registered successfully!"
else
    log_error "Registration failed. Response: $REGISTER_RESPONSE"
    log_error "Please check your registration token and server URL."
    exit 1
fi

# Step 8: Install as system service
log_info "Installing as system service..."

detect_init_system() {
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "launchd"
    elif command -v systemctl &>/dev/null; then
        echo "systemd"
    else
        echo "none"
    fi
}

INIT_SYSTEM=$(detect_init_system)

case "$INIT_SYSTEM" in
    systemd)
        SERVICE_FILE="/etc/systemd/system/open-ace-agent.service"
        sudo tee "$SERVICE_FILE" > /dev/null << EOF
[Unit]
Description=Open ACE Remote Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${INSTALL_DIR}
ExecStart=$(which python3) ${INSTALL_DIR}/agent.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
        sudo systemctl daemon-reload
        sudo systemctl enable open-ace-agent
        sudo systemctl start open-ace-agent
        log_success "Installed as systemd service"
        ;;
    launchd)
        PLIST_FILE="$HOME/Library/LaunchAgents/com.open-ace.agent.plist"
        cat > "$PLIST_FILE" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.open-ace.agent</string>
    <key>ProgramArguments</key>
    <array>
        <string>$(which python3)</string>
        <string>${INSTALL_DIR}/agent.py</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/agent.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/agent-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
    </dict>
</dict>
</plist>
EOF
        launchctl load "$PLIST_FILE" 2>/dev/null
        log_success "Installed as launchd service"
        ;;
    *)
        log_warn "No init system detected. Run manually: python3 ${INSTALL_DIR}/agent.py"
        ;;
esac

echo ""
log_success "============================================"
log_success "Open ACE Remote Agent installed successfully!"
log_success "============================================"
echo ""
log_info "Machine ID: $MACHINE_ID"
log_info "Config: ${INSTALL_DIR}/config.json"
log_info "Logs: ${INSTALL_DIR}/agent.log"
echo ""
log_info "To view logs: tail -f ${INSTALL_DIR}/agent.log"
