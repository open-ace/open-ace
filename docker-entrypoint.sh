#!/bin/bash
# Docker entrypoint script for Open ACE
# Handles database initialization and multi-user workspace setup

set -e

# ============================================================================
# 0. Configuration Validation and Logging (Issue #2242)
# ============================================================================
# Validates configuration consistency and logs results for debugging.
# Outputs a summary to stdout and writes detailed results to a log file.

CONFIG_CHECK_LOG="/tmp/config-check.log"

log_config_check() {
    local status="$1"
    local message="$2"
    local timestamp
    timestamp=$(date '+%Y-%m-%d %H:%M:%S')

    # Append to log file
    mkdir -p "$(dirname "$CONFIG_CHECK_LOG")" 2>/dev/null || true
    echo "[$timestamp] $status: $message" >> "$CONFIG_CHECK_LOG" 2>/dev/null || true
}

output_config_summary() {
    local mode
    local uid
    uid=$(id -u)

    # Determine mode
    if [ "${WORKSPACE_MULTI_USER_MODE}" = "true" ]; then
        mode="multi-user"
    else
        mode="single-user"
    fi

    echo ""
    echo "=========================================="
    echo "  Configuration Summary (Issue #2242)"
    echo "=========================================="
    echo ""
    echo "Mode: $mode"
    echo "Running as: $(id -un) (uid=$uid)"
    echo ""

    if [ "$mode" = "multi-user" ]; then
        echo "Multi-user configuration:"
        echo "  • WORKSPACE_MULTI_USER_MODE: ${WORKSPACE_MULTI_USER_MODE:-<not set>}"
        echo "  • OPENACE_ALLOW_ROOT_MULTI_USER: ${OPENACE_ALLOW_ROOT_MULTI_USER:-<not set>}"
        echo "  • OPENACE_CONFIG_DIR: ${OPENACE_CONFIG_DIR:-<not set>}"
        echo "  • WORKSPACE_BASE_DIR: ${WORKSPACE_BASE_DIR:-/workspace}"
        echo ""

        # Validate configuration consistency
        local config_ok=true

        if [ "$uid" != "0" ]; then
            echo "  ❌ ERROR: Running as non-root user but multi-user mode requires root"
            config_ok=false
        fi

        if [ "${OPENACE_ALLOW_ROOT_MULTI_USER}" != "1" ]; then
            echo "  ❌ ERROR: OPENACE_ALLOW_ROOT_MULTI_USER=1 not set"
            config_ok=false
        fi

        if [ "$config_ok" = true ]; then
            echo "  ✅ Configuration validated successfully"
            log_config_check "INFO" "Multi-user mode configuration validated"
        else
            echo ""
            echo "  💡 Quick fix: Use the one-click startup script:"
            echo "     ./scripts/start-multi-user.sh"
            echo ""
            echo "  💡 Or use Docker Compose overlay:"
            echo "     docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d"
            log_config_check "ERROR" "Multi-user mode configuration invalid"
        fi
    else
        echo "Single-user mode (default)"
        echo "  • Container runs as non-root user (uid 1000)"
        echo "  • No system user creation needed"
        echo ""
        echo "  ✅ Configuration OK"
        log_config_check "INFO" "Single-user mode configuration validated"
    fi

    echo ""
    echo "Config check log: $CONFIG_CHECK_LOG"
    echo "=========================================="
    echo ""
}

# Run configuration summary early (after basic validation)
# This is called later in the script after security mode validation

# ============================================================================
# 0. Non-root runtime guard (PR #1780 review / docker-root)
# ============================================================================
# The image defaults to the non-root open-ace user (uid 1000). Single-user
# mode works fine as uid 1000. Multi-user workspace mode genuinely needs root
# (it runs useradd/chown and `sudo -u <user>` across /home), so it must opt
# back into root explicitly via OPENACE_ALLOW_ROOT_MULTI_USER=1 AND run as
# uid 0 (`docker run --user 0` / manifest `runAsUser: 0`). Fail fast instead
# of silently swallowing the useradd/chown permission errors that a naive
# non-root multi-user deployment would hit.
require_root_for_multi_user() {
    if [ "$(id -u)" != "0" ]; then
        echo "ERROR: multi-user workspace mode requires root to create system users."
        echo "       The image defaults to the non-root open-ace user (uid 1000)."
        echo ""
        echo "=========================================="
        echo "  💡 QUICK FIX (Recommended)"
        echo "=========================================="
        echo ""
        echo "Use the one-click startup script:"
        echo "  ./scripts/start-multi-user.sh"
        echo ""
        echo "Or use Docker Compose overlay:"
        echo "  docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d"
        echo ""
        echo "=========================================="
        echo "  🔧 MANUAL FIX"
        echo "=========================================="
        echo ""
        echo "Start the container as root AND set required variables:"
        echo "  docker run --user 0 \\"
        echo "    -e WORKSPACE_MULTI_USER_MODE=true \\"
        echo "    -e OPENACE_ALLOW_ROOT_MULTI_USER=1 \\"
        echo "    -e OPENACE_CONFIG_DIR=/home/open-ace/.open-ace ..."
        echo ""
        echo "Or keep single-user mode (the default):"
        echo "  - If set via environment: unset WORKSPACE_MULTI_USER_MODE"
        echo "  - If set via config.json: set 'multi_user_mode': false"
        log_config_check "ERROR" "Multi-user mode requires root but running as non-root"
        exit 1
    fi
    if [ "${OPENACE_ALLOW_ROOT_MULTI_USER}" != "1" ]; then
        echo "ERROR: multi-user workspace mode is running as root but the explicit"
        echo "       opt-in OPENACE_ALLOW_ROOT_MULTI_USER=1 is not set."
        echo ""
        echo "=========================================="
        echo "  💡 QUICK FIX (Recommended)"
        echo "=========================================="
        echo ""
        echo "Use the one-click startup script:"
        echo "  ./scripts/start-multi-user.sh"
        echo ""
        echo "Or use Docker Compose overlay:"
        echo "  docker compose -f docker-compose.yml -f docker-compose.multi-user.yml up -d"
        echo ""
        echo "=========================================="
        echo "  🔧 MANUAL FIX"
        echo "=========================================="
        echo ""
        echo "Set OPENACE_ALLOW_ROOT_MULTI_USER=1 when running as root:"
        echo "  docker run --user 0 -e OPENACE_ALLOW_ROOT_MULTI_USER=1 ..."
        echo ""
        echo "Or keep single-user mode (the default, non-root)."
        log_config_check "ERROR" "Root user without OPENACE_ALLOW_ROOT_MULTI_USER=1"
        exit 1
    fi
}

# Early fail-fast for the env-var trigger (before any setup work runs).
if [ "${WORKSPACE_MULTI_USER_MODE}" = "true" ]; then
    require_root_for_multi_user
fi

# ============================================================================
# 0. Security Mode Validation (Issue #2331)
# ============================================================================
# Require explicit OPENACE_SECURITY_MODE in production-capable paths.
# Must run BEFORE any database initialization or secret generation.

validate_security_mode() {
    local mode="${OPENACE_SECURITY_MODE:-}"
    local scheduler_mode="${SCHEDULER_MODE:-web}"
    local flask_env="${FLASK_ENV:-}"

    # Check if this is a production-capable path
    # Production indicators: scheduler running, FLASK_ENV=production, or running in production container
    local is_production_path=false

    # If SCHEDULER_MODE is set to web or scheduler (not dev), it's production-capable
    if [ "$scheduler_mode" = "web" ] || [ "$scheduler_mode" = "scheduler" ]; then
        is_production_path=true
    fi

    # If FLASK_ENV is production, it's production-capable
    if [ "$flask_env" = "production" ]; then
        is_production_path=true
    fi

    # If running under Kubernetes
    if [ -n "${KUBERNETES_SERVICE_HOST:-}" ]; then
        is_production_path=true
    fi

    # If running under systemd
    if [ -d "/run/systemd/system" ]; then
        is_production_path=true
    fi

    # Test/CI contexts are NOT production-capable
    if [ "${OPENACE_TEST_MODE:-}" = "1" ] || [ "${CI:-}" = "true" ] || [ "$flask_env" = "test" ]; then
        is_production_path=false
    fi

    # Emergency rollback flag (expires after 30 days)
    # Requires OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP to be set
    if [ "${OPENACE_ALLOW_IMPLICIT_MODE:-}" = "1" ]; then
        if [ -z "${OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP:-}" ]; then
            echo "ERROR: OPENACE_ALLOW_IMPLICIT_MODE=1 requires OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP"
            echo "       Format: YYYY-MM-DD (e.g., 2025-01-15)"
            echo "       Flag is being IGNORED. Set OPENACE_SECURITY_MODE explicitly instead."
        else
            # Check expiration (30 days)
            flag_date=$(date -d "${OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP}" +%s 2>/dev/null || date -j -f "%Y-%m-%d" "${OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP}" +%s 2>/dev/null)
            if [ -n "$flag_date" ]; then
                now=$(date +%s)
                age_days=$(( (now - flag_date) / 86400 ))
                if [ $age_days -gt 30 ]; then
                    echo "ERROR: EMERGENCY ROLLBACK FLAG EXPIRED (set ${age_days} days ago, max: 30)"
                    echo "       Flag is being IGNORED. Set OPENACE_SECURITY_MODE explicitly instead."
                else
                    echo "WARNING: OPENACE_ALLOW_IMPLICIT_MODE=1 is active"
                    echo "         Set ${age_days} days ago, expires in $((30 - age_days)) days"
                    echo "         This flag should not be used in production"
                    is_production_path=false
                fi
            else
                echo "ERROR: Invalid OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP format: ${OPENACE_ALLOW_IMPLICIT_MODE_TIMESTAMP}"
                echo "       Expected format: YYYY-MM-DD"
                echo "       Flag is being IGNORED. Set OPENACE_SECURITY_MODE explicitly instead."
            fi
        fi
    fi

    # Production-capable paths MUST have explicit mode
    if [ "$is_production_path" = true ] && [ -z "$mode" ]; then
        echo "ERROR: OPENACE_SECURITY_MODE must be set explicitly in production-capable paths"
        echo ""
        echo "Valid values:"
        echo "  production  - Strict validation, reject weak secrets"
        echo "  pilot       - Trial mode, auto-generate secrets with warnings"
        echo "  development - Local dev only, auto-generate secrets"
        echo ""
        echo "Examples:"
        echo "  For production:  OPENACE_SECURITY_MODE=production"
        echo "  For trial:       OPENACE_SECURITY_MODE=pilot"
        echo "  For development: OPENACE_SECURITY_MODE=development"
        echo ""
        echo "Migration guide: https://github.com/open-ace/open-ace/issues/2331"
        exit 1
    fi

    # Validate mode value if set
    if [ -n "$mode" ]; then
        case "$mode" in
            production|pilot|development)
                # Valid mode
                ;;
            *)
                echo "ERROR: Invalid OPENACE_SECURITY_MODE value: '$mode'"
                echo ""
                echo "Valid values: production, pilot, development"
                exit 1
                ;;
        esac
    fi
}

# Run security mode validation before any other checks
validate_security_mode

# ============================================================================
# 0. Security Baseline Check (Issue #1893)
# ============================================================================
# Detect security mode and enforce baseline checks before proceeding.
# This must run before any database initialization or secret generation.

# Detect security mode based on environment variables
# Priority: OPENACE_SECURITY_MODE > FLASK_ENV > default (development)
detect_security_mode() {
    # Priority 1: Explicit security mode variable
    if [ "${OPENACE_SECURITY_MODE}" = "production" ]; then
        echo "production"
        return
    fi
    if [ "${OPENACE_SECURITY_MODE}" = "pilot" ]; then
        echo "pilot"
        return
    fi
    if [ "${OPENACE_SECURITY_MODE}" = "development" ]; then
        echo "development"
        return
    fi

    # Priority 2: Flask environment inference (backward compatibility)
    if [ "${FLASK_ENV}" = "production" ]; then
        echo "production"
        return
    fi

    # Default: development mode
    echo "development"
}

# Forbidden database password values (Issue #1893)
# Includes development default password that must be changed for production
FORBIDDEN_DB_PASSWORDS="ace-secret dev-password-change-in-production change-me password admin postgres 123456"

# Check if password is in forbidden list
is_forbidden_password() {
    local password="$1"
    local forbidden
    for forbidden in $FORBIDDEN_DB_PASSWORDS; do
        if [ "$password" = "$forbidden" ]; then
            return 0  # true: is forbidden
        fi
    done
    return 1  # false: not forbidden
}

# Check if password is a placeholder pattern
is_placeholder_password() {
    local password="$1"
    # Match patterns like replace-with-random-*, dev-secret-key, etc.
    if echo "$password" | grep -qE '^(replace-with-random|dev-secret|default-secret|change-me-in-production)'; then
        return 0  # true: is placeholder
    fi
    return 1  # false: not placeholder
}

