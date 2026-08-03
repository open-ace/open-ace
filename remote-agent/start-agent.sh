#!/usr/bin/env bash
#
# Open ACE Remote Agent - 一键启动脚本 (Linux/macOS)
#
# 用法:
#   bash start-agent.sh                # 启动 agent（若已在运行则跳过）
#   bash start-agent.sh --auto-start   # 配置开机自启（systemd 服务或 crontab @reboot）
#   bash start-agent.sh --stop         # 停止 agent
#   bash start-agent.sh --status       # 查看运行状态
#
# 说明:
#   - agent 的 config.json 已保存 server_url / machine_id / agent_token，
#     重启后直接复用，无需重新生成注册令牌。
#   - 本脚本只启动/维护 agent 进程，不会覆盖已有配置。

set -u

# ============================================================================
# 定位安装目录
# ============================================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
INSTALL_DIR=""
if [ -f "$SCRIPT_DIR/agent.py" ]; then
    INSTALL_DIR="$SCRIPT_DIR"
elif [ -f "$HOME/.open-ace-agent/agent.py" ]; then
    INSTALL_DIR="$HOME/.open-ace-agent"
fi

if [ -z "$INSTALL_DIR" ]; then
    echo "[ERROR] 未找到 agent.py。请先执行安装注册命令，或用 --dir 指定安装目录。"
    exit 1
fi

CONFIG_FILE="$INSTALL_DIR/config.json"
if [ ! -f "$CONFIG_FILE" ]; then
    echo "[ERROR] 未找到配置文件 $CONFIG_FILE"
    echo "       请先执行安装注册命令（仅需一次）。"
    exit 1
fi

# 读取配置（仅用于展示）
SERVER_URL=$(python3 -c "import json;print(json.load(open('$CONFIG_FILE')).get('server_url',''))" 2>/dev/null || echo "")
MACHINE_ID=$(python3 -c "import json;print(json.load(open('$CONFIG_FILE')).get('machine_id',''))" 2>/dev/null || echo "")

# 寻找 python
find_python() {
    for py in python3 python; do
        if command -v "$py" >/dev/null 2>&1; then
            echo "$py"
            return 0
        fi
    done
    return 1
}
PYTHON_BIN="$(find_python)" || { echo "[ERROR] 未找到 python3/python，请先安装 Python 3.8+"; exit 1; }

# ============================================================================
# 检测 agent 进程
# ============================================================================
is_agent_running() {
    pgrep -f "python.*${INSTALL_DIR}/agent.py" >/dev/null 2>&1
    return $?
}

show_status() {
    if is_agent_running; then
        echo "[OK]   Agent 正在运行"
        echo "       机器 ID: $MACHINE_ID"
        echo "       服务器: $SERVER_URL"
    else
        echo "[WARN] Agent 未运行"
        echo "       机器 ID: $MACHINE_ID"
        echo "       服务器: $SERVER_URL"
        echo "       启动: bash $INSTALL_DIR/start-agent.sh"
    fi
}

# ============================================================================
# 参数处理
# ============================================================================
case "${1:-}" in
    --status)
        show_status
        exit 0
        ;;
    --stop)
        if is_agent_running; then
            echo "[INFO] 停止 agent..."
            pkill -f "python.*${INSTALL_DIR}/agent.py" 2>/dev/null || true
            sleep 1
        fi
        echo "[OK]   Agent 已停止"
        exit 0
        ;;
    --auto-start)
        echo "[INFO] 配置开机自启..."
        if command -v systemctl >/dev/null 2>&1; then
            SERVICE="/etc/systemd/system/open-ace-agent.service"
            if [ ! -f "$SERVICE" ]; then
                sudo tee "$SERVICE" >/dev/null <<EOF
[Unit]
Description=Open ACE Remote Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=${INSTALL_DIR}
ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/agent.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF
                sudo systemctl daemon-reload
            fi
            sudo systemctl enable open-ace-agent >/dev/null 2>&1
            sudo systemctl start open-ace-agent >/dev/null 2>&1
            echo "[OK]   已配置 systemd 服务 open-ace-agent (开机自启 + 崩溃自动重启)"
        else
            # 无 systemd（如 WSL2）: 使用 crontab @reboot
            CRON_LINE="@reboot $PYTHON_BIN $INSTALL_DIR/agent.py >> $INSTALL_DIR/agent.log 2>&1"
            ( crontab -l 2>/dev/null | grep -v "open-ace-agent/agent.py" ; echo "$CRON_LINE" ) | crontab -
            echo "[OK]   已配置 crontab @reboot 开机自启"
            echo "       （WSL2 需 Windows 侧设置自启 WSL 会话，或在 Windows 中执行 start-agent.ps1）"
        fi
        exit 0
        ;;
esac

# ============================================================================
# 启动 agent
# ============================================================================
if is_agent_running; then
    echo "[OK]   Agent 已在运行，无需重复启动"
    exit 0
fi

echo "[INFO] 启动 Open ACE Remote Agent..."
nohup "$PYTHON_BIN" "$INSTALL_DIR/agent.py" >> "$INSTALL_DIR/agent.log" 2>&1 &
AGENT_PID=$!
sleep 2
if kill -0 "$AGENT_PID" >/dev/null 2>&1; then
    echo "[OK]   Agent 已启动 (PID: $AGENT_PID)"
    echo "       机器 ID: $MACHINE_ID"
    echo "       服务器: $SERVER_URL"
    echo "       日志: $INSTALL_DIR/agent.log"
    echo ""
    echo "Agent 启动后会自动连接服务器并在数秒内恢复在线状态。"
    echo "即使服务器重启，Agent 也会在 1-60 秒内自动重连，无需人工干预。"
    echo ""
    echo "如需开机自启: bash $INSTALL_DIR/start-agent.sh --auto-start"
else
    echo "[ERROR] Agent 启动失败，请检查 $INSTALL_DIR/agent.log"
    exit 1
fi
