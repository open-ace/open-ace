# Open ACE Remote Agent - Uninstall Script (Windows)
#
# Usage:
#   Invoke-WebRequest -Uri "<server>/api/remote/agent/uninstall.ps1" | Invoke-Expression
#
# Options:
#   -KeepConfig    Keep configuration files
#   -KeepDeps      Keep Python dependencies
#

param(
    [switch]$KeepConfig,
    [switch]$KeepDeps
)

$InstallDir = "$env:USERPROFILE\.open-ace-agent"

function Write-Info { Write-Host "[INFO] $args" -ForegroundColor Blue }
function Write-Success { Write-Host "[OK] $args" -ForegroundColor Green }
function Write-Warn { Write-Host "[WARN] $args" -ForegroundColor Yellow }

Write-Info "Uninstalling Open ACE Remote Agent..."
Write-Info "Install dir: $InstallDir"

# Step 1: Stop and remove service
Write-Info "Stopping agent service..."

# Try to stop scheduled task
$TaskName = "OpenACEAgent"
$Task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($Task) {
    Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Success "Stopped and removed scheduled task"
} else {
    Write-Info "No scheduled task found"
}

# Try to kill process directly
$AgentProcess = Get-Process -Name "python" -ErrorAction SilentlyContinue | 
    Where-Object { $_.Path -like "*agent.py*" }
if ($AgentProcess) {
    Stop-Process -Id $AgentProcess.Id -Force -ErrorAction SilentlyContinue
    Write-Success "Killed agent process"
}

# Step 2: Get Python path from config
$PythonPath = "python"
if (Test-Path "$InstallDir\config.json") {
    try {
        $Config = Get-Content "$InstallDir\config.json" | ConvertFrom-Json
        if ($Config.python_path) {
            $PythonPath = $Config.python_path
        }
    } catch {}
}

# Step 3: Remove Python dependencies
if (-not $KeepDeps) {
    Write-Info "Removing Python dependencies..."
    foreach ($pkg in @("requests", "websocket-client")) {
        & $PythonPath -m pip uninstall -q -y $pkg 2>$null
    }
    Write-Success "Python dependencies removed"
} else {
    Write-Info "Keeping Python dependencies"
}

# Step 4: Remove installation directory
Write-Info "Removing installation files..."

if ($KeepConfig) {
    # Keep config, remove agent files
    $FilesToRemove = @(
        "agent.py", "config.py", "executor.py", "system_info.py",
        "requirements.txt", "__init__.py", "agent.log", "agent-error.log",
        "start.ps1", "stop.ps1"
    )
    foreach ($file in $FilesToRemove) {
        $filePath = Join-Path $InstallDir $file
        if (Test-Path $filePath) {
            Remove-Item $filePath -Force -ErrorAction SilentlyContinue
        }
    }
    $CliAdaptersPath = Join-Path $InstallDir "cli_adapters"
    if (Test-Path $CliAdaptersPath) {
        Remove-Item $CliAdaptersPath -Recurse -Force -ErrorAction SilentlyContinue
    }
    Write-Success "Agent files removed (config preserved)"
} else {
    # Remove everything
    if (Test-Path $InstallDir) {
        Remove-Item $InstallDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-Success "Installation directory removed"
    } else {
        Write-Info "Installation directory not found"
    }
}

Write-Host ""
Write-Success "============================================"
Write-Success "Uninstall completed successfully!"
Write-Success "============================================"
Write-Host ""

if ($KeepConfig) {
    Write-Info "Config preserved at: $InstallDir"
}

Write-Info "Machine record still exists on server"
Write-Info "Delete it via the web UI or API"
