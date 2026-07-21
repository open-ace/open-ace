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
#   --ca-bundle PATH     PEM CA bundle for a private/self-signed server
#   --insecure-skip-tls-verify
#                        Disable TLS verification (dangerous, explicit only)
#   --skip-code-server   Skip code-server installation
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
SKIP_CODE_SERVER=false
CA_BUNDLE_PATH=""
INSECURE_SKIP_TLS_VERIFY=false

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
        --skip-code-server)
            SKIP_CODE_SERVER=true
            shift
            ;;
        --ca-bundle)
            CA_BUNDLE_PATH="$2"
            shift 2
            ;;
        --insecure-skip-tls-verify)
            INSECURE_SKIP_TLS_VERIFY=true
            shift
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

if [[ -n "$CA_BUNDLE_PATH" && "$INSECURE_SKIP_TLS_VERIFY" == "true" ]]; then
    log_error "--ca-bundle and --insecure-skip-tls-verify are mutually exclusive"
    exit 1
fi

if [[ -n "$CA_BUNDLE_PATH" && ! -r "$CA_BUNDLE_PATH" ]]; then
    log_error "CA bundle is not readable: $CA_BUNDLE_PATH"
    exit 1
fi
if [[ -n "$CA_BUNDLE_PATH" ]]; then
    CA_BUNDLE_PATH="$(cd "$(dirname "$CA_BUNDLE_PATH")" && pwd)/$(basename "$CA_BUNDLE_PATH")"
fi

SERVER_CURL_TLS_ARGS=()
if [[ -n "$CA_BUNDLE_PATH" ]]; then
    SERVER_CURL_TLS_ARGS+=(--cacert "$CA_BUNDLE_PATH")
elif [[ "$INSECURE_SKIP_TLS_VERIFY" == "true" ]]; then
    log_warn "TLS certificate verification is explicitly disabled"
    SERVER_CURL_TLS_ARGS+=(--insecure)
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

# Find the best Python 3 installation
# Priority: python3.12 > python3.11 > python3.10 > python3.9 > python3
find_python() {
    for py in python3.12 python3.11 python3.10 python3.9 python3; do
        if command -v "$py" &>/dev/null; then
            PYTHON_PATH=$(command -v "$py")
            # Verify it's actually working
            if "$PYTHON_PATH" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' 2>/dev/null; then
                echo "$PYTHON_PATH"
                return 0
            fi
        fi
    done
    return 1
}

if ! PYTHON_PATH=$(find_python); then
    log_error "Python 3.8+ is not installed. Please install Python 3.8+ first."
    exit 1
fi

