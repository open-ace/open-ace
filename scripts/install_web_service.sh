#!/bin/bash
# AI Token Usage - Install/Uninstall Web Server LaunchAgent

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_DIR="$(dirname "$SCRIPT_DIR")"
PLIST_FILE="$SCRIPT_DIR/com.ai-token-analyzer.web.plist"
LAUNCH_DIR="$HOME/Library/LaunchAgents"

usage() {
    echo "Usage: $0 {install|uninstall}"
    echo ""
    echo "Commands:"
    echo "  install  - Install the launch agent to auto-start web server"
    echo "  uninstall - Remove the launch agent"
    exit 1
}

install_agent() {
    # Check if plist file exists
    if [ ! -f "$PLIST_FILE" ]; then
        echo "ERROR: Plist file not found: $PLIST_FILE"
        exit 1
    fi

    # Create LaunchAgents directory if needed
    mkdir -p "$LAUNCH_DIR"

    # Copy plist to LaunchAgents
    cp "$PLIST_FILE" "$LAUNCH_DIR/"
    echo "Installed launch agent to $LAUNCH_DIR/"

    # Load the agent
    launchctl load "$LAUNCH_DIR/$PLIST_FILE"
    echo "Launch agent loaded."

    echo ""
    echo "Done! The web server will now start automatically when you log in."
    echo "To start immediately without logging out: launchctl bootstrap gui/$(id -u)/$LAUNCH_DIR $PLIST_FILE"
}

uninstall_agent() {
    if [ ! -f "$LAUNCH_DIR/$PLIST_FILE" ]; then
        echo "Launch agent not installed."
        exit 0
    fi

    # Unload the agent
    launchctl unload "$LAUNCH_DIR/$PLIST_FILE"
    echo "Launch agent unloaded."

    # Remove the plist file
    rm "$LAUNCH_DIR/$PLIST_FILE"
    echo "Launch agent removed."
}

# Main
case "${1:-}" in
    install)
        install_agent
        ;;
    uninstall)
        uninstall_agent
        ;;
    *)
        usage
        ;;
esac