# Security baseline checker
check_security_baseline() {
    local mode
    mode=$(detect_security_mode)

    echo "=========================================="
    echo "  Security Baseline Check (Issue #1893)"
    echo "  Mode: $mode"
    echo "=========================================="

    local has_warning=false
    local db_password="${DB_PASSWORD:-}"

    # ---- Database Password Check ----
    if [ -z "$db_password" ]; then
        # Empty password
        case "$mode" in
            production)
                echo "[ERROR] SECURITY: Database password is required in production mode."
                echo ""
                echo "To fix:"
                echo "  1. Generate a strong password:"
                echo "     python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
                echo "  2. Set in .env: DB_PASSWORD=<generated-password>"
                echo "  3. Restart: docker compose down && docker compose up -d"
                echo ""
                echo "For trial/development, use OPENACE_SECURITY_MODE=development"
                exit 1
                ;;
            pilot)
                # Generate temporary password for pilot mode
                DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
                export DB_PASSWORD
                echo "[ERROR] SECURITY: Database password not set. Auto-generated temporary password."
                echo "        This is acceptable for pilot but MUST be set before production."
                echo ""
                echo "To set a permanent password:"
                echo "  1. Generate: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
                echo "  2. Set in .env: DB_PASSWORD=<generated-password>"
                echo "  3. Restart: docker compose down && docker compose up -d"
                echo ""
                has_warning=true
                ;;
            development|*)
                # Generate temporary password for development mode
                DB_PASSWORD=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
                export DB_PASSWORD
                echo "[WARN] SECURITY: Database password not set. Auto-generated temporary password."
                echo "       For production, set DB_PASSWORD in .env before deployment."
                has_warning=true
                ;;
        esac
    elif is_forbidden_password "$db_password"; then
        # Forbidden password
        case "$mode" in
            production)
                echo "[ERROR] SECURITY: Database password \"$db_password\" is a known weak default."
                echo "        This password is forbidden in production mode."
                echo ""
                echo "To fix:"
                echo "  1. Generate a strong password:"
                echo "     python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
                echo "  2. Update .env: DB_PASSWORD=<generated-password>"
                echo "  3. Restart: docker compose down && docker compose up -d"
                exit 1
                ;;
            pilot)
                echo "[ERROR] SECURITY: Database password \"$db_password\" is a known weak default."
                echo "        This is acceptable for pilot but MUST be changed before production."
                echo ""
                echo "To fix:"
                echo "  1. Generate: python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
                echo "  2. Update .env: DB_PASSWORD=<generated-password>"
                echo "  3. Restart: docker compose down && docker compose up -d"
                echo ""
                echo "Set OPENACE_SECURITY_MODE=production to enforce this check."
                has_warning=true
                ;;
            development|*)
                echo "[WARN] SECURITY: Database password \"$db_password\" is a known weak default."
                echo "       For production, use a strong password (>=9 characters)."
                has_warning=true
                ;;
        esac
    elif [ "$mode" = "production" ] && [ ${#db_password} -le 8 ]; then
        # Password too short for production
        echo "[ERROR] SECURITY: Database password is too short (${#db_password} chars)."
        echo "        Production mode requires at least 9 characters."
        echo ""
        echo "To fix:"
        echo "  1. Generate a strong password:"
        echo "     python3 -c \"import secrets; print(secrets.token_urlsafe(32))\""
        echo "  2. Update .env: DB_PASSWORD=<generated-password>"
        exit 1
    fi

    # ---- Secret Key Check ----
    local secret_key="${SECRET_KEY:-}"
    if [ -z "$secret_key" ]; then
        case "$mode" in
            production)
                echo "[ERROR] SECURITY: SECRET_KEY is required in production mode."
                echo ""
                echo "To fix:"
                echo "  1. Generate: python3 -c \"import secrets; print(secrets.token_hex(32))\""
                echo "  2. Set in .env: SECRET_KEY=<generated-key>"
                echo "  3. Restart: docker compose down && docker compose up -d"
                exit 1
                ;;
            pilot)
                echo "[WARN] SECURITY: SECRET_KEY not set. Will auto-generate."
                echo "        For production, set SECRET_KEY explicitly in .env."
                has_warning=true
                ;;
            development|*)
                # Development mode: auto-generate is fine
                ;;
        esac
    elif is_placeholder_password "$secret_key"; then
        case "$mode" in
            production)
                echo "[ERROR] SECURITY: SECRET_KEY contains placeholder value."
                echo "        Production requires a strong random key."
                exit 1
                ;;
            pilot|development|*)
                echo "[WARN] SECURITY: SECRET_KEY appears to be a placeholder."
                has_warning=true
                ;;
        esac
    fi

    # ---- Encryption Key Check ----
    local enc_key="${OPENACE_ENCRYPTION_KEY:-}"
    if [ -z "$enc_key" ]; then
        case "$mode" in
            production)
                echo "[ERROR] SECURITY: OPENACE_ENCRYPTION_KEY is required in production mode."
                echo "        This key encrypts sensitive data (SMTP passwords, API keys)."
                echo ""
                echo "To fix:"
                echo "  1. Generate: python3 -c \"import secrets; print(secrets.token_hex(16))\""
                echo "  2. Set in .env: OPENACE_ENCRYPTION_KEY=<generated-key>"
                echo "  3. Restart: docker compose down && docker compose up -d"
                echo ""
                echo "WARNING: Changing this key makes existing encrypted data unreadable!"
                exit 1
                ;;
            pilot)
                echo "[WARN] SECURITY: OPENACE_ENCRYPTION_KEY not set. Will auto-generate."
                echo "        For production, set OPENACE_ENCRYPTION_KEY explicitly in .env."
                echo "        WARNING: Key change makes existing encrypted data unreadable!"
                has_warning=true
                ;;
            development|*)
                # Development mode: auto-generate is fine
                ;;
        esac
    elif is_placeholder_password "$enc_key"; then
        case "$mode" in
            production)
                echo "[ERROR] SECURITY: OPENACE_ENCRYPTION_KEY contains placeholder value."
                echo "        Production requires a strong random key."
                exit 1
                ;;
            pilot|development|*)
                echo "[WARN] SECURITY: OPENACE_ENCRYPTION_KEY appears to be a placeholder."
                has_warning=true
                ;;
        esac
    fi

    # ---- Root User Check (Issue #1893) ----
    if [ "$(id -u)" = "0" ]; then
        # Running as root - check if properly authorized for multi-user mode
        if [ "${WORKSPACE_MULTI_USER_MODE}" != "true" ] || [ "${OPENACE_ALLOW_ROOT_MULTI_USER}" != "1" ]; then
            echo "[ERROR] SECURITY: Container running as root without proper authorization."
            echo "        The image defaults to non-root user (uid 1000)."
            echo ""
            echo "If you need multi-user workspace mode:"
            echo "  1. Set WORKSPACE_MULTI_USER_MODE=true"
            echo "  2. Set OPENACE_ALLOW_ROOT_MULTI_USER=1"
            echo "  3. Use: docker run --user 0 ..."
            echo ""
            echo "For single-user mode, remove any --user 0 or user: \"0\" setting."
            exit 1
        fi
    fi

    # Summary
    if [ "$has_warning" = true ]; then
        echo ""
        echo "=========================================="
        echo "  Security warnings detected. Review above."
        if [ "$mode" = "pilot" ]; then
            echo "  These MUST be fixed before production deployment."
        fi
        echo "=========================================="
    else
        echo "Security baseline check passed."
    fi
}

# Run security baseline check before any other initialization
check_security_baseline

# ============================================================================
# 0. Output Configuration Summary (Issue #2242)
# ============================================================================
# Output configuration summary after security checks pass
output_config_summary

# ============================================================================
# 0. Pre-flight Setup
# ============================================================================
# Create logs directory (Issue #1205)
mkdir -p /app/logs

# Default the config dir to a path the current uid can actually write.
# The image runs as the non-root open-ace user (uid 1000) by default
# (Dockerfile `USER 1000`). /root is root:root 0700, so defaulting to
# /root/.open-ace made `generate_default_config`'s `mkdir -p /root/.open-ace`
# fail with Permission denied under `set -e`, breaking bare `docker run` and
# the default docker-compose single-user path. Pick a writable home-based dir
# unless the caller overrides OPENACE_CONFIG_DIR (e.g. K8s sets it explicitly).
if [ -z "${OPENACE_CONFIG_DIR:-}" ]; then
    if [ "$(id -u)" = "0" ]; then
        OPENACE_CONFIG_DIR="/root/.open-ace"
    else
        OPENACE_CONFIG_DIR="${HOME:-/home/open-ace}/.open-ace"
    fi
fi
OPENACE_CONFIG_FILE="${OPENACE_CONFIG_FILE:-${OPENACE_CONFIG_DIR}/config.json}"
export OPENACE_CONFIG_DIR OPENACE_CONFIG_FILE

# ============================================================================
# 0.1. Pre-flight Validation (Issue #1006)
# ============================================================================
validate_node_environment() {
    echo "Validating Node.js environment..."

    # Check node executable exists in PATH
    if ! command -v node &>/dev/null; then
        echo "ERROR: Node.js not found in PATH. Cannot start container."
        echo "       This indicates a Docker image build failure."
        echo "       Please rebuild the image with proper Node.js installation."
        exit 1
    fi

    NODE_PATH=$(which node)

    # Verify node is executable
    if [ ! -x "$NODE_PATH" ]; then
        echo "ERROR: Node.js at $NODE_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi

    # Get and display node version
    NODE_VERSION=$(node --version 2>/dev/null || echo "unknown")
    echo "  Node.js: $NODE_PATH ($NODE_VERSION)"

    # Verify CLI file exists
    CLI_PATH="/usr/lib/node_modules/@qwen-code/qwen-code/cli.js"
    if [ ! -f "$CLI_PATH" ]; then
        echo "ERROR: qwen-code CLI not found at $CLI_PATH."
        echo "       This indicates npm install failed during image build."
        echo "       Please rebuild the image with proper npm installation."
        exit 1
    fi
    echo "  CLI: $CLI_PATH"

    # Verify WebUI executable exists
    WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")
    if [ ! -x "$WEBUI_PATH" ]; then
        echo "ERROR: qwen-code-webui not executable at $WEBUI_PATH."
        echo "       Please check installation or rebuild the image."
        exit 1
    fi
    echo "  WebUI: $WEBUI_PATH"

    # === Process Tools Verification (Issue #1050) ===
    echo "Validating process tools..."

    if ! command -v ps &>/dev/null; then
        echo "ERROR: ps command not found in PATH."
        echo "       This indicates procps package was not installed during Docker build."
        echo "       WebUI requires ps to find and abort CLI processes."
        echo "       Please rebuild the image with procps package."
        exit 1
    fi

    PS_PATH=$(which ps)
    if [ ! -x "$PS_PATH" ]; then
        echo "ERROR: ps at $PS_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi
    echo "  ps: $PS_PATH"

    # === Git Verification ===
    echo "Validating git and GitHub CLI..."

    if ! command -v git &>/dev/null; then
        echo "ERROR: git not found in PATH."
        echo "       This indicates git package was not installed during Docker build."
        echo "       Autonomous development requires git for clone, branch, commit, push operations."
        echo "       Please rebuild the image with git package."
        exit 1
    fi

    GIT_PATH=$(which git)
    if [ ! -x "$GIT_PATH" ]; then
        echo "ERROR: git at $GIT_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi

    # Get and display git version
    GIT_VERSION=$(git --version 2>/dev/null || echo "unknown")
    echo "  git: $GIT_PATH ($GIT_VERSION)"

    # === GitHub CLI Verification ===
    if ! command -v gh &>/dev/null; then
        echo "ERROR: gh CLI not found in PATH."
        echo "       This indicates gh CLI was not installed during Docker build."
        echo "       Autonomous development requires gh for PR, Issue, and GitHub API operations."
        echo "       Please rebuild the image with gh CLI installed."
        exit 1
    fi

    GH_PATH=$(which gh)
    if [ ! -x "$GH_PATH" ]; then
        echo "ERROR: gh CLI at $GH_PATH is not executable."
        echo "       Please check file permissions or rebuild the image."
        exit 1
    fi

    # Get and display gh version
    GH_VERSION=$(gh --version 2>/dev/null | head -n1 || echo "unknown")
    echo "  gh: $GH_PATH ($GH_VERSION)"

    echo "Node.js environment validated successfully."
}

# If a custom command is passed, execute it directly (skip validation)
if [ "$1" != "" ] && [ "$1" != "gunicorn" ]; then
    exec "$@"
fi

# Run validation before starting the application
validate_node_environment

echo "=========================================="
echo "  Open ACE - Starting..."
echo "=========================================="

# ============================================================================
# 0.2. Generate Default Config (Issue #1260)
# ============================================================================

