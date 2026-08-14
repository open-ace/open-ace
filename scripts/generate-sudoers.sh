#!/bin/bash
# generate-sudoers.sh - Unified sudoers generator for Issue #2334
#
# Single source of truth for sudoers configuration used by both Docker and Package.
# All conditionals are resolved BEFORE writing to the sudoers file.
#
# Usage: generate-sudoers.sh --output <file> [--user <user>] [--dry-run]
#
# Exit codes:
#   0 - Success
#   1 - Invalid arguments
#   2 - Required wrapper missing
#   3 - visudo validation failed
#   4 - Write failed

set -euo pipefail

# Default values
OUTPUT_FILE=""
RUN_USER="open-ace"
DRY_RUN=false
INSTALL_DIR=""

# Security mode configuration (Issue #2650 - Gradual Migration Strategy)
# STRICT_MODE=true  - Only allow wrappers (recommended for production)
# STRICT_MODE=false - Allow fallback to basic git/gh commands (for migration/testing)
STRICT_MODE="${OPENACE_STRICT_WRAPPER:-true}"

# Audit log configuration
AUDIT_LOG="${AUDIT_LOG:-/var/log/openace/sudoers-audit.log}"

usage() {
    echo "Usage: $0 --output <file> [--user <user>] [--install-dir <dir>] [--dry-run]"
    echo ""
    echo "Options:"
    echo "  --output <file>      Output sudoers file path (required)"
    echo "  --user <user>        Service user (default: open-ace)"
    echo "  --install-dir <dir>  Installation directory for path resolution"
    echo "  --dry-run            Show generated content without writing"
    exit 1
}

# Parse arguments
while [[ $# -gt 0 ]]; do
    case "$1" in
        --output)
            OUTPUT_FILE="$2"
            shift 2
            ;;
        --user)
            RUN_USER="$2"
            shift 2
            ;;
        --install-dir)
            INSTALL_DIR="$2"
            shift 2
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            echo "Unknown option: $1" >&2
            usage
            ;;
    esac
done

if [[ -z "$OUTPUT_FILE" ]]; then
    echo "ERROR: --output is required" >&2
    usage
fi

# ============================================================================
# Resolve all paths and validate wrappers BEFORE generating content
# ============================================================================

# Resolve git and gh paths
GIT_PATH=$(which git 2>/dev/null || echo "/usr/bin/git")
GH_PATH=$(which gh 2>/dev/null || echo "/usr/bin/gh")

# Resolve WebUI path
WEBUI_PATH=$(which qwen-code-webui 2>/dev/null || echo "/usr/bin/qwen-code-webui")

# Validate required wrappers exist and are executable (skip in dry-run mode)
# In dry-run mode, we're testing the output format, not actual installation
WEBUI_LAUNCH_WRAPPER="/usr/local/bin/openace-webui-launch"
RUN_AS_WRAPPER="/usr/local/bin/openace-run-as"
GIT_WRAPPER="/usr/local/bin/openace-git"
GH_WRAPPER="/usr/local/bin/openace-gh"

if [[ "$DRY_RUN" != true ]]; then
    if [[ ! -x "$WEBUI_LAUNCH_WRAPPER" ]]; then
        echo "ERROR: Required wrapper not executable: $WEBUI_LAUNCH_WRAPPER" >&2
        echo "       WebUI launcher wrapper must be installed before generating sudoers" >&2
        exit 2
    fi

    if [[ ! -x "$RUN_AS_WRAPPER" ]]; then
        echo "ERROR: Required wrapper not executable: $RUN_AS_WRAPPER" >&2
        exit 2
    fi

    # Issue #2650: git/gh wrappers are required for secure operations
    if [[ ! -x "$GIT_WRAPPER" ]]; then
        echo "ERROR: Required wrapper not executable: $GIT_WRAPPER" >&2
        echo "       openace-git wrapper must be installed before generating sudoers" >&2
        exit 2
    fi

    if [[ ! -x "$GH_WRAPPER" ]]; then
        echo "ERROR: Required wrapper not executable: $GH_WRAPPER" >&2
        echo "       openace-gh wrapper must be installed before generating sudoers" >&2
        exit 2
    fi
fi

# ============================================================================
# Build sudoers content with all conditionals pre-resolved
# ============================================================================

# Build WebUI launcher rules (fail-closed, no fallback)
WEBUI_RULES="${RUN_USER} ALL=(ALL) NOPASSWD: ${WEBUI_LAUNCH_WRAPPER} * \"${WEBUI_PATH}\" *"

# Build run-as wrapper rules
WRAPPER_RULE="${RUN_USER} ALL=(root) NOPASSWD: ${RUN_AS_WRAPPER} --isolated *"