PYTHON_VERSION=$("$PYTHON_PATH" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
log_success "Python ${PYTHON_VERSION} found at $PYTHON_PATH"

# Check pip
if ! "$PYTHON_PATH" -m pip --version &>/dev/null; then
    log_warn "pip not found. Installing pip..."
    "$PYTHON_PATH" -m ensurepip --upgrade 2>/dev/null || {
        log_error "Failed to install pip"
        exit 1
    }
fi
log_success "pip found"

# Step 1.5: Check for existing agent installation
log_info "Checking for existing agent installation..."

# Function to extract server_url from config.json (normalized, no trailing slash)
extract_server_url() {
    local config_path="$1"
    "$PYTHON_PATH" -c "
import json
try:
    with open('$config_path') as f:
        cfg = json.load(f)
    url = cfg.get('server_url', '')
    print(url.rstrip('/'))
except:
    print('')
" 2>/dev/null || echo ""
}

# Function to extract machine_id from config.json
extract_machine_id() {
    local config_path="$1"
    "$PYTHON_PATH" -c "
import json
try:
    with open('$config_path') as f:
        cfg = json.load(f)
    print(cfg.get('machine_id', ''))
except:
    print('')
" 2>/dev/null || echo ""
}

# Check all possible config locations
EXISTING_CONFIG_FOUND=false
EXISTING_SERVER=""
EXISTING_DIR=""
EXISTING_MACHINE_ID=""

# Check 1: Current specified directory
if [[ -f "${INSTALL_DIR}/config.json" ]]; then
    EXISTING_CONFIG_FOUND=true
    EXISTING_DIR="$INSTALL_DIR"
    EXISTING_SERVER=$(extract_server_url "${INSTALL_DIR}/config.json")
    EXISTING_MACHINE_ID=$(extract_machine_id "${INSTALL_DIR}/config.json")
fi

# Check 2: Default directory (if different from specified)
DEFAULT_DIR="$HOME/.open-ace-agent"
if [[ -f "${DEFAULT_DIR}/config.json" && "$DEFAULT_DIR" != "$INSTALL_DIR" ]]; then
    if [[ "$EXISTING_CONFIG_FOUND" == false ]]; then
        EXISTING_CONFIG_FOUND=true
        EXISTING_DIR="$DEFAULT_DIR"
        EXISTING_SERVER=$(extract_server_url "${DEFAULT_DIR}/config.json")
        EXISTING_MACHINE_ID=$(extract_machine_id "${DEFAULT_DIR}/config.json")
    fi
fi

# Check 3: systemd service file
SYSTEMD_SERVICE_FILE="/etc/systemd/system/open-ace-agent.service"
if [[ -f "$SYSTEMD_SERVICE_FILE" ]]; then
    SERVICE_DIR=$(grep "WorkingDirectory=" "$SYSTEMD_SERVICE_FILE" | cut -d= -f2 || echo "")
    if [[ -n "$SERVICE_DIR" && -f "${SERVICE_DIR}/config.json" ]]; then
        if [[ "$EXISTING_CONFIG_FOUND" == false ]]; then
            EXISTING_CONFIG_FOUND=true
            EXISTING_DIR="$SERVICE_DIR"
            EXISTING_SERVER=$(extract_server_url "${SERVICE_DIR}/config.json")
            EXISTING_MACHINE_ID=$(extract_machine_id "${SERVICE_DIR}/config.json")
        fi
    fi
fi

# Compare server URLs (both normalized)
NEW_URL="${SERVER_URL%/}"

if [[ "$EXISTING_CONFIG_FOUND" == true ]]; then
    log_info "Found existing agent installation at: $EXISTING_DIR"
    log_info "Existing server: $EXISTING_SERVER"

    if [[ "$EXISTING_SERVER" == "$NEW_URL" ]]; then
        # Same server: upgrade scenario
        log_info "Same server detected. Upgrading existing agent..."

        # Stop systemd service if exists
        if systemctl is-active open-ace-agent >/dev/null 2>&1; then
            log_warn "Stopping systemd service..."
            sudo systemctl stop open-ace-agent 2>/dev/null || true
            log_success "Systemd service stopped"
        fi

        # Kill processes by exact install directory match
        CURRENT_USER=$(whoami)
        pgrep -u "$CURRENT_USER" -f "python.*${EXISTING_DIR}/agent.py" 2>/dev/null | while read pid; do
            log_warn "Stopping agent process (PID: $pid)..."
            kill "$pid" 2>/dev/null || true
        done
        sleep 2

        # Force kill if still running
        pgrep -u "$CURRENT_USER" -f "python.*${EXISTING_DIR}/agent.py" 2>/dev/null | while read pid; do
            log_warn "Force killing stubborn process (PID: $pid)..."
            kill -9 "$pid" 2>/dev/null || true
        done

        # Clean up orphan processes (user-limited, verify it's open-ace-agent)
        pgrep -u "$CURRENT_USER" -f "python.*agent.py" 2>/dev/null | while read pid; do
            if ps -p "$pid" -o args= 2>/dev/null | grep -q "open-ace-agent"; then
                log_warn "Cleaning orphan agent process (PID: $pid)..."
                kill "$pid" 2>/dev/null || true
            fi
        done

        log_success "Existing agent stopped"

        # Preserve machine_id
        if [[ -n "$EXISTING_MACHINE_ID" ]]; then
            log_info "Preserving machine_id: $EXISTING_MACHINE_ID"
            # Will be used when generating config
        fi

    else
        # Different server: migration scenario - abort and prompt uninstall
        log_warn "Existing agent is configured for different server:"
        log_warn "  Current: $EXISTING_SERVER"
        log_warn "  New:     $NEW_URL"
        log_error ""
        log_error "Cannot proceed. Please uninstall the existing agent first:"
        log_error ""

        # Check if old server is available
        OLD_SERVER_AVAILABLE=false
        if curl -s -o /dev/null -w "%{http_code}" "${EXISTING_SERVER}/api/remote/agent/uninstall.sh" 2>/dev/null | grep -q "200"; then
            OLD_SERVER_AVAILABLE=true
            log_error "  curl -fsSL ${EXISTING_SERVER}/api/remote/agent/uninstall.sh | bash"
        else
            log_error "  # Old server is unavailable, use local uninstall:"
        fi

        # Check if local uninstall.sh exists
        if [[ -f "${EXISTING_DIR}/uninstall.sh" ]]; then
            log_error "  bash ${EXISTING_DIR}/uninstall.sh"
        fi

        # Manual uninstall instructions
        log_error ""
        log_error "  # Or manually:"
        log_error "  sudo systemctl stop open-ace-agent"
        log_error "  sudo systemctl disable open-ace-agent"
        log_error "  rm -rf ${EXISTING_DIR}"
        log_error ""
        log_error "Then re-run the install command for the new server."

        exit 1
    fi
fi

log_success "No conflicting installation found"

# Step 2: Create installation directory
log_info "Creating installation directory..."
mkdir -p "$INSTALL_DIR"
log_success "Directory created: $INSTALL_DIR"

# Step 3: Download agent files
log_info "Downloading agent files..."

AGENT_URL="${SERVER_URL}/api/remote/agent/files"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
AGENT_FILES=(
    agent.py
    config.py
    constants.py
    executor.py
    system_info.py
    requirements.txt
    terminal_menu.py
    terminal_server.py
    terminal_relay.py
    websocket_proxy.py
    session_sync.py
    openace_cli.py
    cli_settings.py
    zcode_app_server.py
    tls_config.py
    __init__.py
)

# If running from curl, download files; if running from source, copy
if [[ -f "${SCRIPT_DIR}/agent.py" ]]; then
    # Running from source directory
    log_info "Installing from source directory..."
    for file in "${AGENT_FILES[@]}"; do
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
    HTTP_CODE=$(curl -s "${SERVER_CURL_TLS_ARGS[@]}" -o /dev/null -w "%{http_code}" "${AGENT_URL}/agent.py" 2>/dev/null || echo "000")

    if [[ "$HTTP_CODE" == "200" ]]; then
        for file in "${AGENT_FILES[@]}"; do
            curl -fsSL "${SERVER_CURL_TLS_ARGS[@]}" "${AGENT_URL}/${file}" -o "${INSTALL_DIR}/${file}" 2>/dev/null || {
                log_warn "Could not download ${file}"
            }
        done
        # Download CLI adapters
        mkdir -p "${INSTALL_DIR}/cli_adapters"
        for file in __init__.py base.py qwen_code.py claude_code.py codex_cli.py codex_jsonl_parser.py openclaw.py usage_parser.py zcode.py; do
            curl -fsSL "${SERVER_CURL_TLS_ARGS[@]}" "${AGENT_URL}/cli_adapters/${file}" -o "${INSTALL_DIR}/cli_adapters/${file}" 2>/dev/null || {
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

# Install the user-facing Open ACE CLI wrapper for SSH shells.
log_info "Installing openace command..."
BIN_DIR="${OPENACE_BIN_DIR:-$HOME/.local/bin}"
mkdir -p "$BIN_DIR"
OPENACE_BIN="$BIN_DIR/openace"
cat > "$OPENACE_BIN" <<EOF
#!/usr/bin/env bash
exec "$PYTHON_PATH" "$INSTALL_DIR/openace_cli.py" "\$@"
EOF
chmod +x "$OPENACE_BIN"

if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    BASHRC="$HOME/.bashrc"
    MARKER="# Open ACE CLI path"
    if [[ ! -f "$BASHRC" ]] || ! grep -qF "$MARKER" "$BASHRC"; then
        {
            echo ""
            echo "$MARKER"
            echo "export PATH=\"$BIN_DIR:\$PATH\""
        } >> "$BASHRC"
    fi
    log_warn "$BIN_DIR is not in the current PATH. Restart your shell or run: export PATH=\"$BIN_DIR:\$PATH\""
fi
log_success "openace command installed: $OPENACE_BIN"

# Step 4: Install Python dependencies
log_info "Installing Python dependencies..."
if [[ -f "${INSTALL_DIR}/requirements.txt" ]]; then
    # Try different installation methods for externally-managed environments (PEP 668)
    # Method 1: --user (safest, installs to user directory)
    # Method 2: --break-system-packages (for Homebrew Python etc.)
    # Method 3: Standard pip install
    if ! "$PYTHON_PATH" -m pip install --user -q -r "${INSTALL_DIR}/requirements.txt" 2>/dev/null; then
        if ! "$PYTHON_PATH" -m pip install --break-system-packages -q -r "${INSTALL_DIR}/requirements.txt" 2>/dev/null; then
            "$PYTHON_PATH" -m pip install -q -r "${INSTALL_DIR}/requirements.txt" 2>/dev/null || {
                log_warn "Some dependencies may not have installed correctly"
            }
        fi
    fi
fi
log_success "Dependencies installed"

# Step 5: Optionally install CLI tool
if [[ -n "$INSTALL_CLI" ]]; then
    log_info "Installing CLI tool: $INSTALL_CLI..."

    # Check if npm is available, if not try to install Node.js
    if ! command -v npm &>/dev/null; then
        log_info "npm not found, attempting to install Node.js..."

        # Detect OS and install Node.js
        if [[ "$(uname)" == "Darwin" ]]; then
            # macOS - use Homebrew
            log_info "Detected macOS. Installing Node.js via Homebrew..."
            if command -v brew &>/dev/null; then
                brew install node
            else
                log_warn "Homebrew not found. Installing Homebrew first..."
                /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
                if command -v brew &>/dev/null; then
                    brew install node
                else
                    log_warn "Failed to install Homebrew. Please install Node.js manually:"
                    log_warn "  1. Install Homebrew: /bin/bash -c \"\$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)\""
                    log_warn "  2. Install Node.js: brew install node"
                    log_warn "  3. Install CLI: npm install -g @qwen-code/qwen-code@latest"
                fi
            fi
        elif [ -f /etc/os-release ]; then
            . /etc/os-release
            case "$ID" in
                rhel|centos|fedora|rocky|almalinux|ol)
                    # RHEL/CentOS/Rocky/Alma - use nodesource RPM repo
                    log_info "Installing Node.js via NodeSource (RPM)..."
                    if command -v curl &>/dev/null; then
                        curl -fsSL https://rpm.nodesource.com/setup_20.x | bash -
                    elif command -v wget &>/dev/null; then
                        wget -qO- https://rpm.nodesource.com/setup_20.x | bash -
                    fi
                    if command -v yum &>/dev/null; then
                        yum install -y nodejs
                    elif command -v dnf &>/dev/null; then
                        dnf install -y nodejs
                    fi
                    ;;
                debian|ubuntu|linuxmint|pop)
                    # Debian/Ubuntu - use nodesource APT repo
                    log_info "Installing Node.js via NodeSource (APT)..."
                    if command -v curl &>/dev/null; then
                        curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
                    elif command -v wget &>/dev/null; then
                        wget -qO- https://deb.nodesource.com/setup_20.x | bash -
                    fi
                    apt-get install -y nodejs
                    ;;
                alpine)
                    # Alpine Linux - use apk
                    log_info "Installing Node.js via apk..."
                    apk add --no-cache nodejs npm
                    ;;
                arch|manjaro)
                    # Arch Linux - use pacman
                    log_info "Installing Node.js via pacman..."
                    pacman -Sy --noconfirm nodejs npm
                    ;;
                sles|suse)
                    # SUSE - use zypper
                    log_info "Installing Node.js via zypper..."
                    zypper install -y nodejs20
                    ;;
                *)
                    log_warn "Unsupported OS: $ID. Cannot auto-install Node.js."
                    log_warn "Please install Node.js manually and then run:"
                    log_warn "  npm install -g @qwen-code/qwen-code@latest"
                    ;;
            esac
        else
            log_warn "Cannot detect OS. Cannot auto-install Node.js."
            log_warn "Please install Node.js manually and then run:"
            log_warn "  npm install -g @qwen-code/qwen-code@latest"
        fi
    fi

    # Now try to install the CLI tool
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
        log_warn "npm still not available after attempting Node.js installation."
        log_warn "Please install Node.js manually and then run:"
        log_warn "  npm install -g @qwen-code/qwen-code@latest"
    fi
