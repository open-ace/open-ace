#!/bin/bash
#
# Open ACE Remote Sync - Remote Deployment Script
#
# Deploy to a remote machine via SSH
#
# Usage: ./deploy-remote.sh [options]
#
# Options:
#   --host HOST         Remote host (user@hostname or hostname)
#   --server URL        Central server URL (required)
#   --auth-key KEY      Authentication key (required)
#   --hostname NAME     Remote machine's hostname (default: remote hostname)
#   --interval SECONDS  Sync interval (default: 300)
#   --user USER         User to run service as on remote (default: SSH user)
#   --install-dir DIR   Installation directory (default: ~/upload-to-central)
#   --uninstall         Remove the service and files from remote
#

set -e

# Default values
REMOTE_HOST=""
SERVER_URL=""
AUTH_KEY=""
HOSTNAME=""
INTERVAL=300
REMOTE_USER=""
INSTALL_DIR="~/upload-to-central"
UNINSTALL=false

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --host)
            REMOTE_HOST="$2"
            shift 2
            ;;
        --server)
            SERVER_URL="$2"
            shift 2
            ;;
        --auth-key)
            AUTH_KEY="$2"
            shift 2
            ;;
        --hostname)
            HOSTNAME="$2"
            shift 2
            ;;
        --interval)
            INTERVAL="$2"
            shift 2
            ;;
        --user)
            REMOTE_USER="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --uninstall)
            UNINSTALL=true
            shift
            ;;
        --help)
            head -20 "$0" | tail -15
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            exit 1
            ;;
    esac
done

# Validate required parameters
if [ -z "$REMOTE_HOST" ]; then
    echo "Error: --host is required"
    exit 1
fi

# Extract user from host if specified (user@host)
if [[ "$REMOTE_HOST" == *"@"* ]]; then
    SSH_USER="${REMOTE_HOST%@*}"
    SSH_HOST="${REMOTE_HOST#*@}"
else
    SSH_USER="${REMOTE_USER:-$(whoami)}"
    SSH_HOST="$REMOTE_HOST"
fi

# Set default hostname if not specified
if [ -z "$HOSTNAME" ]; then
    HOSTNAME="$SSH_HOST"
fi

# Get script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "=== Deploying Open ACE Remote Sync ==="
echo "  Remote host: $SSH_USER@$SSH_HOST"
echo "  Server: ${SERVER_URL:-<from config>}"
echo "  Hostname: $HOSTNAME"
echo "  Interval: ${INTERVAL}s"
echo "  Install dir: $INSTALL_DIR"
echo

# Uninstall mode
if $UNINSTALL; then
    echo "Uninstalling from remote..."
    ssh "$SSH_USER@$SSH_HOST" "sudo systemctl stop upload-to-central 2>/dev/null || true"
    ssh "$SSH_USER@$SSH_HOST" "sudo systemctl disable upload-to-central 2>/dev/null || true"
    ssh "$SSH_USER@$SSH_HOST" "sudo rm -f /etc/systemd/system/upload-to-central.service"
    ssh "$SSH_USER@$SSH_HOST" "sudo systemctl daemon-reload"
    ssh "$SSH_USER@$SSH_HOST" "rm -rf $INSTALL_DIR"
    echo "Uninstall complete!"
    exit 0
fi

# Validate required parameters for install
if [ -z "$SERVER_URL" ]; then
    echo "Error: --server is required"
    exit 1
fi

if [ -z "$AUTH_KEY" ]; then
    echo "Error: --auth-key is required"
    exit 1
fi

# Copy deploy script to remote
echo "Copying deployment script..."
scp "$SCRIPT_DIR/deploy.sh" "$SSH_USER@$SSH_HOST:/tmp/deploy-ace-sync.sh"

# Copy fetch scripts
echo "Copying fetch scripts..."
FETCH_DIR="$SCRIPT_DIR/../../scripts"
if [ -d "$FETCH_DIR" ]; then
    scp "$FETCH_DIR"/fetch_*.py "$SSH_USER@$SSH_HOST:/tmp/" 2>/dev/null || echo "  (No fetch scripts found, skipping)"
    
    # Copy shared modules
    echo "Copying shared modules..."
    ssh "$SSH_USER@$SSH_HOST" "mkdir -p /tmp/shared"
    scp "$FETCH_DIR"/shared/*.py "$SSH_USER@$SSH_HOST:/tmp/shared/"
else
    echo "  (Scripts directory not found, skipping)"
fi

# Run deployment on remote
echo "Running deployment on remote..."
ssh "$SSH_USER@$SSH_HOST" << EOF
    chmod +x /tmp/deploy-ace-sync.sh
    
    # Run deployment (requires sudo for systemd)
    /tmp/deploy-ace-sync.sh \
        --server "$SERVER_URL" \
        --auth-key "$AUTH_KEY" \
        --hostname "$HOSTNAME" \
        --interval "$INTERVAL" \
        --user "$SSH_USER" \
        --install-dir "$INSTALL_DIR" \
        --fetch-scripts /tmp
    
    # Cleanup
    rm -f /tmp/deploy-ace-sync.sh /tmp/fetch_*.py
EOF

# Get absolute path for install directory
ABS_INSTALL_DIR=$(ssh "$SSH_USER@$SSH_HOST" "cd $INSTALL_DIR && pwd")

# Install systemd service with root
echo "Installing systemd service..."
ssh root@$SSH_HOST << EOF
    cat > /etc/systemd/system/upload-to-central.service << SERVICE
[Unit]
Description=Open ACE Remote Sync
After=network.target

[Service]
Type=simple
User=$SSH_USER
WorkingDirectory=$ABS_INSTALL_DIR
Environment=DATABASE_URL=sqlite:///$ABS_INSTALL_DIR/ace.db
Environment=PYTHONUNBUFFERED=1
ExecStart=/usr/bin/python3 $ABS_INSTALL_DIR/upload_to_server.py --daemon --interval $INTERVAL
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICE

    systemctl daemon-reload
    systemctl enable upload-to-central
    systemctl restart upload-to-central
    systemctl status upload-to-central --no-pager
EOF

echo
echo "=== Remote Deployment Complete ==="