# Build security wrapper rules
# In dry-run mode, show all expected wrappers for testing
# In production mode, only include wrappers that actually exist
# Issue #2650: Added openace-git and openace-gh wrappers
SECURITY_WRAPPERS_RULES=""
for wrapper in openace-git openace-gh openace-chown openace-useradd openace-cat openace-mkdir openace-write-as openace-rm; do
    wrapper_path="/usr/local/bin/${wrapper}"
    if [[ "$DRY_RUN" = true ]] || [[ -x "$wrapper_path" ]]; then
        SECURITY_WRAPPERS_RULES="${SECURITY_WRAPPERS_RULES}
${RUN_USER} ALL=(root) NOPASSWD: ${wrapper_path} *"
    fi
done

# Build GH admin rule (opt-in only)
GH_ADMIN_RULE=""
if [[ "${OPENACE_ALLOW_ADMIN_MERGE:-}" = "1" ]]; then
    GH_ADMIN_RULE="${GH_PATH} pr merge * --admin, \\
    "
fi

# Build fallback rules for non-strict mode (Issue #2650 - Gradual Migration)
# These provide a rollback path if wrappers have issues
FALLBACK_RULES=""
if [[ "$STRICT_MODE" = "false" ]]; then
    # Limited fallback commands - safe read-only operations only
    # WARNING: These bypass wrapper security checks, use only for migration
    FALLBACK_RULES="
# ============================================================================
# 【迁移备选路径】仅用于过渡期 (OPENACE_STRICT_WRAPPER=false)
# ============================================================================
# 保留基本的只读 git 命令作为备选路径
# WARNING: 这些命令绕过包装器安全检查，仅用于迁移过渡期
# 生产环境应设置 OPENACE_STRICT_WRAPPER=true
Cmnd_Alias GIT_READONLY = ${GIT_PATH} --version, \\
                         ${GIT_PATH} --help, \\
                         ${GIT_PATH} version, \\
                         ${GIT_PATH} status *, \\
                         ${GIT_PATH} log *, \\
                         ${GIT_PATH} diff *, \\
                         ${GIT_PATH} branch --list *, \\
                         ${GIT_PATH} remote -v

# 保留基本的只读 gh 命令作为备选路径
Cmnd_Alias GH_READONLY = ${GH_PATH} --version, \\
                        ${GH_PATH} --help, \\
                        ${GH_PATH} version, \\
                        ${GH_PATH} auth status

"
fi

# Generate the sudoers content
# Issue #2650: GIT_SAFE and GH_SAFE now point to security wrappers only
# Issue #2650: Added gradual migration strategy with fallback paths
SUDOERS_CONTENT="# Open ACE WebUI - Multi-user workspace sudo configuration
# Auto-generated by generate-sudoers.sh on $(date '+%Y-%m-%d %H:%M:%S')
# Issue #2334: Unified generator for Docker and Package paths
# Issue #2650: git/gh hardened wrappers for security boundary enforcement
#
# Support both open-ace (container user) and openace (workspace user synced from database)
#
# ============================================================================
# 【迁移策略 Issue #2650】渐进式安全加固
# ============================================================================
# 当前模式: STRICT_MODE=${STRICT_MODE}
#
# 生产环境（推荐）: OPENACE_STRICT_WRAPPER=true
#   - 所有 git/gh 操作必须通过包装器
#   - 完全阻断直接 git/gh 调用
#   - 最大化安全边界
#
# 迁移/测试环境: OPENACE_STRICT_WRAPPER=false
#   - 允许基本的只读 git/gh 命令作为备选
#   - 仅用于迁移过渡期，不推荐用于生产
#   - 设置严格模式后应立即回退
#
# 回滚路径:
#   1. 检查包装器日志: /var/log/openace/wrapper-audit.log
#   2. 验证配置文件: /etc/openace/wrapper.yaml
#   3. 临时设置为宽松模式: export OPENACE_STRICT_WRAPPER=false
#   4. 重新生成 sudoers: scripts/generate-sudoers.sh --output /etc/sudoers.d/openace
# ============================================================================

# ============================================================================
# 【安全加固 Issue #2650】Git/GH 安全包装器配置
# ============================================================================
# 通过 openace-git/openace-gh 包装器实现命令白名单：
# - 只允许 github_ops 实际构造的命令形状
# - 阻止 -c alias.* 等 RCE 攻击向量
# - 阻止 clean -fd、reset --hard 等破坏性操作
# - 阻止 gh repo delete、gh api -X DELETE 等危险操作
#
# 配置文件: /etc/openace/wrapper.yaml, git-verbs.yaml, gh-commands.yaml
# See Issue #2650 for design details.

# Git 安全包装器（Issue #2650）
# 所有 git 操作必须通过包装器，直接 git 调用被阻断
Cmnd_Alias GIT_SAFE = /usr/local/bin/openace-git *