fi

# Step 5.5: Install git and code-server
log_info "Checking for git and code-server..."

# Install git if not present
if command -v git &>/dev/null; then
    log_success "git already installed: $(git --version 2>/dev/null)"
else
    log_info "git not found, attempting to install..."
    GIT_INSTALLED=false
    if [[ "$(uname)" == "Darwin" ]]; then
        if command -v brew &>/dev/null; then
            brew install git && GIT_INSTALLED=true
        else
            log_warn "Homebrew not found. Please install git manually: xcode-select --install"
        fi
    elif [ -f /etc/os-release ]; then
        . /etc/os-release
        case "$ID" in
            debian|ubuntu|linuxmint|pop)
                apt-get install -y git && GIT_INSTALLED=true
                ;;
            rhel|centos|fedora|rocky|almalinux|ol)
                if command -v dnf &>/dev/null; then
                    dnf install -y git && GIT_INSTALLED=true
                else
                    yum install -y git && GIT_INSTALLED=true
                fi
                ;;
            alpine)
                apk add --no-cache git && GIT_INSTALLED=true
                ;;
            arch|manjaro)
                pacman -Sy --noconfirm git && GIT_INSTALLED=true
                ;;
            sles|suse)
                zypper install -y git && GIT_INSTALLED=true
                ;;
            *)
                log_warn "Unsupported OS: $ID. Cannot auto-install git."
                ;;
        esac
    fi
    if [[ "$GIT_INSTALLED" == "true" ]]; then
        log_success "git installed: $(git --version 2>/dev/null)"
    else
        log_warn "Failed to install git. Remote workspace will be missing file changes panel."
        log_warn "Please install git manually."
    fi
