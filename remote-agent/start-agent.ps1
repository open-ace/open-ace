# Open ACE Remote Agent - Windows 一键启动/自启脚本
#
# 用法:
#   powershell -ExecutionPolicy Bypass -File start-agent.ps1             # 启动 agent（若已在运行则跳过）
#   powershell -ExecutionPolicy Bypass -File start-agent.ps1 -InstallAutoStart  # 注册计划任务，登录时自动启动
#   powershell -ExecutionPolicy Bypass -File start-agent.ps1 -Stop       # 停止 agent
#   powershell -ExecutionPolicy Bypass -File start-agent.ps1 -Status     # 查看 agent 运行状态
#
# 说明:
#   - agent 的配置 (config.json) 包含 server_url / machine_id / agent_token，
#     重启后直接复用，无需重新生成注册令牌。
#   - 本脚本只启动/维护 agent 进程，不会覆盖已有配置。

param(
    [switch]$InstallAutoStart,
    [switch]$Stop,
    [switch]$Status,
    [string]$InstallDir = ""
)

$ErrorActionPreference = "Continue"

function Write-Info  { Write-Host "[INFO] $args" -ForegroundColor Cyan }
function Write-Ok    { Write-Host "[OK]   $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN] $args" -ForegroundColor Yellow }
function Write-ErrorMsg { Write-Host "[ERROR] $args" -ForegroundColor Red }

# ============================================================================
# 1. 定位安装目录
# ============================================================================
if (-not $InstallDir) {
    # 优先使用脚本所在目录（脚本被 install.ps1 复制到安装目录）
    $InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
}
# 兼容默认安装目录
if (-not (Test-Path "$InstallDir\agent.py")) {
    $defaultDir = "$env:USERPROFILE\.open-ace-agent"
    if ((Test-Path "$defaultDir\agent.py")) {
        $InstallDir = $defaultDir
    }
}

if (-not (Test-Path "$InstallDir\agent.py")) {
    Write-ErrorMsg "未找到 agent.py（查找目录: $InstallDir）"
    Write-Host ""
    Write-Host "请先在远程机器上执行安装注册命令（仅需一次），例如:" -ForegroundColor White
    Write-Host "  powershell -Command [Invoke-WebRequest -Uri 服务器地址/api/remote/agent/install.ps1 -OutFile install.ps1; powershell -ExecutionPolicy Bypass -File install.ps1 -ServerUrl 服务器地址 -RegistrationToken 注册令牌]" -ForegroundColor Gray
    exit 1
}

$configPath = "$InstallDir\config.json"
if (-not (Test-Path $configPath)) {
    Write-ErrorMsg "未找到配置文件 $configPath"
    Write-Host "请先执行安装注册命令，或检查安装目录是否正确 (-InstallDir 目录)" -ForegroundColor Gray
    exit 1
}

# 读取配置用于展示
$cfg = $null
try {
    $cfg = Get-Content $configPath -Raw | ConvertFrom-Json
} catch {
    Write-Warn "无法解析 config.json: $_"
}

$serverUrl = ""
$machineId = ""
if ($cfg) {
    $serverUrl = [string]$cfg.server_url
    $machineId = [string]$cfg.machine_id
}

# ============================================================================
# 2. 检测 agent 进程是否已在运行
# ============================================================================
function Get-AgentProcess {
    $agentPyPath = $InstallDir.Replace('\', '\\')
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'python3.exe' OR Name = 'pythonw.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -and $_.CommandLine -match "agent\.py" -and $_.CommandLine -match $agentPyPath }
}

function Show-Status {
    $procs = @(Get-AgentProcess)
    if ($procs.Count -gt 0) {
        Write-Ok "Agent 正在运行 (PID: $($procs[0].ProcessId))"
        Write-Host "  机器 ID: $machineId" -ForegroundColor Gray
        Write-Host "  服务器: $serverUrl" -ForegroundColor Gray
    } else {
        Write-Warn "Agent 未运行"
        Write-Host "  机器 ID: $machineId" -ForegroundColor Gray
        Write-Host "  服务器: $serverUrl" -ForegroundColor Gray
        Write-Host "  启动方式: powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`"" -ForegroundColor Gray
    }
}

if ($Status) {
    Show-Status
    exit 0
}

if ($Stop) {
    $procs = @(Get-AgentProcess)
    if ($procs.Count -gt 0) {
        foreach ($p in $procs) {
            Write-Info "停止 agent 进程 (PID: $($p.ProcessId))..."
            Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
        }
        Write-Ok "Agent 已停止"
    } else {
        Write-Warn "Agent 未在运行"
    }
    exit 0
}

# ============================================================================
# 3. 配置开机自启（计划任务）
# ============================================================================
if ($InstallAutoStart) {
    Write-Info "配置开机自启 (计划任务)..."
    try {
        $taskName = "OpenACEAgent"
        $existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
        if ($existingTask) {
            Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
        }
        $action = New-ScheduledTaskAction -Execute "python" -Argument "`"$InstallDir\agent.py`"" -WorkingDirectory $InstallDir
        $trigger = New-ScheduledTaskTrigger -AtLogOn
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
        $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
        Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Open ACE Remote Agent - Auto-start" -Force | Out-Null
        Write-Ok "已配置计划任务 '$taskName'，登录后自动启动 agent"
    } catch {
        Write-ErrorMsg "配置计划任务失败: $_"
        Write-Host "可手动创建: 任务计划程序 -> 新建任务 -> 登录时运行 -> 程序 python，参数 `"$InstallDir\agent.py`"" -ForegroundColor Gray
        exit 1
    }
}

# ============================================================================
# 4. 启动 agent
# ============================================================================
$procs = @(Get-AgentProcess)
if ($procs.Count -gt 0) {
    Write-Ok "Agent 已在运行 (PID: $($procs[0].ProcessId))，无需重复启动"
    exit 0
}

Write-Info "启动 Open ACE Remote Agent..."
$logPath = "$InstallDir\agent.log"

# 后台启动 agent.py，日志写入 agent.log
$agentProc = Start-Process -FilePath "python" -ArgumentList "`"$InstallDir\agent.py`"" -WorkingDirectory $InstallDir -WindowStyle Hidden -RedirectStandardOutput $logPath -RedirectStandardError "$InstallDir\agent-error.log" -PassThru

if ($agentProc -and $agentProc.Id) {
    Write-Ok "Agent 已启动 (PID: $($agentProc.Id))"
    Write-Host "  机器 ID: $machineId" -ForegroundColor Gray
    Write-Host "  服务器: $serverUrl" -ForegroundColor Gray
    Write-Host "  日志: $logPath" -ForegroundColor Gray
    Write-Host ""
    Write-Host "Agent 启动后会自动连接服务器并在数秒内恢复在线状态。" -ForegroundColor White
    Write-Host "若服务器重启过，Agent 会在 1-60 秒内自动重连，无需人工干预。" -ForegroundColor White
    Write-Host ""
    Write-Host "如需开机自启，请执行: powershell -ExecutionPolicy Bypass -File `"$($MyInvocation.MyCommand.Path)`" -InstallAutoStart" -ForegroundColor Cyan
} else {
    Write-ErrorMsg "Agent 启动失败"
    Write-Host "请检查: python 是否在 PATH 中、$configPath 是否有效" -ForegroundColor Gray
    exit 1
}