# GH 安全包装器（Issue #2650）
# 所有 gh 操作必须通过包装器，直接 gh 调用被阻断
Cmnd_Alias GH_SAFE = /usr/local/bin/openace-gh *
${FALLBACK_RULES}
# 【安全加固 Issue #2334】OPENACE_UTILS 收紧
# 移除 git/gh 通配（改用 GIT_SAFE/GH_SAFE）
# 移除 mkdir（改用 openace-mkdir wrapper）
# 保留低风险只读命令：test, ls, stat, id, find
# find 是只读操作，DAC 已保护敏感目录
Cmnd_Alias OPENACE_UTILS = /usr/bin/test *, /usr/bin/ls *, /usr/bin/stat *, /usr/bin/id *, /usr/bin/find *

# ============================================================================
# 用户权限配置
# ============================================================================

# Git/GH rules with (ALL) runas for cross-user operations (Issue #2280)
# These allow github_ops to run git/gh as system_account
${RUN_USER} ALL=(ALL) NOPASSWD: GIT_SAFE
${RUN_USER} ALL=(ALL) NOPASSWD: GH_SAFE

# WebUI launcher - wrapper required (no fallback per Issue #2334)
${WEBUI_RULES}

# Agent CLI - Isolated sandbox only
${WRAPPER_RULE}

# Security wrappers (root runas only - validated internally)
${SECURITY_WRAPPERS_RULES}

# Low-risk utilities (root runas only)
${RUN_USER} ALL=(root) NOPASSWD: OPENACE_UTILS

# ============================================================================
# Preserve environment variables for sudo env_keep passing.
# ============================================================================
# 【安全加固 Issue #2181 + #2334】清理敏感变量
# Agent 进程通过 openace-run-as --isolated 使用 env -i，不继承 env_keep
# env_keep 主要用于 WebUI 启动（sudo -u），需要清理敏感凭据
# 移除：OPENAI_API_KEY, ANTHROPIC_API_KEY, GEMINI_API_KEY, OPENCLAW_TOKEN, GH_TOKEN
# 保留：非敏感变量（proxy_token, GIT_*签名变量, PATH）
Defaults env_keep += \"OPENACE_PROXY_TOKEN OPENACE_PROXY_URL OPENACE_MODEL OPENACE_LOG_DIR PATH\"
Defaults env_keep += \"GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL\"
Defaults env_keep += \"SESSION_TIMEOUT_MS KEEPALIVE_INTERVAL_MS\"
"

# ============================================================================
# Add fallback rules for non-strict mode (Issue #2650)
# ============================================================================
if [[ "$STRICT_MODE" = "false" ]]; then
    SUDOERS_CONTENT="${SUDOERS_CONTENT}

# 【迁移备选权限】仅用于过渡期
${RUN_USER} ALL=(ALL) NOPASSWD: GIT_READONLY
${RUN_USER} ALL=(ALL) NOPASSWD: GH_READONLY
"
fi

# ============================================================================
# Dry run mode: output content and exit
# ============================================================================
if [[ "$DRY_RUN" = true ]]; then
    echo "$SUDOERS_CONTENT"
    exit 0
fi

# ============================================================================
# Atomic install protocol
# ============================================================================

# Write to temp file first
TEMP_FILE=$(mktemp)
trap 'rm -f "$TEMP_FILE"' EXIT

echo "$SUDOERS_CONTENT" > "$TEMP_FILE"
chmod 440 "$TEMP_FILE"

# Validate with visudo
if ! visudo -c -f "$TEMP_FILE" >/dev/null 2>&1; then
    echo "ERROR: sudoers syntax validation failed" >&2
    visudo -c -f "$TEMP_FILE" 2>&1 || true
    exit 3
fi

# Backup existing file if present
BACKUP_FILE=""
if [[ -f "$OUTPUT_FILE" ]]; then
    BACKUP_FILE="${OUTPUT_FILE}.bak.$(date +%s)"
    if ! cp -p "$OUTPUT_FILE" "$BACKUP_FILE" 2>/dev/null; then
        echo "WARNING: Could not backup existing sudoers file" >&2
        BACKUP_FILE=""
    fi
fi

# Atomic rename
if ! mv "$TEMP_FILE" "$OUTPUT_FILE"; then
    echo "ERROR: Failed to write sudoers file" >&2
    # Restore backup if we have one
    if [[ -n "$BACKUP_FILE" ]] && [[ -f "$BACKUP_FILE" ]]; then
        mv "$BACKUP_FILE" "$OUTPUT_FILE"
        echo "Restored previous sudoers from backup" >&2
    fi
    exit 4
fi

# Clear trap since we succeeded
trap - EXIT

# Write audit log entry
log_audit() {
    local msg="$1"
    mkdir -p "$(dirname "$AUDIT_LOG")" 2>/dev/null || true
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $msg" >> "$AUDIT_LOG" 2>/dev/null || true
}

log_audit "[generate-sudoers] Generated $OUTPUT_FILE for user=$RUN_USER"

echo "Generated sudoers file: $OUTPUT_FILE"
if [[ -n "$BACKUP_FILE" ]]; then
    echo "Backup: $BACKUP_FILE"
fi

exit 0