fi

# Install code-server if not present
if [[ "$SKIP_CODE_SERVER" == "true" ]]; then
    log_info "Skipping code-server installation (--skip-code-server)"
elif command -v code-server &>/dev/null; then
    log_success "code-server already installed: $(code-server --version 2>/dev/null | head -1)"
else
    log_info "code-server not found, attempting to install..."
    log_info "This may take a few minutes, please wait..."
    CS_INSTALLED=false
    if [[ "$(uname)" == "Darwin" ]] || [[ "$(uname)" == "Linux" ]]; then
        CS_INSTALL_SCRIPT=$(mktemp)
        CS_DOWNLOAD_OK=false

        # Download install script with timeout
        if command -v curl &>/dev/null; then
            curl -fsSL --connect-timeout 10 --max-time 120 https://code-server.dev/install.sh -o "$CS_INSTALL_SCRIPT" && CS_DOWNLOAD_OK=true
        elif command -v wget &>/dev/null; then
            wget --timeout=120 -qO "$CS_INSTALL_SCRIPT" https://code-server.dev/install.sh && CS_DOWNLOAD_OK=true
        fi

        if [[ "$CS_DOWNLOAD_OK" == "true" ]]; then
            # Run install with timeout (300s) and progress dots
            log_info "Running code-server installer..."
            _cs_killed=false
            sh "$CS_INSTALL_SCRIPT" &
            CS_PID=$!
            _CS_START=$SECONDS
            while kill -0 "$CS_PID" 2>/dev/null; do
                if [[ $(( SECONDS - _CS_START )) -gt 300 ]]; then
                    kill "$CS_PID" 2>/dev/null
                    _cs_killed=true
                    log_warn "code-server install timed out after 300s"
                    break
                fi
                sleep 5
                echo -n "."
            done
            echo ""
            if [[ "$_cs_killed" == "false" ]] && wait "$CS_PID" 2>/dev/null; then
                CS_INSTALLED=true
            fi
        else
            log_warn "Failed to download code-server install script (network timeout or unavailable)"
        fi
        rm -f "$CS_INSTALL_SCRIPT"
    fi
    if [[ "$CS_INSTALLED" == "true" ]]; then
        log_success "code-server installed: $(code-server --version 2>/dev/null | head -1)"
    else
        log_warn "Failed to install code-server. Remote workspace will be missing VSCode editor."
        log_warn "You can install it manually later: https://coder.com/docs/code-server/latest/install"
    fi
