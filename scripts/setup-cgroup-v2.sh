#!/bin/bash
# setup-cgroup-v2.sh — enable cgroup v2 resource limits for autonomous agents.
#
# Issue #2020: the autonomous agent launcher (openace-run-as.sh) enforces
# per-task memory/pids/cpu limits via cgroup v2. This requires:
#   1. the host kernel to expose a writable cgroup v2 hierarchy;
#   2. the memory/pids/cpu controllers delegated to children of the root cgroup;
#   3. a parent cgroup (default /sys/fs/cgroup/openace-agent) for task subgroups;
#   4. agent-launcher.conf to carry non-zero resource values and cgroup_enabled=on.
#
# On a bare-metal or VM Rocky/RHEL/CentOS 9 host these are NOT set up by the
# standard installer or the Dockerfile — the container path uses cgroup_enabled=auto
# and falls back to prlimit. This script automates the bare-metal setup and
# persists it across reboots via a oneshot systemd unit.
#
# Usage:
#   sudo bash scripts/setup-cgroup-v2.sh                      # apply defaults
#   sudo bash scripts/setup-cgroup-v2.sh --memory 4G --pids 1024 --cpu 4     # custom
#   sudo bash scripts/setup-cgroup-v2.sh --cgroup-enabled auto               # don't force
#   sudo bash scripts/setup-cgroup-v2.sh --dry-run                          # preview only
#
# Options:
#   --memory BYTES     memory.max for the parent cgroup (default: 2147483648 = 2G)
#                      accepts human-friendly suffixes: 512M, 2G, 4G
#   --pids N           pids.max (default: 512)
#   --cpu N            cpu.max as "quota period" = N * 100000 / 100000 (default: 2 → 200000 100000)
#   --cgroup-enabled   agent_task_cgroup_enabled value: on|auto|off (default: on)
#   --concurrency N    agent_max_concurrent_workflows (default: 3)
#   --wall-clock N     agent_task_wall_clock_limit in seconds (default: 3600)
#   --conf PATH        agent-launcher.conf path (default: /etc/openace/agent-launcher.conf)
#   --cgroup-root PATH parent cgroup path (default: /sys/fs/cgroup/openace-agent)
#   --dry-run          print actions without executing
#   --help             show this help
#
# Exit codes: 0 = success; 1 = not root or bad input; 2 = cgroup v2 unavailable; 3 = write failure

set -euo pipefail

# ── defaults ────────────────────────────────────────────────────────────────
MEMORY_BYTES=2147483648   # 2 GiB
PIDS_MAX=512
CPU_CORES=2               # expanded to "${CPU_CORES}00000 100000"
CGROUP_ENABLED="on"
CONCURRENCY=3
WALL_CLOCK=3600
CONF_PATH="/etc/openace/agent-launcher.conf"
CGROUP_ROOT="/sys/fs/cgroup/openace-agent"
SYSTEMD_UNIT="openace-cgroup-setup.service"
DRY_RUN=false

# ── helpers ─────────────────────────────────────────────────────────────────

log()  { printf '[setup-cgroup-v2] %s\n' "$*" >&2; }
warn() { printf '[setup-cgroup-v2] WARNING: %s\n' "$*" >&2; }
err()  { printf '[setup-cgroup-v2] ERROR: %s\n' "$*" >&2; }

# write_file PATH VALUE — write a value to a cgroup file (or print in dry-run).
# Uses direct redirection instead of bash -c to avoid command injection when
# values or paths contain shell metacharacters.
write_file() {
    local path="$1" val="$2"
    if $DRY_RUN; then
        printf '[dry-run] echo %s > %s\n' "$val" "$path"
    else
        printf '%s\n' "$val" > "$path"
    fi
}

human_to_bytes() {
    local val="$1"
    local lower_val="${val,,}"
    # Match integer + optional k/m/g suffix (case-insensitive).
    if [[ "$lower_val" =~ ^([0-9]+)([kmg]?)$ ]]; then
        local num="${BASH_REMATCH[1]}"
        local suffix="${BASH_REMATCH[2]}"
        case "$suffix" in
            g) echo $((num * 1024 * 1024 * 1024)) ;;
            m) echo $((num * 1024 * 1024)) ;;
            k) echo $((num * 1024)) ;;
            *) echo "$num" ;;
        esac
        return 0
    fi
    err "invalid --memory value: $val (expected e.g. 512M, 2G, 4G)"
    exit 1
}

show_help() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
    exit 0
}

# ── parse args ──────────────────────────────────────────────────────────────

