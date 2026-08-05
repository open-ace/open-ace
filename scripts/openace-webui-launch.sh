#!/bin/sh
# openace-webui-launch — Secure passthrough wrapper for launching qwen-code-webui
# with inline environment variables via /usr/bin/env.
#
# The sudoers rule restricts this wrapper to only be called with the webui_path
# as its first non-env argument, preventing arbitrary command execution.
#
# Usage (invoked by webui_manager.py via sudo):
#   sudo -u <user> /usr/local/bin/openace-webui-launch \
#       PATH=/usr/bin:/bin OPENAI_API_KEY=token ... \
#       /opt/openace/qwen-code-webui --port 3100 ...
#
# Relationship to openace-run-as:
# - openace-run-as: autonomous agent sandbox (env -i, restricted PATH)
# - openace-webui-launch: webui process launch with env vars (passthrough)
exec /usr/bin/env "$@"