fi

# Step 6: Generate machine ID and save config
log_info "Generating configuration..."

# Use existing machine_id if upgrading (same server), otherwise generate new one
if [[ -n "$EXISTING_MACHINE_ID" && "$EXISTING_SERVER" == "$NEW_URL" ]]; then
    MACHINE_ID="$EXISTING_MACHINE_ID"
    log_info "Using preserved machine_id: $MACHINE_ID"
else
    MACHINE_ID=$("$PYTHON_PATH" -c "import uuid; print(uuid.uuid4())")
    log_info "Generated new machine_id: $MACHINE_ID"
fi

CA_BUNDLE_JSON=$("$PYTHON_PATH" -c 'import json,sys; print(json.dumps(sys.argv[1] or None))' "$CA_BUNDLE_PATH")
cat > "${INSTALL_DIR}/config.json" << EOF
{
    "server_url": "${SERVER_URL}",
    "machine_id": "${MACHINE_ID}",
    "machine_name": "${MACHINE_NAME}",
    "registration_token": "${REGISTRATION_TOKEN}",
    "cli_tool": "${INSTALL_CLI}",
    "python_path": "${PYTHON_PATH}",
    "heartbeat_interval": 60,
    "reconnect_backoff_max": 60,
    "skip_ssl_verify": ${INSECURE_SKIP_TLS_VERIFY},
    "ca_bundle_path": ${CA_BUNDLE_JSON}
}
EOF