# Auto-generate strong random secrets when the operator did not set them.
# Lets `docker compose up` work with zero configuration: the entrypoint fills
# SECRET_KEY / OPENACE_ENCRYPTION_KEY / UPLOAD_AUTH_KEY so the Python app
# (which runs as FLASK_ENV=production and strictly validates these) starts.
#
# Persistence: generated values are written to $OPENACE_CONFIG_DIR/generated-
# secrets.env (the config-data named volume) and re-sourced on subsequent
# starts. This is critical because OPENACE_ENCRYPTION_KEY encrypts data that is
# itself persisted (SMTP passwords, API keys in postgres) — a fresh random key
# on every restart would silently make that ciphertext undecryptable. Setting
# any secret explicitly via env (e.g. .env) always takes precedence and is
# never overwritten.
ensure_secret_env() {
    local secrets_file="${OPENACE_CONFIG_DIR}/generated-secrets.env"

    # NOTE on persistence location: secrets_file lives under OPENACE_CONFIG_DIR,
    # which is also where config.json lives, so both persist together on the
    # config-data volume. In the default (uid 1000) compose deployment that dir
    # is /home/open-ace/.open-ace and the volume is mounted there — matches. If
    # an operator switches to root/multi-user mode they must ensure the volume
    # is mounted at the matching path (/root/.open-ace for root), otherwise
    # neither config.json nor these secrets persist. See .env.example.

    # 1. Reload previously generated secrets (persisted across restarts), but
    #    ONLY for variables not already set in the environment — explicit env
    #    values (e.g. from .env) always take precedence. We avoid `set -a; source`
    #    because that would clobber operator-provided values. Python parses the
    #    file (NAME='value' lines we wrote), validates NAME is an identifier and
    #    VALUE is hex (the only format we ever write), and prints assignments
    #    only for still-unset names.
    if [ -f "$secrets_file" ]; then
        local reloaded
        reloaded=$(python3 -c "
import os, re
for line in open('$secrets_file'):
    line = line.strip()
    if not line or line.startswith('#') or '=' not in line:
        continue
    name, value = line.split('=', 1)
    # We only ever write NAME='hex' — reject anything else to avoid eval risk.
    if name.isidentifier() and re.fullmatch(r\"'[0-9a-f]+'\", value) and not os.environ.get(name):
        print(line)
")
        if [ -n "$reloaded" ]; then
            eval "$reloaded"
            export SECRET_KEY OPENACE_ENCRYPTION_KEY UPLOAD_AUTH_KEY
        fi
    fi

    # 2. Generate any still-unset secret. Single python invocation generates
    #    only the missing ones and prints NAME='value' lines for eval.
    local generated
    generated=$(python3 -c "
import os, secrets
if not os.environ.get('SECRET_KEY'):
    print(\"SECRET_KEY='\" + secrets.token_hex(32) + \"'\")
if not os.environ.get('OPENACE_ENCRYPTION_KEY'):
    print(\"OPENACE_ENCRYPTION_KEY='\" + secrets.token_hex(16) + \"'\")
if not os.environ.get('UPLOAD_AUTH_KEY'):
    print(\"UPLOAD_AUTH_KEY='\" + secrets.token_hex(16) + \"'\")
")
    if [ -n "$generated" ]; then
        eval "$generated"
        export SECRET_KEY OPENACE_ENCRYPTION_KEY UPLOAD_AUTH_KEY
        # Log which keys were generated (name only, never the value).
        local line name
        while IFS= read -r line; do
            name="${line%%=*}"
            echo "Generated $name (set $name env to override)."
        done <<< "$generated"

        # 3. Persist ONLY the secrets we generated this run (not values the
        #    operator set explicitly via env — those belong in .env, not here).
        #    Idempotent: drop any prior assignment for these names, append new.
        local names=""
        while IFS= read -r line; do
            name="${line%%=*}"
            names="${names:+$names|}$name"
        done <<< "$generated"
        mkdir -p "$OPENACE_CONFIG_DIR"
        grep -vE "^($names)=" "$secrets_file" 2>/dev/null > "${secrets_file}.tmp" || true
        printf '%s\n' "$generated" >> "${secrets_file}.tmp"
        mv "${secrets_file}.tmp" "$secrets_file"
        chmod 600 "$secrets_file"
        echo "Persisted generated secrets to $secrets_file (survives restart)."
    fi
}

# Generate default config.json if not exists (one-click deploy support)
generate_default_config() {
    CONFIG_FILE="$OPENACE_CONFIG_FILE"
    CONFIG_DIR=$(dirname "$CONFIG_FILE")

    # Skip if config already exists (user-mounted or previously generated)
    if [ -f "$CONFIG_FILE" ]; then
        echo "Config file exists at $CONFIG_FILE, skipping generation."
        return 0
    fi

    echo "Generating default config at $CONFIG_FILE..."

    # Create config directory
    mkdir -p "$CONFIG_DIR"

    # Auto-detect SERVER_IP if not configured (Issue #1306)
    # Resolve host.docker.internal to get host gateway IP (via extra_hosts)
    if [ -z "$SERVER_IP" ]; then
        # Method 1: getent hosts (resolves host.docker.internal to host gateway IP)
        SERVER_IP=$(getent hosts host.docker.internal 2>/dev/null | awk '{print $1; exit}')

        # Method 2: hostname -I (fallback, filter localhost and link-local)
        if [ -z "$SERVER_IP" ]; then
            SERVER_IP=$(hostname -I 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i!="127.0.0.1" && !match($i,/^169\.254\./)) {print $i; exit}}')
        fi

        # Final fallback
        if [ -z "$SERVER_IP" ]; then
            echo "WARNING: Could not auto-detect SERVER_IP, falling back to host.docker.internal"
            SERVER_IP="host.docker.internal"
        else
            # Validate the detected address is plausibly browser-reachable.
            # Some runtimes (e.g. OrbStack) resolve host.docker.internal /
            # hostname -I to a container-internal address in the 0.0.0.0/8
            # reserved block (e.g. 0.250.250.254) that browsers cannot reach,
            # which leaves the workspace iframe blank. Default docker-compose
            # deployments are accessed locally, so localhost is the safe
            # fallback. Private IPs (192.168/10.x/172.16-31.x) are legit
            # production access addresses and are left untouched.
            case "$SERVER_IP" in
                0.*)
                    echo "WARNING: detected SERVER_IP $SERVER_IP is in the reserved 0.0.0.0/8 block (unreachable from browser); using localhost"
                    SERVER_IP="localhost"
                    ;;
            esac
            echo "Auto-detected SERVER_IP: $SERVER_IP"
        fi
    fi
    PORT="${PORT:-19888}"
    DEFAULT_WORKSPACE_MULTI_USER_MODE="${WORKSPACE_MULTI_USER_MODE:-false}"
    if [ "$DEFAULT_WORKSPACE_MULTI_USER_MODE" != "true" ]; then
        DEFAULT_WORKSPACE_MULTI_USER_MODE="false"
    fi
    # Issue #3374 (PR review round 3): multi-user installs pin an explicit
    # isolation floor so a later launch-path degradation cannot silently
    # drop per-user isolation. WORKSPACE_REQUIRED_ISOLATION_LEVEL overrides.
    DEFAULT_REQUIRED_ISOLATION="${WORKSPACE_REQUIRED_ISOLATION_LEVEL:-}"
    if [ -z "$DEFAULT_REQUIRED_ISOLATION" ] && [ "$DEFAULT_WORKSPACE_MULTI_USER_MODE" = "true" ]; then
        DEFAULT_REQUIRED_ISOLATION="os_user"
    fi

    # Get hostname dynamically (matches install.sh behavior)
    HOST_NAME=$(hostname -f 2>/dev/null || hostname 2>/dev/null || echo "docker-container")

    # Generate random token secret (32 chars hex). UPLOAD_AUTH_KEY is sourced
    # from ensure_secret_env above (env or persisted file) so the value stays
    # stable across restarts; only generate here as a last-resort fallback.
    TOKEN_SECRET=$(python3 -c "import secrets; print(secrets.token_hex(16))")
    UPLOAD_AUTH_KEY="${UPLOAD_AUTH_KEY:-$(python3 -c "import secrets; print(secrets.token_hex(16))")}"

    # Generate default config (matches install.sh defaults)
    # Note: DATABASE_URL env var takes precedence over config file for database connection
    # Database credentials use shell-expanded variables from environment
    # Issue #1893: Use ${VAR:-} syntax (no default password) - security check runs earlier
    # Issue #1336: Use ${VAR:-default} syntax (no \$ escape) so shell expands variables
    # This prevents fetch scripts from reading unexpanded "${DB_USER:-ace}" literal strings
    # which would cause psycopg2 to parse "${DB_USER" as username (colon as delimiter)
    cat > "$CONFIG_FILE" << CONFIG_EOF
{
  "host_name": "$HOST_NAME",
  "database": {
    "type": "postgresql",
    "url": "postgresql://${DB_USER:-ace}:${DB_PASSWORD:-dev-password-change-in-production}@postgres:5432/${DB_NAME:-ace}"
  },
  "server": {
    "upload_auth_key": "$UPLOAD_AUTH_KEY",
    "server_url": "http://${SERVER_IP}:${PORT}",
    "web_port": ${PORT},
    "web_host": "0.0.0.0"
  },
  "workspace": {
    "enabled": true,
    "url": "http://${SERVER_IP}",
    "multi_user_mode": ${DEFAULT_WORKSPACE_MULTI_USER_MODE},
    "required_isolation_level": "${DEFAULT_REQUIRED_ISOLATION}",
    "port_range_start": 3100,
    "port_range_end": 3200,
    "max_instances": 30,
    "idle_timeout_minutes": 30,
    "cleanup_interval_minutes": 5,
    "token_secret": "$TOKEN_SECRET",
    "webui_path": ""
  },
  "autonomous": {
    "enabled": true
  },
  "tools": {
    "openclaw": {
      "enabled": true,
      "token_env": "OPENCLAW_TOKEN",
      "gateway_url": "http://${SERVER_IP}:18789"
    },
    "claude": {
      "enabled": true
    },
    "qwen": {
      "enabled": true
    }
  },
  "feishu": {
    "app_id": "",
    "app_secret": ""
  },
  "insights": {
    "temperature": 0.3,
    "max_tokens": 4096
  }
}
CONFIG_EOF

    # Set restrictive permissions (Issue #1252)
    chmod 600 "$CONFIG_FILE"
    echo "Default config generated with permissions 600."
}

# Ensure security secrets exist before the app starts. Must run before
# generate_default_config (which reuses UPLOAD_AUTH_KEY from env when present,
# falling back to generation only if still unset) and before gunicorn (which
# needs SECRET_KEY / OPENACE_ENCRYPTION_KEY in env).
ensure_secret_env

# Issue #2331: Create pilot metadata when in pilot mode with auto-generated secrets
if [ "${OPENACE_SECURITY_MODE}" = "pilot" ]; then
    # Check if we have generated secrets (indicates auto-generation occurred)
    if [ -f "${OPENACE_CONFIG_DIR}/generated-secrets.env" ]; then
        echo "Creating pilot mode metadata file..."

# Create pilot metadata using Python
        python3 -c "
import json
import os
from datetime import datetime, timezone

metadata_path = os.path.join(os.environ.get('OPENACE_CONFIG_DIR', '/home/open-ace/.open-ace'), 'pilot-mode-metadata.json')

# Check which secrets were auto-generated
secrets_generated = []
secrets_file = os.path.join(os.environ.get('OPENACE_CONFIG_DIR', '/home/open-ace/.open-ace'), 'generated-secrets.env')
if os.path.exists(secrets_file):
    with open(secrets_file) as f:
        for line in f:
            line = line.strip()
            if '=' in line and not line.startswith('#'):
                name = line.split('=', 1)[0]
                if name in ['SECRET_KEY', 'OPENACE_ENCRYPTION_KEY', 'UPLOAD_AUTH_KEY']:
                    secrets_generated.append(name)

metadata = {
    'mode': 'pilot',
    'generated_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    'warning': 'NOT FOR PRODUCTION USE - Auto-generated secrets',
    'secrets_generated': secrets_generated,
    'persistent_file': 'generated-secrets.env'
}

with open(metadata_path, 'w') as f:
    json.dump(metadata, f, indent=2)

print(f'Pilot metadata created at {metadata_path}')
"

        echo "=========================================="
        echo "  PILOT MODE WARNING"
        echo "  Auto-generated secrets - NOT for production use"
        echo "  Metadata: ${OPENACE_CONFIG_DIR}/pilot-mode-metadata.json"
        echo "=========================================="
    fi
fi

generate_default_config

# ============================================================================
# 1. Database Initialization
# ============================================================================
if [ -n "$DATABASE_URL" ]; then
    # Extract connection parameters from DATABASE_URL
    # Format: postgresql://user:password@host:port/dbname
    DB_HOST_FROM_URL=$(echo "$DATABASE_URL" | sed -n 's|.*@\([^:]*\):.*|\1|p')
    DB_PORT_FROM_URL=$(echo "$DATABASE_URL" | sed -n 's|.*:\([0-9]*\)/.*|\1|p')
    DB_USER_FROM_URL=$(echo "$DATABASE_URL" | sed -n 's|.*://\([^:]*\):.*|\1|p')

    echo "Waiting for PostgreSQL at ${DB_HOST_FROM_URL}:${DB_PORT_FROM_URL}..."

    # Wait for PostgreSQL to be ready (max 60 seconds)
    WAIT_COUNT=0
    MAX_WAIT=60
    while [ $WAIT_COUNT -lt $MAX_WAIT ]; do
        if pg_isready -h "${DB_HOST_FROM_URL}" -p "${DB_PORT_FROM_URL}" -U "${DB_USER_FROM_URL}" 2>/dev/null; then
            echo "PostgreSQL is ready."
            break
        fi
        sleep 2
        WAIT_COUNT=$((WAIT_COUNT + 2))
    done

    if [ $WAIT_COUNT -ge $MAX_WAIT ]; then
        echo "ERROR: PostgreSQL not ready after ${MAX_WAIT}s. Exiting."
        exit 1
    fi

    echo "Checking database initialization status..."
    HAS_APP_SCHEMA=$(python3 -c "
import os, psycopg2
SENTINEL_TABLES = ['users', 'agent_sessions', 'session_messages']
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(
        \"\"\"
        SELECT 1
        FROM information_schema.tables
        WHERE table_schema = 'public'
          AND table_name = ANY(%s)
        LIMIT 1
        \"\"\",
        (SENTINEL_TABLES,),
    )
    result = 'yes' if cur.fetchone() else 'no'
    conn.close()
    print(result)
except Exception:
    print('unknown')
" 2>/dev/null || echo "unknown")

    # Issue #3397: fresh-database self-initialization.
    # A fresh production install used to be REFUSED here: with no schema and
    # no alembic_version table, scripts/check_min_revision.py exits 1 in
    # production mode ("Fresh database detected"), so the documented compose
    # deployment path could not boot at all — the acceptance run had to use a
    # one-shot `alembic upgrade head && python3 scripts/init_db.py` container
    # as a DECLARED DEVIATION. The entrypoint now SELF-initializes exactly
    # that state: NO application schema AND no alembic_version table (the
    # same two commands as the deviation workaround, run by the normal flow
    # below). A database with existing schema OR a recorded revision is NEVER
    # touched by this branch — it keeps the minimum-revision refusal and the
    # regular upgrade path (init_db.py still seeds only when no application
    # schema existed at boot). Quoted heredoc probe (the #3399 raw-quote class
    # cannot recur); a probe failure yields "unknown", which is NOT fresh, so
    # detection fails strict, never loose.
    HAS_ALEMBIC_VERSION=$(python3 - <<'PY_FRESH_DB_PROBE_EOF' 2>/dev/null || echo "unknown"
import os

import psycopg2

try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM information_schema.tables "
        "WHERE table_schema = 'public' AND table_name = 'alembic_version'"
    )
    result = 'yes' if cur.fetchone() else 'no'
    conn.close()
    print(result)
except Exception:
    print('unknown')
PY_FRESH_DB_PROBE_EOF
)
    FRESH_DB="false"
    if [ "$HAS_APP_SCHEMA" = "no" ] && [ "$HAS_ALEMBIC_VERSION" = "no" ]; then
        FRESH_DB="true"
        echo "Fresh database detected — self-initializing schema and seed (was #3397)."
    elif [ "$HAS_APP_SCHEMA" = "yes" ]; then
        echo "Existing application schema detected."
    elif [ "$HAS_APP_SCHEMA" = "no" ]; then
        echo "No application schema detected. Treating this as a fresh installation."
    else
        echo "WARNING: Could not determine whether application tables already exist."
        echo "Proceeding with minimum revision check and Alembic upgrade."
    fi

    # Verify the database is on the supported (>= baseline_2026_06_23) lineage
    # before upgrading. Fresh databases (no alembic_version table) pass through;
    # the schema is built from the baseline snapshot below.
    if [ "$FRESH_DB" != "true" ]; then
        if ! python3 scripts/check_min_revision.py; then
            echo "ERROR: database revision is below the minimum supported starting point (baseline_2026_06_23)."
            echo "       Restore a known-healthy backup already on the baseline lineage, then restart the container."
            exit 1
        fi
    fi

    echo "Running database migrations..."
    if ! alembic upgrade head; then
        echo ""
        echo "ERROR: alembic upgrade head failed"
        echo ""
        echo "Database schema migration failed. This could indicate:"
        echo "  - Database schema is incompatible or corrupted"
        echo "  - Network connectivity issues"
        echo "  - Insufficient database permissions"
        echo ""
        echo "Recovery steps:"
        echo "  1. Verify database connection:"
        echo "     pg_isready -h <host> -p <port>"
        echo ""
        echo "  2. Check current schema version:"
        echo "     alembic current"
        echo ""
        echo "  3. Review migration history:"
        echo "     alembic history"
        echo ""
        echo "  4. For fresh databases, ensure schema is initialized:"
        echo "     alembic upgrade head"
        echo ""
        echo "  5. If migration is blocked, restore from backup:"
        echo "     pg_restore -d <database> <backup_file>"
        echo ""
        echo "Issue #2190: Schema authority model enforcement"
        exit 1
    fi

    if [ "$HAS_APP_SCHEMA" != "yes" ]; then
        echo "Creating default admin user..."
        python3 scripts/init_db.py || echo "WARNING: admin user creation failed (may already exist)"
        echo "Database initialization completed."
    else
        echo "Database migration completed."
    fi

    # ========================================================================
    # 1b. Fix materialized view ownership for PostgreSQL (Issue #1192)
    # Ensure current database user can refresh materialized views.
    # Must run AFTER migrations because views may be created or replaced there.
    # ========================================================================
    echo "Fixing materialized view ownership..."
    python3 -c "
import os, psycopg2
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # Fix session_stats ownership with verification
    cur.execute(\"SELECT matviewowner FROM pg_matviews WHERE matviewname = 'session_stats'\")
    owner_row = cur.fetchone()
    if owner_row:
        current_owner = owner_row[0]
        cur.execute(\"SELECT CURRENT_USER\")
        db_user = cur.fetchone()[0]
        if current_owner != db_user:
            cur.execute(\"ALTER MATERIALIZED VIEW session_stats OWNER TO CURRENT_USER\")
            print(f'Fixed session_stats owner: {current_owner} -> {db_user}')
        else:
            print(f'session_stats owner already correct: {current_owner}')

    # Fix request_stats ownership (if exists)
    cur.execute(\"SELECT matviewowner FROM pg_matviews WHERE matviewname = 'request_stats'\")
    owner_row = cur.fetchone()
    if owner_row:
        current_owner = owner_row[0]
        cur.execute(\"SELECT CURRENT_USER\")
        db_user = cur.fetchone()[0]
        if current_owner != db_user:
            cur.execute(\"ALTER MATERIALIZED VIEW request_stats OWNER TO CURRENT_USER\")
            print(f'Fixed request_stats owner: {current_owner} -> {db_user}')

    # Fix all sequences and tables ownership to CURRENT_USER (Issue #1042 + #1192)
    # Use psycopg2.sql.Identifier for safe identifier quoting (review feedback)
    from psycopg2 import sql
    cur.execute(\"SELECT sequencename FROM pg_sequences WHERE schemaname = 'public'\")
    for seq_row in cur.fetchall():
        cur.execute(sql.SQL(\"ALTER SEQUENCE {} OWNER TO CURRENT_USER\").format(sql.Identifier(seq_row[0])))
    cur.execute(\"SELECT tablename FROM pg_tables WHERE schemaname = 'public'\")
    for tbl_row in cur.fetchall():
        cur.execute(sql.SQL(\"ALTER TABLE {} OWNER TO CURRENT_USER\").format(sql.Identifier(tbl_row[0])))

    conn.commit()
    conn.close()
    print('Ownership fix completed.')
except Exception as e:
    print(f'Warning: {e}')
" || echo "WARNING: materialized view ownership fix failed"
fi

# ============================================================================
# 2. Multi-User Workspace Setup
# ============================================================================
# Check multi-user mode from both environment variable and config.json
# This ensures the setting works even if docker-compose.yml is missing the env var
CONFIG_MULTI_USER="false"
if [ -f "$OPENACE_CONFIG_FILE" ]; then
    CONFIG_MULTI_USER=$(python3 -c "import json, os; c=json.load(open(os.environ['OPENACE_CONFIG_FILE'])); print('true' if c.get('workspace',{}).get('multi_user_mode',False) else 'false')" 2>/dev/null || echo "false")
fi

if [ "$WORKSPACE_MULTI_USER_MODE" = "true" ] || [ "$CONFIG_MULTI_USER" = "true" ]; then
    echo "Configuring multi-user workspace mode (env=$WORKSPACE_MULTI_USER_MODE, config=$CONFIG_MULTI_USER)..."
    # Fail fast if multi-user mode was enabled via config.json but the container
    # is not running as root with the explicit opt-in (see top-of-file guard).
    require_root_for_multi_user

    # Issue #2730 + #3396: shared-project groups.
    # - openace-shared (GLOBAL): membership grants ONLY the right to create a
    #   project directory inside the sticky <base>/shared namespace root
    #   (root:openace-shared 3770). It is never the group-owner of project
    #   content since #3396.
    # - openace-shared-<tenant_id>: per-tenant CONTENT group — shared project
    #   dirs are group-owned by it with 2770/660 (no others bits), so members
    #   of OTHER tenants get EACCES at the OS layer even though they hold the
    #   global membership for namespace creation.
    # Enrollment of the DB users into BOTH groups happens in the DB-driven
    # shared-group sync below (a /home-glob pass cannot know a directory's
    # tenant). Tenant-less platform admins map to openace-shared-0.
    SHARED_GROUP="openace-shared"
    if ! getent group "$SHARED_GROUP" > /dev/null 2>&1; then
        groupadd -f "$SHARED_GROUP"
        echo "  Created shared project group: $SHARED_GROUP"
    else
        echo "  Shared project group already exists: $SHARED_GROUP"
    fi

    # Ensure workspace base directory exists
    # Issue #3379: WORKSPACE_BASE_DIR may be a comma-separated list (the fs
    # layer's _home_roots_for_user semantics) — a single `mkdir -p` on the
    # raw value would create a literal "a,b" directory. Trimming is pure
    # bash (review NIT): `echo | xargs` aborts under set -e when a base dir
    # contains a quote character.
    WORKSPACE_DIR="${WORKSPACE_BASE_DIR:-/workspace}"
    IFS=',' read -r -a _workspace_base_dirs <<< "$WORKSPACE_DIR"
    for _base_dir in "${_workspace_base_dirs[@]}"; do
        _base_dir="${_base_dir#"${_base_dir%%[![:space:]]*}"}"
        _base_dir="${_base_dir%"${_base_dir##*[![:space:]]}"}"
        [ -z "$_base_dir" ] && continue
        mkdir -p "$_base_dir"

        # Issue #3379 (multi-user acceptance gap): provision the shared
        # namespace root. POST /api/projects with create_dir runs
        # `sudo -u <user> mkdir -p` — on a fresh volume the parent is
        # root:root 0755 and every user's creation EACCESes (403). The
        # #3376 first-class <base>/shared/<name> namespace needs its root
        # to pre-exist, group-writable by openace-shared with setgid so
        # shared files inherit the group. Idempotent on restarts; only the
        # root itself is touched, never its contents.
        #
        # Review MINOR (account-named-shared guard): if a REAL account named
        # "shared" exists, <base>/shared is that account's home root —
        # re-chgrp/chmod on every restart would ping-pong ownership with the
        # app's _ensure_workspace_dirs and group-open a private home in
        # between. The app side already rejects the collision fail-closed at
        # registration (path_guard); the entrypoint skips loudly instead.
        if id "shared" &>/dev/null || { [ -e "$_base_dir/shared" ] && [ "$(stat -c '%U' "$_base_dir/shared" 2>/dev/null)" != "root" ]; }; then
            echo "  WARNING: skipping shared-namespace provisioning for $_base_dir/shared — path collides with a real account or is not root-owned (administrator intervention required)"
            continue
        fi
        # review round 2 (4004368890): degrade to a warning, not a crash loop —
        # a failed provisioning only means shared-project creation 403s until
        # an administrator fixes it; the app's own dir/ownership failures are
        # warning-grade too, and set -e would otherwise restart-loop the whole
        # service on e.g. a root_squash NFS base dir.
        # chmod 3770 (was 3775; sticky since review round 3, 4004874853):
        # +sticky — rename(2) only needs
        # write+search on the parent, and openace-shared is a GLOBAL group
        # (every tenant's account joins, for namespace creation only), so
        # without the sticky bit any member could mv/replace another
        # tenant's project directory. Sticky blocks non-owner renames at the
        # root; sudo -u <user> mkdir for new projects and root-run
        # setup_permissions_with_depth_limit are unaffected. Content-level
        # cross-tenant access inside projects is fenced by the per-tenant
        # groups (openace-shared-<tenant>, 2770/660 — Issue #3396).
        # The OTHERS bits are now dropped (3775 -> 3770): the old others r-x
        # let ANY non-member process on the host enumerate the namespace
        # root and read shared project NAMES — metadata only (content access
        # always needed the tenant group), but project names can be
        # sensitive. Declared residual, inherent to the namespace design:
        # openace-shared is global, so every active account — including
        # OTHER tenants', who need the creation right — can still list the
        # root via the GROUP r-x; per-tenant name secrecy is not achievable
        # while namespace creation is a global right (content stays fenced).
        # The unconditional chgrp+chmod below re-normalizes mode drift from
        # any pre-existing 3775 deployment on the next boot (idempotent).
        if ! { mkdir -p "$_base_dir/shared" && chgrp "$SHARED_GROUP" "$_base_dir/shared" && chmod 3770 "$_base_dir/shared"; }; then
            echo "  WARNING: could not provision $_base_dir/shared — shared-project creation will fail (403) until an administrator fixes it"
        fi
    done

    # Issue #3396 review (finding 8): the openace-chown wrapper's built-in
    # ALLOWED_PREFIXES only covers /workspace and /home — a deployment with a
    # custom WORKSPACE_BASE_DIR (e.g. /data) had EVERY revocation reclaim
    # rejected by the wrapper, fail-soft, forever. Write the operator-facing
    # override config from the configured base dirs (plus /home) so the
    # wrapper's path guard tracks this deployment's layout. Root-owned 0644;
    # the wrapper keeps its built-in defaults when the file is absent.
    # PR #3402 review: the conf is DATA, one prefix per line — never shell
    # code. The previous generation wrote a sourced `ALLOWED_PREFIXES=(...)`
    # assignment, so a `"` or `$(...)` inside WORKSPACE_BASE_DIR became code
    # the root-run wrapper would execute; the wrapper now stat-validates the
    # file (root-owned, not group/world-writable) and character-validates
    # every line ([A-Za-z0-9/._-] only), and this writer applies the same
    # character filter before emitting. The WRITE is part of the if-condition
    # (where set -e does not apply): a read-only /etc/openace mount or an
    # unwritable conf must degrade to the warning, never crash-loop the
    # container — bash 5 aborts under set -e on a failed `{ ...; } > file`
    # redirection (bash 3.2 does not, which is why the harness passed on
    # macOS but failed on CI).
    _chown_conf_dir="/etc/openace"
    _chown_conf="$_chown_conf_dir/openace-chown.conf"
    if mkdir -p "$_chown_conf_dir" 2>/dev/null && {
        echo "# Generated by docker-entrypoint.sh at boot — do not edit while running."
        echo "# Consumed by scripts/openace-chown.sh (allowed chown prefixes, one per line)."
        _wb_raw="${WORKSPACE_BASE_DIR:-/workspace}"
        IFS=',' read -r -a _wb_list <<< "$_wb_raw"
        for _wb in "${_wb_list[@]}"; do
            _wb="${_wb#"${_wb%%[![:space:]]*}"}"
            _wb="${_wb%"${_wb##*[![:space:]]}"}"
            [ -z "$_wb" ] && continue
            case "$_wb" in
                /*) ;;
                *) _wb="/$_wb" ;;
            esac
            # strip ALL trailing slashes, then skip what is left empty: the
            # old strip-then-compare-to-"/" never matched, so a "/" entry
            # produced a "/" prefix that disables the wrapper's path guard
            # entirely
            while [ "$_wb" != "${_wb%/}" ]; do _wb="${_wb%/}"; done
            [ -z "$_wb" ] && continue
            # only filesystem-safe characters are ever emitted: the wrapper
            # runs as root, and env-derived shell syntax must not reach it
            # (defense in depth — the wrapper re-validates every line)
            case "$_wb" in
                *[!A-Za-z0-9/._-]*) echo "  WARNING: skipping unsafe WORKSPACE_BASE_DIR entry '$_wb' in openace-chown.conf" >&2; continue ;;
            esac
            printf '%s/\n' "$_wb"
        done
        printf '/home/\n'
    } > "$_chown_conf"; then
        chmod 644 "$_chown_conf" 2>/dev/null || true
    else
        echo "  WARNING: could not write $_chown_conf — openace-chown stays on built-in prefixes (/workspace, /home)"
    fi
    unset _wb _wb_raw _wb_list _chown_conf_dir _chown_conf
    unset _base_dir _workspace_base_dirs

    # Fix /home directory permissions (Issue #1249)
    # When data/home is mounted as /home, restrictive 700 permissions prevent
    # users from accessing their own home directories. /home should be 755
    # (enterable by all), while /home/<user> remains 700 (private to user).
    if [ -d "/home" ]; then
        home_perms=$(stat -c "%a" /home 2>/dev/null || echo "unknown")
        if [ "$home_perms" != "755" ] && [ "$home_perms" != "unknown" ]; then
            chmod 755 /home
            echo "  Fixed /home permissions: $home_perms -> 755"
        fi
    fi

    # Sync workspace users from database to container
    # This creates OS users for each database user with system_account.
    # Issue #3390: accounts are pinned to their recorded users.system_uid
    # (useradd -u) and deactivated users get nologin placeholder accounts,
    # so uids are stable across container recreation and a deactivated
    # user's uid is never inherited by an active account.
    if [ -n "$DATABASE_URL" ]; then
        echo "Syncing workspace users from database..."
        # Review on #3390: the plain `python3 -c ... | tee LOG || echo` pipeline
        # masked python's exit status (tee returns 0; no pipefail file-wide) and
        # piped stdout is block-buffered — a #3399-style boot-context death made
        # all sync phases vanish silently with the WARNING never firing. The
        # subshell scopes pipefail to this one pipeline, -u unbuffers, and the
        # trailing || keeps set -e from crash-looping the service while making
        # any sync failure (including the missing-account check below) loud.
        ( set -o pipefail; python3 -u -c "
import os
import pwd
import subprocess
import sys
import psycopg2

workspace_base = os.environ.get('WORKSPACE_BASE_DIR', '/workspace')

def uid_owner(uid):
    \"\"\"Issue #3390: account NAME currently owning uid, or None if unassigned.\"\"\"
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None

def create_system_user(username, uid=None):
    \"\"\"Create a system user and workspace directory if they don't exist.

    Issue #3390 (uid pinning): uid is the recorded users.system_uid pin;
    useradd -u keeps the account on the same numeric uid across container
    recreations, so a fresh /etc/passwd can never hand this uid (and with it
    the numeric ownership of the volume dirs) to a different account.
    Returns the account's actual uid (for record-back) or None on failure.
    \"\"\"
    # Check if user exists
    result = subprocess.run(['id', username], capture_output=True, text=True)
    if result.returncode == 0:
        print(f'  User {username} already exists')
        # Issue #3390: an account that exists as a DEACTIVATED user's
        # placeholder (nologin) must be upgraded back to a login shell when
        # its own user is active again (reactivation across a recreate).
        try:
            shell = pwd.getpwnam(username).pw_shell
        except KeyError:
            shell = None
        if shell == '/usr/sbin/nologin':
            subprocess.run(['usermod', '-s', '/bin/bash', username], capture_output=True, text=True)
            print(f'  Upgraded placeholder account {username} to /bin/bash (reactivated user, #3390)')
    else:
        # Issue #3390 collision guard (CREATE path only): a pinned uid owned
        # by a DIFFERENT account means useradd would fail — or worse,
        # renumbering would silently move the boundary between two users'
        # files. Skip loudly for an admin; the pin stays recorded so the
        # next sync retries with it. (When the account already exists above,
        # the OS is the truth and the caller's drift branch converges the
        # record instead — no useradd, nothing to collide.)
        if uid is not None:
            owner = uid_owner(uid)
            if owner is not None and owner != username:
                print(f'  ERROR (issue #3390): recorded uid {uid} for {username} is already owned by {owner} — skipping account creation, resolve the conflict manually (not renumbering)')
                return None
        # Create user with home directory; -u pins the recorded uid (#3390)
        cmd = ['useradd', '-m', '-s', '/bin/bash']
        if uid is not None:
            cmd.extend(['-u', str(uid)])
        cmd.append(username)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            print(f'  Created user: {username}' + (f' (uid {uid})' if uid else ''))
        else:
            print(f'  Failed to create user {username}: {result.stderr}')
            return None

    # Create workspace directory for user (always attempt if user exists or was just created)
    user_workspace = os.path.join(workspace_base, username)
    if not os.path.exists(user_workspace):
        # Issue #3410: 0700, like /home/<user> — <base>/<account> is the fs
        # API's home root; at 0755 any other account could read the whole
        # workspace from a terminal or webui session.
        os.makedirs(user_workspace, mode=0o700, exist_ok=True)
        # Set ownership to user
        subprocess.run(['chown', f'{username}:{username}', user_workspace], capture_output=True)
        print(f'  Created workspace directory: {user_workspace}')
    elif username == 'shared':
        # <base>/shared is the shared-project NAMESPACE ROOT (root:openace-shared
        # 3770), not a user workspace — the same collision the provisioner at the
        # top of this script and _ensure_workspace_dirs both refuse. Normalizing
        # it to 0700 would break shared-project creation for every tenant.
        print(f'  WARNING: skipping mode normalization for {user_workspace} '
              f'(shared-project namespace root, not a user workspace)')
    else:
        # Issue #3410: converge volumes created before the 0700 default, but
        # only when the directory really belongs to this account.
        try:
            if os.stat(user_workspace).st_uid == pwd.getpwnam(username).pw_uid:
                os.chmod(user_workspace, 0o700)
            else:
                # A restored volume with a stale uid: loud, like every other
                # skip around here — a silent one leaves it 0755 and wrong-
                # owned through every boot until the account's first login.
                print(f'  WARNING: {user_workspace} is owned by uid '
                      f'{os.stat(user_workspace).st_uid}, not {username} '
                      f'({pwd.getpwnam(username).pw_uid}); skipping mode '
                      f'normalization (chown it or re-pin the uid)')
        except (OSError, KeyError) as e:
            print(f'  WARNING: cannot chmod 0700 {user_workspace}: {e}')

    # Fix home directory permissions (Issue #1205)
    # When /home is mounted as volume, useradd -m won't fix permissions on existing directory
    user_home = f'/home/{username}'
    if os.path.isdir(user_home):
        # Check if ownership is correct before running chown (Issue #1209 review)
        stat_result = subprocess.run(['stat', '-c', '%U:%G', user_home], capture_output=True, text=True)
        current_owner = stat_result.stdout.strip()
        expected_owner = f'{username}:{username}'

        if current_owner != expected_owner:
            subprocess.run(['chown', '-R', f'{username}:{username}', user_home], capture_output=True)
            print(f'  Fixed home directory permissions: {user_home}')
        else:
            print(f'  Home directory ownership correct: {user_home}')

    # Sync SSH keys if mounted (Issue #1122)
    # 【安全加固 Issue #2182 + #2328】使用独立的 Python 脚本实现安全同步（fail-closed）
    sync_ssh_keys_secure(username)

    try:
        return pwd.getpwnam(username).pw_uid
    except KeyError:
        return None

def create_placeholder_user(username, uid):
    \"\"\"Issue #3390: placeholder account for a deactivated/soft-deleted user.

    A fresh container re-useradds only what this sync creates; without a
    placeholder, useradd's sequential uid assignment hands this user's old
    uid (and so the numeric ownership of their 0700 /home/<user> and
    /workspace/<user>) to whichever active account lands on the number.
    The placeholder is a nologin shell that only reserves the uid — home
    dirs and workspaces are NOT created, chowned, or ssh-synced (the
    volume dirs already carry this numeric owner; stat -c %U reports the
    placeholder name from then on).
    \"\"\"
    result = subprocess.run(['id', username], capture_output=True, text=True)
    if result.returncode == 0:
        print(f'  Placeholder user {username} already exists (recorded uid {uid})')
        return
    owner = uid_owner(uid)
    if owner is not None and owner != username:
        # Name conflict with a live account (admin reused the account name)
        # or a duplicate recorded pin — either way an admin decision, never
        # renumber. The deactivated user's dirs stay on the orphan uid
        # (owner UNKNOWN), which the isolation checks treat as safe.
        print(f'  WARNING (issue #3390): recorded uid {uid} for deactivated user {username} is owned by {owner} — placeholder skipped, not renumbering')
        return
    result = subprocess.run(['useradd', '-u', str(uid), '-s', '/usr/sbin/nologin', username], capture_output=True, text=True)
    if result.returncode == 0:
        print(f'  Created placeholder user: {username} (uid {uid}, nologin)')
    else:
        print(f'  Failed to create placeholder user {username}: {result.stderr}')


def sync_ssh_keys_secure(username):
    \"\"\"
    安全同步 SSH 密钥（Issue #2182 + #2328 - fail-closed）

    使用独立的 Python 脚本 /usr/local/bin/openace-ssh-sync 实现：
    - 白名单机制：默认只允许 known_hosts 等安全文件
    - 禁止清单：明确禁止私钥、证书、socket、token 等危险文件
    - TOCTOU 防护：使用文件描述符操作防止竞态条件
    - 硬链接检测：防止通过硬链接绕过 symlink 检测
    - Owner/Group 验证：确保同步文件的 owner 正确
    - 审计日志：记录所有同步操作
    - 升级检测：检测并处理旧版本复制的私钥

    FAIL-CLOSED: 当脚本不可用或失败时，不执行任何同步，返回失败状态。
    Open ACE 不会将 root 私钥传播给工作区用户。

    Returns:
        0: Success
        1: Failure (script missing/failed/timed out/exception)
    \"\"\"
    root_ssh = '/root/.ssh'
    user_ssh = f'/home/{username}/.ssh'

    # Skip if SSH keys not mounted
    if not os.path.isdir(root_ssh):
        return 0

    ssh_sync_script = '/usr/local/bin/openace-ssh-sync'

    # Validate script exists and is executable
    if not os.path.isfile(ssh_sync_script):
        _log_sync_failure(username, \"script_missing\",
                          f\"{ssh_sync_script} not found\")
        return 1

    if not os.access(ssh_sync_script, os.X_OK):
        _log_sync_failure(username, \"script_not_executable\",
                          f\"{ssh_sync_script} not executable\")
        return 1

    # Execute secure sync
    try:
        upgrade_action = os.environ.get('OPENACE_SSH_UPGRADE_ACTION', 'backup')
        timeout_seconds = int(os.environ.get('OPENACE_SSH_SYNC_TIMEOUT_SECONDS', '30'))

        result = subprocess.run(
            [
                ssh_sync_script,
                '--user', username,
                '--upgrade-action', upgrade_action,
            ],
            capture_output=True,
            text=True,
            timeout=timeout_seconds
        )

        if result.returncode == 0:
            print(f'  SSH keys securely synced to {user_ssh}')
            if result.stdout:
                print(f'    {result.stdout.strip()}')
            return 0
        else:
            _log_sync_failure(username, \"script_failed\", result.stderr.strip())
            return 1

    except subprocess.TimeoutExpired:
        _log_sync_failure(username, \"script_timeout\",
                          f\"Script execution exceeded {timeout_seconds} seconds\")
        return 1

    except Exception as e:
        _log_sync_failure(username, \"script_exception\", str(e))
        return 1


def _log_sync_failure(username, reason, details):
    \"\"\"Log sync failure to multiple channels for operational visibility\"\"\"
    import json
    import sys
    from datetime import datetime

    timestamp = datetime.now().isoformat()

    # 1. Structured JSON log (machine-readable)
    log_entry = {
        \"timestamp\": timestamp,
        \"event\": \"SSH_SYNC_FAILURE\",
        \"user\": username,
        \"reason\": reason,
        \"details\": details,
        \"severity\": \"ERROR\",
        \"remediation\": _get_remediation_hint(reason)
    }

    try:
        log_file = \"/var/log/openace/ssh-sync-failure.json\"
        os.makedirs(os.path.dirname(log_file), exist_ok=True)

        with open(log_file, \"a\") as f:
            f.write(json.dumps(log_entry) + \"\n\")
    except Exception:
        # Fallback to stderr if file logging fails
        pass

    # 2. Human-readable warning file (for operators)
    try:
        warning_file = \"/var/log/openace/ssh-sync-failure.warning\"
        os.makedirs(os.path.dirname(warning_file), exist_ok=True)

        with open(warning_file, \"w\") as f:
            f.write(f\"[{timestamp}] SSH Sync Failure\n\")
            f.write(f\"User: {username}\n\")
            f.write(f\"Reason: {reason}\n\")
            f.write(f\"Details: {details}\n\")
            remediation = _get_remediation_hint(reason)
            f.write(f\"\nRemediation:\n{remediation}\n\")
    except Exception:
        # Fallback to stderr if warning file creation fails
        pass

    # 3. Console output
    print(f\"  ERROR: SSH key sync failed for {username}\", file=sys.stderr)
    print(f\"  Reason: {reason}\", file=sys.stderr)
    print(f\"  Details: {details}\", file=sys.stderr)
    print(f\"  See /var/log/openace/ssh-sync-failure.warning for details\", file=sys.stderr)


def _get_remediation_hint(reason):
    \"\"\"Return remediation guidance based on failure reason\"\"\"
    hints = {
        \"script_missing\": (
            \"Ensure /usr/local/bin/openace-ssh-sync is installed.\n\"
            \"For Docker: ensure the script is COPYed in Dockerfile.\n\"
            \"For package installation: ensure the package installs the script.\"
        ),
        \"script_not_executable\": (
            \"Run: chmod +x /usr/local/bin/openace-ssh-sync\"
        ),
        \"script_failed\": (
            \"Check /var/log/openace/ssh-sync.log for details.\n\"
            \"Common causes: permission errors, invalid whitelist config.\"
        ),
        \"script_timeout\": (
            \"Script took too long. Check for:\n\"
            \"- Large number of files in /root/.ssh\n\"
            \"- Slow filesystem\n\"
            \"- Increase timeout via OPENACE_SSH_SYNC_TIMEOUT_SECONDS\"
        ),
        \"script_exception\": (
            \"Unexpected error. Check:\n\"
            \"- Python version >= 3.10\n\"
            \"- PyYAML package installed\n\"
            \"- /var/log/openace/ directory writable\"
        )
    }
    return hints.get(reason, \"Check logs for details.\")

try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # Get all users with system_account or username.
    # Issue #3390: pull the recorded uid pin (users.system_uid) alongside the
    # account so re-creation lands on the same numeric uid, and ALSO include
    # deactivated/soft-deleted users that still have a pin — their placeholder
    # accounts (below) keep that uid from being reassigned to an active
    # account. ORDER BY id keeps the sync deterministic across recreations.
    cur.execute(
        'SELECT id, username, system_account, system_uid, is_active, deleted_at '
        'FROM users '
        'WHERE (deleted_at IS NULL AND is_active = true) OR system_uid IS NOT NULL '
        'ORDER BY id'
    )
    rows = cur.fetchall()

    # Classify (#3390): active rows get real accounts; every other row with a
    # recorded pin gets a nologin placeholder so the uid is never inherited.
    active_rows = []
    placeholder_rows = []
    for row_id, username, system_account, system_uid, is_active, deleted_at in rows:
        account = system_account or username
        if not account:
            continue
        if deleted_at is None and is_active:
            active_rows.append((row_id, username, account, system_uid))
        elif system_uid is not None:
            placeholder_rows.append((account, system_uid))

    # Build a mapping of username -> system_account for owner lookup
    user_mapping = {username: account for row_id, username, account, uid in active_rows}

    # Issue #3390 ordering — pinned claims before any auto-assigned useradd:
    #   1. active users WITH a recorded pin (deterministic uids, and the
    #      account name wins any collision with a deactivated row),
    #   2. placeholder accounts reserving deactivated users' uids,
    #   3. active users WITHOUT a pin — useradd auto-assigns from the
    #      remaining free uids (it can no longer land on a reserved pin),
    #      and the assigned uid is recorded back so phase 1 covers them on
    #      every future recreation.
    for row_id, username, account, recorded_uid in active_rows:
        if recorded_uid is None:
            continue
        actual_uid = create_system_user(account, uid=recorded_uid)
        if actual_uid is not None and actual_uid != recorded_uid:
            cur.execute('UPDATE users SET system_uid = %s WHERE id = %s', (actual_uid, row_id))
            print(f'  Updated recorded uid for {account}: {recorded_uid} -> {actual_uid} (#3390 drift)')

    for account, uid in placeholder_rows:
        create_placeholder_user(account, uid)

    for row_id, username, account, recorded_uid in active_rows:
        if recorded_uid is not None:
            continue
        actual_uid = create_system_user(account)
        if actual_uid is not None:
            cur.execute('UPDATE users SET system_uid = %s WHERE id = %s', (actual_uid, row_id))
            print(f'  Recorded uid {actual_uid} for {account} (#3390 pin bootstrap)')

    # Persist the pins before the project-dir pass — a failure there must
    # not lose them (the connection is otherwise read-only until close).
    conn.commit()

    # Review on #3390 (missing-actives verification): a collision skip, a
    # useradd failure, or a mid-loop exception can each leave an ACTIVE user
    # without an OS account — that user cannot log in to a workspace at all.
    # Verify loudly and exit nonzero: the pipefail wrapper in the entrypoint
    # turns this into the WARNING line (visible in container logs) without
    # crash-looping the service. Review round 2: the exit happens only AFTER
    # the project-dir pass below — that section already tolerates missing
    # owners per-project (Warning + skip), so one failed account must not
    # block every other user's project directories on every boot. The pins
    # above were already committed, so the next recreation retries from the
    # recorded state.
    missing_actives = []
    for row_id, username, account, recorded_uid in active_rows:
        try:
            pwd.getpwnam(account)
        except KeyError:
            missing_actives.append(account)
    if missing_actives:
        print(f'  ERROR (issue #3390): active users WITHOUT an OS account after sync: {sorted(missing_actives)} — see the collision/failure lines above; resolve manually')

    # Sync project directories from database (Issue #1083)
    print('Syncing project directories...')
    cur.execute('SELECT path FROM projects WHERE is_active = true')
    project_rows = cur.fetchall()

    for project_path in project_rows:
        path = project_path[0]
        # Only process paths under workspace_base
        if path.startswith(workspace_base):
            if not os.path.exists(path):
                # Infer owner from path: /workspace/<owner>/...
                parts = path.split('/')
                # workspace_base is '/workspace' by default, so parts[1] == 'workspace' covers both cases
                if len(parts) >= 3 and parts[1] == 'workspace':
                    # Get the user directory name (second or third component)
                    owner_candidate = parts[2] if len(parts) > 2 else None
                    # Try to find owner from user_mapping or directly
                    owner = user_mapping.get(owner_candidate, owner_candidate)
                    try:
                        pw_info = pwd.getpwnam(owner)
                        os.makedirs(path, exist_ok=True)
                        subprocess.run(['chown', '-R', f'{owner}:{owner}', path], capture_output=True)
                        print(f'  Created project directory: {path} (owner: {owner})')
                    except KeyError:
                        print(f'  Warning: User {owner} not found for path {path}, skipping')
            else:
                print(f'  Project directory exists: {path}')

    conn.close()
    if missing_actives:
        # exit nonzero only AFTER the project-dir pass (review on #3390:
        # one failed account must not block everyone's project sync) —
        # the pipefail wrapper turns this into the WARNING line.
        sys.exit(1)
    print('User and project sync completed.')
except Exception as e:
    print(f'Error syncing users and projects: {e}')
    # nonzero, or the pipefail wrapper never fires the WARNING (#3399-style
    # silent death: a mid-stage exception — UPDATE failure, connection drop,
    # makedirs PermissionError on root_squash NFS — used to end as exit 0,
    # skipping conn.commit() and the missing-actives verification).
    sys.exit(1)
" 2>&1 | tee /app/logs/open-ace-user-sync.log ) || echo "WARNING: User sync failed - check /app/logs/open-ace-user-sync.log for details"
    fi

    # ========================================================================
    # Issue #3390 declared residual, now closed: first-boot orphan-uid
    # adoption.
    # ========================================================================
    # After an upgrade, a DEACTIVATED/soft-deleted user from BEFORE the pin
    # era has no recorded uid (their rows are never re-synced), so the sync
    # above creates no placeholder for them: their volume dirs (/home/<user>,
    # <base>/<user>, <base>/shared leftovers) sit on uids no account owns
    # (owner "UNKNOWN"), and a future account's auto-assigned useradd can
    # numerically inherit them. Close the residual by scanning the volume
    # trees AFTER the user-sync (its pinned accounts must exist first, or
    # every pin of a recreated deployment would look orphaned) and reserving
    # each unowned uid >= 1000 as a nologin placeholder account
    # openace-orphan-<uid> — from then on useradd can never hand that uid
    # (and with it the numeric ownership of the orphaned dirs) to anyone.
    # Idempotent: an adopted uid resolves via getent on the next boot and is
    # skipped silently. Quoted heredoc (verbatim python, no shell expansion
    # — the #3399 class cannot recur here); failure degrades to the WARNING
    # line, never aborts the boot.
    if [ -n "$DATABASE_URL" ]; then
    ( set -o pipefail; python3 -u - <<'ORPHAN_UID_SCAN_EOF' 2>&1 | tee /app/logs/open-ace-orphan-uid-scan.log ) || echo "WARNING: orphan-uid adoption scan failed - unreserved orphan uids may be inherited by a future account; check /app/logs/open-ace-orphan-uid-scan.log"
import os
import subprocess


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


# Scan roots: /home plus every configured workspace base dir (deduped) —
# the trees the user-sync and the app create per-user content in.
bases = [b.strip().rstrip('/') for b in os.environ.get('WORKSPACE_BASE_DIR', '/workspace').split(',') if b.strip()]
roots = []
for root in ['/home'] + bases:
    if root and root not in roots:
        roots.append(root)

# Collect owner uids of existing entries (dirs AND files) up to depth 3 —
# a user's home/workspace plus their immediate project trees. find -exec
# stat {} + batches the stat calls (one fork per batch, not per entry).
observed_uids = set()
for root in roots:
    if not os.path.isdir(root):
        continue
    r = run(['find', root, '-maxdepth', '3', '-exec', 'stat', '-c', '%u', '{}', '+'])
    if r.returncode != 0:
        print(f'  WARNING (issue #3390): orphan-uid scan could not walk {root}: {r.stderr.strip()}')
        continue
    for token in r.stdout.split():
        try:
            uid = int(token)
        except ValueError:
            continue
        observed_uids.add(uid)

# Adopt: reserve every observed uid >= 1000 that no account owns (getent
# passwd by uid fails) as a nologin placeholder. useradd -M creates no home;
# the volume dirs already carry this numeric owner, and stat -c %U reports
# the placeholder name from then on.
adopted = 0
for uid in sorted(u for u in observed_uids if u >= 1000):
    if run(['getent', 'passwd', str(uid)]).returncode == 0:
        continue  # owned by an account (or already reserved by a placeholder)
    name = f'openace-orphan-{uid}'
    r = run(['useradd', '-M', '-s', '/usr/sbin/nologin', '-u', str(uid), name])
    if r.returncode == 0:
        adopted += 1
    else:
        print(f'  WARNING (issue #3390): could not reserve orphan uid {uid} as {name}: {r.stderr.strip()} — the uid may be handed to a future account')
if adopted:
    print(f'Adopted {adopted} orphan uid(s) from volumes as reserved placeholders.')
ORPHAN_UID_SCAN_EOF
    fi

    # ========================================================================
    # Issue #3396: tenant-scoped shared-group sync (DB-driven).
    # ========================================================================
    # Replaces the two /home-glob usermod passes (#3389 rounds 2/3): a
    # directory name cannot reveal its tenant, so enrollment is derived from
    # the users table instead. For every ACTIVE user with an account:
    #   - global openace-shared (namespace-root creation right), and
    #   - openace-shared-<tenant_id> (tenant shared-content access).
    # PR #3402 review: the sync is also AUTHORITATIVE for tenant-group
    # MEMBERSHIP — every existing openace-shared-<t> group is converged onto
    # the DB's exact member list (gpasswd -M / -d), so stale memberships
    # (tenant moves, deactivations, deletes — whose removals used to run only
    # in the request-handling container) cannot survive a restart of ANY
    # container, the scheduler's included. The global openace-shared group is
    # deliberately left alone (deactivated accounts keep namespace-root
    # creation; no content access).
    # It also RECONCILES shared project directories left on the legacy
    # global group by pre-#3396 deployments: chgrp to the tenant group +
    # 2770/660. The reconcile is skipped per project when the root already
    # carries the tenant group AND mode (one stat per project on
    # steady-state boots), and RECLAIMS directories of projects that are no
    # longer active+shared (revocation whose reclaim failed fail-soft after
    # the DB flip, soft-deleted shared projects) back to the creator:
    # chown -R + dirs 0700 / files 0600.
    # Quoted heredoc: the block is verbatim Python (no shell expansion) and
    # is functionally tested by tests/unit/test_shared_namespace_provisioning_3379.py
    # (extracted between the PY_SYNC_GROUPS_EOF markers).
    # Review hardening (matches the #3390 user-sync pattern): the old plain
    # `python3 - <<EOF ... | tee LOG || echo WARNING` pipeline masked a
    # python-side death (tee returns 0; no pipefail file-wide) and the
    # heredoc python's outermost handler caught every exception and exited
    # 0 — so the WARNING could never fire. pipefail is now scoped to this
    # one pipeline, -u unbuffers, and the python itself exits 1 when any
    # enrollment/reconcile/reclaim step failed (per-row failures are still
    # skipped past so one bad row cannot abort the rest) — the WARNING line
    # is the loud signal that cross-tenant OS isolation may be degraded
    # until the next restart; the entrypoint itself keeps booting.
    if [ -n "$DATABASE_URL" ]; then
    ( set -o pipefail; python3 -u - <<'PY_SYNC_GROUPS_EOF' 2>&1 | tee /app/logs/open-ace-shared-groups.log ) || echo "WARNING: shared-group sync failed - cross-tenant OS isolation may be degraded until restart; check /app/logs/open-ace-shared-groups.log"
import os
import subprocess
import sys

import psycopg2

GLOBAL_GROUP = 'openace-shared'


def tenant_group(tid):
    # Mirrors app.utils.workspace.shared_tenant_group_name: NULL tenant
    # (platform admins) maps to the pseudo-id 0; real tenant ids start at 1.
    return f"openace-shared-{tid if tid is not None else 0}"


def run(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def enroll(username, tid):
    """Enroll one account in the global + tenant shared groups (idempotent).

    Returns True when every membership is in place.
    """
    ok = True
    for group in (GLOBAL_GROUP, tenant_group(tid)):
        r = run(['groupadd', '-f', group])
        if r.returncode != 0:
            print(f'  WARNING: groupadd {group} failed: {r.stderr.strip()}')
            ok = False
            continue
        r = run(['usermod', '-aG', group, username])
        if r.returncode != 0:
            print(f'  WARNING: usermod -aG {group} {username} failed: {r.stderr.strip()}')
            ok = False
    return ok


def reconcile_shared(path, tid):
    """Normalize one shared project dir to the tenant group (2770/660).

    The fast path checks group AND mode: a dir chgrp'd correctly but left on
    a wrong mode (e.g. legacy 2775) must still be normalized, not skipped.
    Returns True when the dir ends up normalized.
    """
    group = tenant_group(tid)
    r = run(['groupadd', '-f', group])
    if r.returncode != 0:
        print(f'  WARNING: groupadd {group} failed: {r.stderr.strip()}')
        return False
    stat = run(['stat', '-c', '%G %a', path])
    if stat.returncode == 0:
        parts = stat.stdout.strip().split()
        if len(parts) == 2 and parts[0] == group and parts[1] == '2770':
            return True  # already normalized (steady-state fast path)
    print(f'  Reconciling shared project {path} -> group {group} (2770/660)')
    ok = True
    r = run(['chgrp', '-R', group, path])
    if r.returncode != 0:
        print(f'  WARNING: chgrp {group} {path} failed: {r.stderr.strip()}')
        ok = False
    # PR #3402 review: batch the chmod passes with `-exec ... {} +` (one
    # chmod invocation per find BATCH, not one fork per entry — `{} ;`
    # forked a chmod per file, so a node_modules-scale project could keep
    # the pre-service-start reconcile running for tens of minutes and kill
    # the container via healthcheck).
    r = run(['find', path, '-type', 'd', '-exec', 'chmod', '2770', '{}', '+'])
    if r.returncode != 0:
        print(f'  WARNING: chmod 2770 pass failed for {path}: {r.stderr.strip()}')
        ok = False
    r = run(['find', path, '-type', 'f', '-exec', 'chmod', '660', '{}', '+'])
    if r.returncode != 0:
        print(f'  WARNING: chmod 660 pass failed for {path}: {r.stderr.strip()}')
        ok = False
    return ok


def reclaim_revoked(path, owner):
    """Re-apply the #3396 revocation reclaim for one no-longer-shared dir.

    The app-side revoke runs fail-soft AFTER the DB flip, so a timeout or
    crash leaves the dir group-accessible with is_shared already false —
    the whole tenant would keep OS access to a now-private project forever.
    Mirrors app.utils.workspace.revoke_shared_project_access: chown -R to
    the creator, then dirs 0700 / files 0600 (ex-members get EACCES).
    Returns True when the reclaim completed.
    """
    uid = run(['id', '-u', owner])
    gid = run(['id', '-g', owner])
    if uid.returncode != 0 or gid.returncode != 0:
        print(f'  WARNING: cannot resolve owner {owner} for {path}: '
              f'{(uid.stderr or gid.stderr).strip()}; not reclaimed')
        return False
    ownership = f'{uid.stdout.strip()}:{gid.stdout.strip()}'
    print(f'  Reclaiming revoked shared project {path} -> owner {owner} (0700/0600)')
    r = run(['chown', '-R', ownership, path])
    if r.returncode != 0:
        print(f'  WARNING: chown {ownership} {path} failed: {r.stderr.strip()}')
        return False
    ok = True
    # batched `{} +` like the reconcile pass above (PR #3402 review: one
    # fork per entry kept large reclaims in the pre-service-start window)
    r = run(['find', path, '-type', 'd', '-exec', 'chmod', '0700', '{}', '+'])
    if r.returncode != 0:
        print(f'  WARNING: chmod 0700 pass failed for {path}: {r.stderr.strip()}')
        ok = False
    r = run(['find', path, '-type', 'f', '-exec', 'chmod', '0600', '{}', '+'])
    if r.returncode != 0:
        print(f'  WARNING: chmod 0600 pass failed for {path}: {r.stderr.strip()}')
        ok = False
    return ok


failures = 0
try:
    conn = psycopg2.connect(os.environ['DATABASE_URL'])
    cur = conn.cursor()

    # Round-2 review N1: user_repo.delete_user soft-deletes by setting
    # deleted_at ONLY (is_active stays true), so the enrollment must filter
    # deleted_at IS NULL — same classification as the user-sync above — or
    # every restart re-enrolls deleted users into openace-shared-<t>, silently
    # undoing the delete-path group drop app/routes/admin.py performs.
    # PR #3402 review: system_account ONLY — no `or username` fallback. An
    # unmapped user's username may equal ANOTHER user's system_account
    # (system_account validates format only, uniqueness is not checked
    # against usernames), and the old fallback enrolled that OS account into
    # this user's tenant group across the tenant boundary; username is no
    # longer selected so the fallback cannot come back silently.
    cur.execute(
        'SELECT system_account, tenant_id FROM users '
        'WHERE is_active = true AND deleted_at IS NULL'
    )
    # PR #3402 review: the sync must also REMOVE, not just add. Tenant moves,
    # deactivations and deletes drop the old group with fail-soft `gpasswd -d`
    # in the REQUEST-handling container only — the scheduler container's
    # /etc/group (its autonomous agents run via openace-run-as against it)
    # kept every stale membership forever, and a failed removal in the app
    # container survived its restarts. The DB is therefore the authority:
    # `desired` holds the exact member list per tenant group, and the pass
    # below converges every existing openace-shared-<t> group onto it. The
    # GLOBAL openace-shared group is deliberately NOT converged (deactivated
    # accounts keep it — accepted residual: it grants namespace-root
    # creation only, no content access).
    desired = {}
    for system_account, tid in cur.fetchall():
        if not system_account:
            continue
        if not enroll(system_account, tid):
            failures += 1
        desired.setdefault(tenant_group(tid), set()).add(system_account)
    print('Shared-group enrollment complete.')

    getent = run(['getent', 'group'])
    if getent.returncode != 0:
        print(f'  WARNING: getent group failed: {getent.stderr.strip()} — '
              'stale tenant-group memberships NOT reconciled this boot')
        failures += 1
    else:
        for line in getent.stdout.splitlines():
            name = line.split(':', 1)[0]
            if not name.startswith(GLOBAL_GROUP + '-'):
                continue  # foreign groups and the global group itself
            suffix = name[len(GLOBAL_GROUP) + 1:]
            if not (suffix.isascii() and suffix.isdigit()):
                # round-5 N2: tenant group suffixes are numeric by
                # construction — an operator-created lookalike such as
                # openace-shared-backup must never be converged (its
                # members would be stripped one by one). isascii() is
                # load-bearing: str.isdigit() alone ACCEPTS non-ASCII
                # digits (e.g. fullwidth '１２３'), and a root-created
                # openace-shared-<unicode-digits> group would pass the
                # guard and be converged to the empty list.
                continue
            # round-5 N4: a desired member whose OS account is missing this
            # boot (user-sync failure, #3399 shape) makes shadow-utils
            # reject the WHOLE gpasswd -M call, silently keeping the group's
            # stale list. Converge the present subset instead — the missing
            # account's absence is already loud in the user-sync log.
            # NSS transient hardening: getent rc==0 -> the account is
            # present; rc==2 -> genuinely absent (getent's documented
            # not-found); any OTHER rc is a transient NSS failure (socket
            # timeout, sssd restart, nscd hiccup) — retry once, and if it
            # STAYS ambiguous skip this group's convergence entirely this
            # boot with a loud warning: a list built on an unreliable
            # answer would either poison gpasswd -M with a missing account
            # (N4 rejection, stale list kept) or silently strip a member
            # who is actually present. The stale-but-valid membership waits
            # one boot instead; the convergence is retried automatically.
            members = []
            ambiguous = False
            for m in sorted(desired.get(name, ())):
                rc = run(['getent', 'passwd', m]).returncode
                if rc not in (0, 2):
                    rc = run(['getent', 'passwd', m]).returncode
                if rc == 0:
                    members.append(m)
                elif rc == 2:
                    continue
                else:
                    ambiguous = True
            if ambiguous:
                print(f'  WARNING: NSS lookup for a member of {name} still ambiguous after retry — '
                      'membership convergence skipped this boot (current members kept; retried next boot)')
                continue
            if members:
                r = run(['gpasswd', '-M', ','.join(members), name])
                if r.returncode != 0:
                    print(f'  WARNING: gpasswd -M {name} failed: {r.stderr.strip()}')
                    failures += 1
            elif line.count(':') >= 3 and line.split(':', 3)[3].strip():
                # shadow-utils `gpasswd -M ""` is a NO-OP (it does not clear
                # the list), so a group whose desired set is EMPTY — every
                # member moved/deleted/deactivated — must have its current
                # members removed one by one.
                for member in line.split(':', 3)[3].split(','):
                    member = member.strip()
                    if not member:
                        continue
                    r = run(['gpasswd', '-d', member, name])
                    if r.returncode != 0:
                        print(f'  WARNING: gpasswd -d {member} {name} failed: {r.stderr.strip()}')
                        failures += 1

    bases = [b.strip().rstrip('/') for b in os.environ.get('WORKSPACE_BASE_DIR', '/workspace').split(',') if b.strip()]
    cur.execute('SELECT path, tenant_id FROM projects WHERE is_active = true AND is_shared = true')
    # active_shared_paths is collected BEFORE any filtering (PR #3402 review):
    # the reclaim pass below must refuse to touch anything that IS, CONTAINS
    # or LIES INSIDE any live shared project, of any tenant.
    active_shared_paths = []
    for path, tid in cur.fetchall():
        if not path:
            continue
        active_shared_paths.append(path.rstrip('/'))
        # Only reconcile paths inside the configured workspace base dirs —
        # rows pointing elsewhere are not ours to touch.
        if not any(path == b or path.startswith(b + '/') for b in bases):
            continue
        if os.path.isdir(path):
            if os.path.islink(path):
                # round-5 N1: a symlink row is never a legitimate shared
                # project root (registrations realpath at creation). Every
                # check here is string-based and stat/find/chgrp would
                # follow the link onto its TARGET — refuse, loudly.
                failures += 1
                print(f'  WARNING: shared project path {path} is a symlink — '
                      'not reconciled; investigate (possible tampering)')
                continue
            try:
                if not reconcile_shared(path, tid):
                    failures += 1
            except Exception as e:  # noqa: BLE001 - one bad row must not abort the rest
                failures += 1
                print(f'  WARNING: reconcile failed for {path}: {e}')

    # Issue #3396 review: RECLAIM pass. Projects that are NOT active+shared
    # (revoked is_shared=false, soft-deleted is_active=false — soft delete
    # flips is_active, projects has no deleted_at column) but whose dir is
    # still group-owned by their tenant group are leftover shared-state on
    # disk; the app-side revoke ran fail-soft after the DB flip, and
    # soft-DELETE of a shared project never reclaims at all.
    #
    # PR #3402 review (takeover hardening): private project registration has
    # NO path-ownership validation (app/routes/projects.py only validates
    # `if is_shared:`), and get_project_by_path is tenant-scoped, so ANY
    # tenant's user could register another tenant's shared dir — or the
    # <base>/shared namespace root itself — as a private project and have
    # this pass chown -R it to them at the next boot. A dir is therefore
    # reclaimed ONLY when it is provably THIS row's own shared leftover:
    #   * strictly INSIDE a workspace base dir, and never a base dir or a
    #     <base>/shared namespace root (the container's own plumbing);
    #   * not overlapping (equal to / containing / inside) ANY live shared
    #     project of ANY tenant;
    #   * on THIS row's tenant group (openace-shared-<row.tenant_id>) — the
    #     only group that proves this tenant shared it;
    #   * a legacy pre-#3396 leftover on the GLOBAL group is reclaimed only
    #     at the exact first-level <base>/shared/<name> shape those
    #     deployments laid out (anything else on the global group cannot be
    #     distinguished from namespace plumbing — declared residual for an
    #     administrator);
    #   * the owner is the row's system_account ONLY (no username fallback —
    #     see the enrollment note above).
    # Steady-state cost stays one isdir + one stat per not-shared row.
    namespace_roots = {b + '/shared' for b in bases}

    def overlaps(a, b):
        return a == b or a.startswith(b + '/') or b.startswith(a + '/')

    def first_level_shared_child(p):
        # direct child of a <base>/shared namespace root (no deeper '/')
        return any(
            p.startswith(ns + '/') and '/' not in p[len(ns) + 1:] for ns in namespace_roots
        )

    cur.execute(
        'SELECT p.path, p.tenant_id, u.system_account FROM projects p '
        'LEFT JOIN users u ON p.created_by = u.id '
        'WHERE NOT (p.is_active = true AND p.is_shared = true)'
    )
    for path, tid, system_account in cur.fetchall():
        path = (path or '').rstrip('/')
        if not path:
            continue
        # strictly inside a base dir; never a base dir or a namespace root
        if not any(path.startswith(b + '/') for b in bases) or path in namespace_roots:
            continue
        # round-5 N1: a symlink can never be this row's own leftover —
        # registrations realpath, so a legit project dir is a real dir. All
        # checks below are string-based and isdir/stat/chown -R follow the
        # link: without this guard a same-tenant private row aliased via
        # `ln -s <base>/shared/<live-project> <own-path>` passes every
        # check, and chown -R (which dereferences its command-line operand)
        # hands the LIVE project's root to the attacker.
        if os.path.islink(path):
            continue
        # never reclaim a dir that is, contains, or lies inside a LIVE
        # shared project (any tenant) — that is a takeover, not a leftover
        if any(overlaps(path, live) for live in active_shared_paths):
            continue
        if not system_account:
            continue
        if not os.path.isdir(path):
            continue
        try:
            stat = run(['stat', '-c', '%G', path])
            if stat.returncode != 0:
                continue
            group = stat.stdout.strip()
            if group == tenant_group(tid):
                pass  # this row's own tenant group — provably its leftover
            elif group == GLOBAL_GROUP and first_level_shared_child(path):
                pass  # legacy pre-#3396 leftover at the canonical shape
            else:
                # another tenant's group, an unshaped legacy global-group dir,
                # or a dir that was never group-shared / is already reclaimed
                continue
            if not reclaim_revoked(path, system_account):
                failures += 1
        except Exception as e:  # noqa: BLE001 - one bad row must not abort the rest
            failures += 1
            print(f'  WARNING: reclaim failed for {path}: {e}')

    conn.close()
    print('Shared-group sync completed.')
    if failures:
        print(f'Shared-group sync finished with {failures} failure(s).')
        sys.exit(1)
except Exception as e:
    print(f'Error syncing shared groups: {e}')
    sys.exit(1)
PY_SYNC_GROUPS_EOF
    fi

    # Configure sudoers for qwen-code-webui
    # Allow open-ace (container user) and openace (workspace user) to run as any workspace user
    # NOTE: Commands must have '*' suffix to allow arguments (e.g., 'test -r', 'ls -1')
    WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")

    # Dynamic path resolution for git and gh (validated in validate_node_environment)
    GIT_PATH=$(which git 2>/dev/null || echo "/usr/bin/git")
    GH_PATH=$(which gh 2>/dev/null || echo "/usr/bin/gh")

    if [ -x "$WEBUI_PATH" ]; then
        # 【修复 Issue #1395】autonomous 开发所需的 git/gh/CLI 工具 + run-as wrapper
        # wrapper 必须存在才注入对应规则（与 Dockerfile COPY 保持一致）
        WRAPPER_PATH="/usr/local/bin/openace-run-as"
        WRAPPER_RULE=""
        if [ -x "$WRAPPER_PATH" ]; then
            WRAPPER_RULE="open-ace ALL=(root) NOPASSWD: ${WRAPPER_PATH} --isolated *
openace ALL=(root) NOPASSWD: ${WRAPPER_PATH} --isolated *"
        fi

        # 【安全加固 Issue #1855 + #2181】安全 wrapper 脚本 sudoers 规则
        # 使用 wrapper 替代通配命令，wrapper 内部做参数校验和审计日志
        # Issue #2181: 添加 openace-rm wrapper 替代 rm * 通配
        SECURITY_WRAPPERS_RULE=""
        for wrapper in openace-chown openace-useradd openace-cat openace-mkdir openace-write-as openace-rm; do
            wrapper_path="/usr/local/bin/${wrapper}"
            if [ -x "$wrapper_path" ]; then
                SECURITY_WRAPPERS_RULE="${SECURITY_WRAPPERS_RULE}open-ace ALL=(root) NOPASSWD: ${wrapper_path} *
openace ALL=(root) NOPASSWD: ${wrapper_path} *
"
            fi
        done

        # Record sudoers generation to audit log (Issue #1855)
        AUDIT_LOG="/app/logs/sudoers-audit.log"
        mkdir -p /app/logs 2>/dev/null || true
        SUDOERS_CHECKSUM=$(echo "sudoers-$(date +%s)" | sha256sum | cut -d' ' -f1)
        echo "[$(date '+%Y-%m-%d %H:%M:%S')] [openace-sudoers] Generated sudoers file, checksum=${SUDOERS_CHECKSUM}" >> "$AUDIT_LOG" 2>/dev/null || true

        # 【安全加固 Issue #2334】Resolve ALL conditionals BEFORE heredoc
        # WebUI launcher wrapper is REQUIRED - fail closed if missing
        WEBUI_LAUNCH_WRAPPER="/usr/local/bin/openace-webui-launch"
        if [ ! -x "$WEBUI_LAUNCH_WRAPPER" ]; then
            echo "ERROR: Required wrapper not executable: $WEBUI_LAUNCH_WRAPPER" >&2
            echo "       WebUI launcher wrapper must be installed. Cannot continue." >&2
            exit 1
        fi
        # Build WebUI rule (no fallback allowed per Issue #2334)
        WEBUI_RULE="open-ace ALL=(ALL) NOPASSWD: ${WEBUI_LAUNCH_WRAPPER} * \"${WEBUI_PATH}\" *
openace ALL=(ALL) NOPASSWD: ${WEBUI_LAUNCH_WRAPPER} * \"${WEBUI_PATH}\" *"

        cat > /etc/sudoers.d/open-ace-webui << SUDOERS_EOF
# Open ACE WebUI - Multi-user workspace sudo configuration
# Auto-generated by docker-entrypoint.sh
# Support both open-ace (container user) and openace (workspace user synced from database)

# ============================================================================
# 【安全加固 Issue #1514 + #1855】精确参数白名单配置
# ============================================================================
# git/gh cross-user command grammar is enforced by openace-git/openace-gh.
# sudoers authorizes only those wrapper binaries; direct git/gh wildcards are not a security boundary.
# Issue #1855: 移除 cat/chown/useradd 通配，改用安全 wrapper

# git/gh cross-user operations are validated by root-owned wrappers (#2650).
Cmnd_Alias GIT_SAFE = /usr/local/bin/openace-git *
Cmnd_Alias GH_SAFE = /usr/local/bin/openace-gh *

# 【安全加固 Issue #2334】OPENACE_UTILS 收紧
# 移除 git/gh 通配（改用 GIT_SAFE/GH_SAFE），消除 runas 漂移
# 移除 root-runas mkdir（改用 openace-mkdir wrapper）
# 保留低风险只读命令：test, ls, stat, id, find
# find 是只读操作，DAC 已保护敏感目录
# 【Issue #2334】runas 使用 (ALL) 以支持 github_ops 跨用户工具调用
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/stat *, /usr/bin/id *, /usr/bin/find *

# 【Issue #2674】跨用户 mkdir：github_ops.create_verification_worktree_dir 发
# 'sudo -u <account> mkdir -p -- <path>' / 'mkdir -m 700 -- <path>'（裸 mkdir，
# 非 openace-mkdir wrapper——wrapper 仅 (root) runas 且产生 root 属主目录，
# 而 verifier worktree 必须由执行 git 的身份持有）。sudo 按调用方 PATH 解析
# 裸 mkdir，/usr/bin 与 /bin 两种解析路径都必须匹配。
Cmnd_Alias MKDIR_SAFE = /usr/bin/mkdir *, /bin/mkdir *

# 【安全加固 Issue #2181】删除 AI CLI 通配规则
# 原 OPENACE_CLI 已删除，所有 AI CLI 启动必须通过 openace-run-as --isolated
# 该 wrapper 已实现目标用户验证、禁止 root 运行、环境隔离

# ============================================================================
# 用户权限配置
# ============================================================================
# WebUI 启动规则：通过 openace-webui-launch wrapper 以任意用户运行
# Issue #2334: wrapper 必须存在，否则安装失败（fail-closed，无 fallback）
# Issue #2305: wrapper 限定首参为 WEBUI_PATH，防止权限提升
${WEBUI_RULE}
# Git/GH 精确白名单：参数已限定，无法注入危险操作
# 【Issue #2334】使用 (ALL) runas 以允许 github_ops 跨用户 git/gh 操作
# 这与 #2280 要求一致：github_ops 必须 sudo -u <system_account> 访问私有 repo
open-ace ALL=(ALL) NOPASSWD: GIT_SAFE
openace ALL=(ALL) NOPASSWD: GIT_SAFE
open-ace ALL=(ALL) NOPASSWD: GH_SAFE
openace ALL=(ALL) NOPASSWD: GH_SAFE
# 【Issue #2674】跨用户 mkdir：github_ops verifier worktree 目录创建
open-ace ALL=(ALL) NOPASSWD: MKDIR_SAFE
openace ALL=(ALL) NOPASSWD: MKDIR_SAFE
# 【Issue #2334】OPENACE_UTILS 收紧：git/gh 已迁移到 GIT_SAFE/GH_SAFE
# 保留低风险只读工具：test, ls, stat, id, find（跨用户 mkdir 由 MKDIR_SAFE 承接）
# 注意：runas 保持 (ALL) 以支持 github_ops 等跨用户工具调用
open-ace ALL=(ALL) NOPASSWD: OPENACE_UTILS
openace ALL=(ALL) NOPASSWD: OPENACE_UTILS
${WRAPPER_RULE}
${SECURITY_WRAPPERS_RULE}
# ============================================================================
# 【安全加固 Issue #1514】sudoers审计日志配置（可选）
# ============================================================================
# 仅记录命令和参数，不记录stdin/stdout（避免敏感信息泄露）
# Defaults logfile=/var/log/sudo-openace.log
# Defaults log_year, log_host

# ============================================================================
# Preserve environment variables for sudo env_keep passing.
# ============================================================================
# 【安全加固 Issue #2181】清理敏感变量
# Agent 进程通过 openace-run-as --isolated 使用 env -i，不继承 env_keep
# env_keep 主要用于 WebUI 启动（sudo -u），需要清理敏感凭据
# 移除：OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENCLAW_TOKEN, GH_TOKEN
# 保留：非敏感变量（proxy_token, GIT_*签名变量）
# 【Issue #2650】PATH 移除：secure_path 已覆盖命令查找，env_keep PATH 是死配置兼隐患
Defaults env_keep += "OPENACE_PROXY_TOKEN OPENACE_PROXY_URL OPENACE_MODEL OPENACE_LOG_DIR"
Defaults env_keep += "GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL"
Defaults env_keep += "SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS"
Defaults secure_path = /usr/local/bin:/sbin:/bin:/usr/sbin:/usr/bin
SUDOERS_EOF
        chmod 440 /etc/sudoers.d/open-ace-webui

        # Validate sudoers syntax
        if visudo -c -f /etc/sudoers.d/open-ace-webui &>/dev/null; then
            if [ -f /etc/sudoers.d/openace-run-as ] && grep -qF "$WRAPPER_PATH" /etc/sudoers.d/openace-run-as; then
                mv /etc/sudoers.d/openace-run-as "/etc/sudoers.d/openace-run-as.disabled.$(date +%s)"
                echo "Disabled legacy broad autonomous-agent sudoers rule"
            fi
            echo "Sudoers configured for qwen-code-webui at: $WEBUI_PATH"
            echo "  git path: $GIT_PATH"
            echo "  gh path: $GH_PATH"
        else
            echo "WARNING: Sudoers syntax validation failed. Removing invalid file."
            rm -f /etc/sudoers.d/open-ace-webui
        fi
    else
        echo "WARNING: qwen-code-webui not found at $WEBUI_PATH. Workspace instances may not start."
    fi
fi

# ============================================================================
# 3. Start Application
# ============================================================================
echo "=========================================="
echo "  Open ACE - Starting Gunicorn"
echo "=========================================="

# Issue #3378 (D5): only the WEB service reconciles orphaned webui sandbox
# pods at boot (positive-trigger env). The scheduler container shares this
# entrypoint but must NOT set it — the web process owns the webui-pod
# lifecycle (process generation reconcile), and two hosts would race.
if [ "${SCHEDULER_MODE:-web}" != "scheduler" ]; then
    export OPENACE_WEBUI_ORPHAN_RECONCILE=1
fi

# Use gunicorn_entry.py wrapper that monkey-patches gevent BEFORE gunicorn
# (or any transitive dep) imports urllib3. This prevents urllib3.util.ssl_
# RecursionError under the gevent event loop during LLM proxy outbound requests.
exec python3 /app/gunicorn_entry.py \
    --config python:app.gunicorn_worker \
    --bind 0.0.0.0:19888 \
    --worker-class app.gunicorn_worker.TerminalGeventWorker \
    --workers 1 \
    --access-logfile - \
    --error-logfile - \
    --capture-output \
    --timeout 120 \
    "app:create_app()"