while [[ $# -gt 0 ]]; do
    case "$1" in
        --memory)        MEMORY_BYTES=$(human_to_bytes "$2"); shift 2 ;;
        --pids)          PIDS_MAX="$2"; shift 2 ;;
        --cpu)           CPU_CORES="$2"; shift 2 ;;
        --cgroup-enabled) CGROUP_ENABLED="$2"; shift 2 ;;
        --concurrency)   CONCURRENCY="$2"; shift 2 ;;
        --wall-clock)    WALL_CLOCK="$2"; shift 2 ;;
        --conf)          CONF_PATH="$2"; shift 2 ;;
        --cgroup-root)   CGROUP_ROOT="$2"; shift 2 ;;
        --dry-run)       DRY_RUN=true; shift ;;
        --help|-h)       show_help ;;
        *)               err "unknown option: $1"; exit 1 ;;
    esac
done

# ── input validation ────────────────────────────────────────────────────────

re_posint='^[1-9][0-9]*$'

[[ "$MEMORY_BYTES" =~ $re_posint ]] || { err "--memory must resolve to a positive integer (got: $MEMORY_BYTES)"; exit 1; }
[[ "$PIDS_MAX" =~ $re_posint ]] || { err "--pids must be a positive integer (got: $PIDS_MAX)"; exit 1; }
[[ "$CPU_CORES" =~ $re_posint ]] || { err "--cpu must be a positive integer (got: $CPU_CORES)"; exit 1; }
[[ "$CONCURRENCY" =~ $re_posint ]] || { err "--concurrency must be a positive integer (got: $CONCURRENCY)"; exit 1; }
[[ "$WALL_CLOCK" =~ $re_posint ]] || { err "--wall-clock must be a positive integer (got: $WALL_CLOCK)"; exit 1; }
[[ "$CGROUP_ENABLED" =~ ^(on|auto|off)$ ]] || { err "--cgroup-enabled must be on|auto|off (got: $CGROUP_ENABLED)"; exit 1; }
[[ "$CONF_PATH" = /* ]] || { err "--conf must be an absolute path (got: $CONF_PATH)"; exit 1; }
[[ "$CGROUP_ROOT" = /* ]] || { err "--cgroup-root must be an absolute path (got: $CGROUP_ROOT)"; exit 1; }
# Defense in depth: paths are embedded into the systemd unit's bash -c string,
# so reject shell metacharacters even though the caller is already root.
re_safepath='^[a-zA-Z0-9._/-]+$'
[[ "$CONF_PATH" =~ $re_safepath ]] || { err "--conf contains invalid characters: $CONF_PATH"; exit 1; }
[[ "$CGROUP_ROOT" =~ $re_safepath ]] || { err "--cgroup-root contains invalid characters: $CGROUP_ROOT"; exit 1; }

# Expand CPU cores to cgroup v2 cpu.max format: "quota period"
# N cores → "N00000 100000" (quota = N * 100000 microseconds per 100000us period)
CPU_MAX="${CPU_CORES}00000 100000"

# ── pre-flight checks ───────────────────────────────────────────────────────

if [[ "$(id -u)" -ne 0 ]]; then
    err "must run as root (try: sudo bash $0)"
    exit 1
fi

if [[ ! -f /sys/fs/cgroup/cgroup.controllers ]]; then
    err "cgroup v2 is not mounted on this host (/sys/fs/cgroup/cgroup.controllers missing)"
    err "this script is for bare-metal / VM hosts with cgroup v2 enabled in the kernel"
    exit 2
fi

# Verify required controllers are available
available_controllers=$(cat /sys/fs/cgroup/cgroup.controllers 2>/dev/null || echo "")
for ctrl in memory pids cpu; do
    if ! grep -qw "$ctrl" <<< "$available_controllers"; then
        err "cgroup controller '$ctrl' is not available (controllers: $available_controllers)"
        err "ensure the kernel is booted with cgroup_no_legacy (cgroup v2 unified mode)"
        exit 2
    fi
done

log "cgroup v2 detected, controllers: $available_controllers"
log "configuration:"
log "  memory.max:        $MEMORY_BYTES bytes ($((MEMORY_BYTES / 1024 / 1024)) MiB)"
log "  pids.max:          $PIDS_MAX"
log "  cpu.max:           $CPU_MAX ($CPU_CORES cores)"
log "  cgroup_enabled:    $CGROUP_ENABLED"
log "  concurrency:       $CONCURRENCY"
log "  wall_clock_limit:  $WALL_CLOCK seconds"
log "  conf path:         $CONF_PATH"
log "  cgroup root:       $CGROUP_ROOT"
if $DRY_RUN; then log "(dry-run mode — no changes will be made)"; fi

# ── step 1: enable controller delegation ────────────────────────────────────

log "step 1: delegating controllers to child cgroups"

# Read current subtree_control and add missing controllers
current=$(cat /sys/fs/cgroup/cgroup.subtree_control 2>/dev/null || echo "")
needed="+memory +pids +cpu"
if [[ "$current" != *"+memory"* || "$current" != *"+pids"* || "$current" != *"+cpu"* ]]; then
    write_file /sys/fs/cgroup/cgroup.subtree_control "$needed" \
        || { err "failed to enable controllers (is /sys/fs/cgroup writable?)"; exit 3; }
    log "  controllers delegated: $needed"
else
    log "  controllers already delegated: $current"
fi

# ── step 2: create parent cgroup ────────────────────────────────────────────

log "step 2: creating parent cgroup $CGROUP_ROOT"

if [[ ! -d "$CGROUP_ROOT" ]]; then
    if $DRY_RUN; then
        printf '[dry-run] mkdir -p %s\n' "$CGROUP_ROOT"
    else
        mkdir -p "$CGROUP_ROOT" \
            || { err "failed to create $CGROUP_ROOT"; exit 3; }
    fi
    log "  created $CGROUP_ROOT"
else
    log "  $CGROUP_ROOT already exists"
fi

# ── step 3: apply resource limits ───────────────────────────────────────────

log "step 3: applying resource limits"

write_file "$CGROUP_ROOT/memory.max" "$MEMORY_BYTES" \
    || { err "failed to set memory.max"; exit 3; }
write_file "$CGROUP_ROOT/pids.max" "$PIDS_MAX" \
    || { err "failed to set pids.max"; exit 3; }
write_file "$CGROUP_ROOT/cpu.max" "$CPU_MAX" \
    || { err "failed to set cpu.max"; exit 3; }

if ! $DRY_RUN; then
    log "  memory.max = $(cat "$CGROUP_ROOT/memory.max")"
    log "  pids.max   = $(cat "$CGROUP_ROOT/pids.max")"
    log "  cpu.max    = $(cat "$CGROUP_ROOT/cpu.max")"
fi

# ── step 4: update agent-launcher.conf ──────────────────────────────────────

log "step 4: updating $CONF_PATH"

# Ensure /etc/openace exists
if $DRY_RUN; then
    printf '[dry-run] mkdir -p %s\n' "$(dirname "$CONF_PATH")"
else
    mkdir -p "$(dirname "$CONF_PATH")"
    chmod 755 "$(dirname "$CONF_PATH")"
fi

# Preserve existing account/workspace lines (and any other non-resource keys)
# by removing only the resource keys we manage, then appending fresh values.
# This keeps user comments and third-party keys intact across re-runs.
existing_account=""
existing_roots=""
if [[ -f "$CONF_PATH" ]]; then
    existing_account=$(sed -n 's/^OPENACE_AUTONOMOUS_AGENT_ACCOUNT=//p' "$CONF_PATH" 2>/dev/null | tr -d '"' | head -1)
    existing_roots=$(sed -n 's/^ALLOWED_WORKSPACE_ROOTS=//p' "$CONF_PATH" 2>/dev/null | tr -d '"' | head -1)
fi
existing_account="${existing_account:-openace-agent}"
existing_roots="${existing_roots:-/home /workspace}"

# Build the new resource block
resource_block=$(cat <<EOF
# Issue #2020: per-task runtime isolation and resource limits.
# Managed by setup-cgroup-v2.sh — do not edit resource keys by hand.
agent_task_root=/run/openace-agent-tasks
agent_task_cgroup_root=$CGROUP_ROOT
agent_task_cgroup_enabled=$CGROUP_ENABLED
agent_task_memory_max_bytes=$MEMORY_BYTES
agent_task_pids_max=$PIDS_MAX
agent_task_cpu_max="$CPU_MAX"
agent_task_wall_clock_limit=$WALL_CLOCK
agent_max_concurrent_workflows=$CONCURRENCY
EOF
)

if $DRY_RUN; then
    printf '[dry-run] would update %s\n' "$CONF_PATH"
    printf '  (preserving account=%s, roots=%s)\n' "$existing_account" "$existing_roots"
    printf '  (writing resource block):\n%s\n' "$resource_block"
else
    # Back up the existing file for auditability
    if [[ -f "$CONF_PATH" ]]; then
        cp "$CONF_PATH" "${CONF_PATH}.bak.$(date +%s)"
    fi

    # Write the new config: preserve account/roots, drop stale resource keys, append fresh
    {
        echo '# Issue #2018: constraints for openace-run-as --isolated.'
        echo '# root:root 0640 — the openace service account cannot edit this file.'
        echo "OPENACE_AUTONOMOUS_AGENT_ACCOUNT=\"$existing_account\""
        echo "ALLOWED_WORKSPACE_ROOTS=\"$existing_roots\""
        echo ""
        # If the file had any other keys (not account/roots/resource), preserve them.
        if [[ -f "$CONF_PATH" ]]; then
            grep -vE '^(#|OPENACE_AUTONOMOUS_AGENT_ACCOUNT=|ALLOWED_WORKSPACE_ROOTS=|agent_task_|agent_max_concurrent_workflows=)' "$CONF_PATH" 2>/dev/null || true
        fi
        echo "$resource_block"
    } > "$CONF_PATH"

    chown root:root "$CONF_PATH"
    chmod 640 "$CONF_PATH"
    log "  $CONF_PATH updated (backup at ${CONF_PATH}.bak.*)"
fi

# ── step 5: create systemd unit for persistence ─────────────────────────────

log "step 5: creating systemd unit for reboot persistence"

UNIT_PATH="/etc/systemd/system/${SYSTEMD_UNIT}"

if $DRY_RUN; then
    printf '[dry-run] would write systemd unit to %s\n' "$UNIT_PATH"
else
    cat > "$UNIT_PATH" <<EOF
[Unit]
Description=Open ACE cgroup v2 setup for agent isolation (#2020)
DefaultDependencies=no
After=local-fs.target
Before=sysinit.target open-ace.service

[Service]
Type=oneshot
RemainAfterExit=yes
# Read resource values from the conf so a re-run of setup-cgroup-v2.sh with
# different values is picked up on next boot without editing this unit.
ExecStart=/bin/bash -c 'set -eu; test -f ${CONF_PATH} || exit 0; source ${CONF_PATH}; test -n "\${agent_task_memory_max_bytes:-}" -a "\${agent_task_memory_max_bytes:-}" != "0" || exit 0; echo "+memory +pids +cpu" > /sys/fs/cgroup/cgroup.subtree_control; mkdir -p \${agent_task_cgroup_root}; echo "\${agent_task_memory_max_bytes}" > \${agent_task_cgroup_root}/memory.max; echo "\${agent_task_pids_max}" > \${agent_task_cgroup_root}/pids.max; echo "\${agent_task_cpu_max}" > \${agent_task_cgroup_root}/cpu.max'

[Install]
WantedBy=sysinit.target
EOF

    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload
        systemctl enable "$SYSTEMD_UNIT" 2>/dev/null || true
        if ! systemctl start "$SYSTEMD_UNIT" 2>&1; then
            err "systemd unit failed to start; inspect: systemctl status $SYSTEMD_UNIT"
            exit 3
        fi
        log "  systemd unit installed and started"
    else
        warn "systemctl not found; systemd unit written to $UNIT_PATH but not activated"
    fi
fi

# ── summary ─────────────────────────────────────────────────────────────────

log ""
log "✓ cgroup v2 resource limits enabled"
log ""
if ! $DRY_RUN; then
    log "verification:"
    log "  cgroup.subtree_control: $(cat /sys/fs/cgroup/cgroup.subtree_control)"
    log "  $CGROUP_ROOT/memory.max: $(cat "$CGROUP_ROOT/memory.max" 2>/dev/null)"
    log "  $CGROUP_ROOT/pids.max:   $(cat "$CGROUP_ROOT/pids.max" 2>/dev/null)"
    log "  $CGROUP_ROOT/cpu.max:    $(cat "$CGROUP_ROOT/cpu.max" 2>/dev/null)"
    if command -v systemctl >/dev/null 2>&1; then
        log "  systemd unit (enabled):  $(systemctl is-enabled "$SYSTEMD_UNIT" 2>/dev/null || echo 'unknown')"
        log "  systemd unit (active):   $(systemctl is-active "$SYSTEMD_UNIT" 2>/dev/null || echo 'unknown')"
    fi
    log ""
    log "to change limits later, re-run:"
    log "  sudo bash scripts/setup-cgroup-v2.sh --memory 4G --pids 1024 --cpu 4"
    log ""
    log "to disable, set --cgroup-enabled off and remove the systemd unit:"
    log "  sudo systemctl disable --now $SYSTEMD_UNIT"
    log "  sudo rm $UNIT_PATH"
fi