log_success "Configuration saved"

# Step 7: Register with server
log_info "Registering with Open ACE server..."

OS_TYPE=$(uname -s 2>/dev/null || echo "unknown")
OS_VERSION=$(uname -r 2>/dev/null || echo "unknown")

# Get local IP address (prefer non-loopback)
LOCAL_IP=$("$PYTHON_PATH" -c "
import socket
try:
    hostname = socket.gethostname()
    ip = socket.gethostbyname(hostname)
    if ip != '127.0.0.1':
        print(ip)
    else:
        # Fallback: try to get IP from a socket connection
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(('8.8.8.8', 80))
        print(s.getsockname()[0])
        s.close()
except:
    print('127.0.0.1')
" 2>/dev/null || echo "127.0.0.1")

CAPABILITIES=$("$PYTHON_PATH" -c "
import json, os, platform, shutil
caps = {
    'os': platform.system().lower(),
    'os_version': platform.release(),
    'cpu_cores': os.cpu_count() or 0,
    'python_version': platform.python_version(),
}
try:
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
for cli in ['qwen', 'claude', 'openclaw']:
    caps[f'{cli}_installed'] = shutil.which(cli) is not None
# Check git and code-server
caps['has_git'] = shutil.which('git') is not None
caps['has_code_server'] = shutil.which('code-server') is not None
print(json.dumps(caps))
" 2>/dev/null || echo "{}")

REGISTER_RESPONSE=$(curl -s "${SERVER_CURL_TLS_ARGS[@]}" -X POST "${SERVER_URL}/api/remote/agent/register" \
    -H "Content-Type: application/json" \
    -d "{
        \"registration_token\": \"${REGISTRATION_TOKEN}\",
        \"machine_id\": \"${MACHINE_ID}\",
        \"machine_name\": \"${MACHINE_NAME}\",
        \"hostname\": \"$(hostname 2>/dev/null || echo 'unknown')\",
        \"os_type\": \"${OS_TYPE}\",
        \"os_version\": \"${OS_VERSION}\",
        \"capabilities\": ${CAPABILITIES},
        \"agent_version\": \"${AGENT_VERSION}\",
        \"ip_address\": \"${LOCAL_IP}\"
    }" 2>/dev/null || echo '{"success": false}')

if echo "$REGISTER_RESPONSE" | "$PYTHON_PATH" -c "import sys,json; d=json.load(sys.stdin); sys.exit(0 if d.get('success') else 1)" 2>/dev/null; then
    log_success "Machine registered successfully!"

    # Extract agent_token from registration response and save to config
    AGENT_TOKEN=$(echo "$REGISTER_RESPONSE" | "$PYTHON_PATH" -c "
import sys, json
try:
    d = json.load(sys.stdin)
    m = d.get('machine', {})
    token = m.get('agent_token', '')
    print(token)
except Exception as e:
    print(f'Warning: Failed to parse agent_token from response: {e}', file=sys.stderr)
    print('')
")

    if [ -n "$AGENT_TOKEN" ]; then
        if "$PYTHON_PATH" -c "
import json, sys
config_path = sys.argv[1]
token = sys.argv[2]
with open(config_path) as f:
    cfg = json.load(f)
cfg['agent_token'] = token
with open(config_path, 'w') as f:
    json.dump(cfg, f, indent=2)
" "${INSTALL_DIR}/config.json" "$AGENT_TOKEN"; then
            log_success "Agent token saved to configuration"
        else
            log_error "Failed to save agent_token to config (check permissions)"
        fi
    else
        log_info "No agent_token in response (server may not support token auth yet)"
    fi
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
AGENT_INSECURE_ARG=""
LAUNCHD_INSECURE_ARG=""
if [[ "$INSECURE_SKIP_TLS_VERIFY" == "true" ]]; then
    AGENT_INSECURE_ARG=" --insecure-skip-tls-verify"
    LAUNCHD_INSECURE_ARG="        <string>--insecure-skip-tls-verify</string>"
fi

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
ExecStart=${PYTHON_PATH} ${INSTALL_DIR}/agent.py${AGENT_INSECURE_ARG}
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
        <string>${PYTHON_PATH}</string>
        <string>${INSTALL_DIR}/agent.py</string>
${LAUNCHD_INSECURE_ARG}
    </array>
    <key>WorkingDirectory</key>
    <string>${INSTALL_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardInPath</key>
    <string>/dev/null</string>
    <key>StandardOutPath</key>
    <string>${INSTALL_DIR}/agent.log</string>
    <key>StandardErrorPath</key>
    <string>${INSTALL_DIR}/agent-error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONUNBUFFERED</key>
        <string>1</string>
        <key>PATH</key>
        <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin:$HOME/.npm-global/bin</string>
    </dict>
</dict>
</plist>
EOF
        launchctl load "$PLIST_FILE" 2>/dev/null
        log_success "Installed as launchd service"
        ;;
    *)
        log_warn "No init system detected. Run manually: python3 ${INSTALL_DIR}/agent.py${AGENT_INSECURE_ARG}"
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
