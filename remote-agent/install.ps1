# Open ACE Remote Agent - Windows Install Script
#
# Usage:
#   Invoke-WebRequest -Uri "https://<server>/api/remote/agent/install.ps1" | Invoke-Expression
#
# Or with parameters:
#   .\install.ps1 -ServerUrl "https://<server>" -RegistrationToken "<token>"
#
# Parameters:
#   -ServerUrl           Open ACE server URL (required)
#   -RegistrationToken   Registration token from admin (required)
#   -MachineName         Machine display name (default: $env:COMPUTERNAME)
#   -InstallCli          CLI tool to install: qwen-code-cli, claude-code (default: qwen-code-cli)
#   -InstallDir          Installation directory (default: $env:USERPROFILE\.open-ace-agent)

param(
    [Parameter(Mandatory=$true)]
    [string]$ServerUrl,

    [Parameter(Mandatory=$true)]
    [string]$RegistrationToken,

    [string]$MachineName = $env:COMPUTERNAME,
    [string]$InstallCli = "qwen-code-cli",
    [string]$InstallDir = "$env:USERPROFILE\.open-ace-agent"
)

$ErrorActionPreference = "Stop"

Write-Host "Open ACE Remote Agent Installer" -ForegroundColor Blue
Write-Host "================================" -ForegroundColor Blue
Write-Host "Server: $ServerUrl"
Write-Host "Machine name: $MachineName"
Write-Host "Install CLI: $InstallCli"
Write-Host "Install dir: $InstallDir"
Write-Host ""

# Remove trailing slash
$ServerUrl = $ServerUrl.TrimEnd('/')

# Step 1: Check prerequisites
Write-Host "[INFO] Checking prerequisites..." -ForegroundColor Cyan

# Check Python 3
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[OK] Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python 3 is not installed. Please install Python 3.8+ first." -ForegroundColor Red
    exit 1
}

# Check pip
try {
    python -m pip --version | Out-Null
    Write-Host "[OK] pip found" -ForegroundColor Green
} catch {
    Write-Host "[WARN] pip not found. Installing pip..." -ForegroundColor Yellow
    python -m ensurepip --upgrade
}

# Step 2: Create installation directory
Write-Host "[INFO] Creating installation directory..." -ForegroundColor Cyan
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
New-Item -ItemType Directory -Force -Path "$InstallDir\cli_adapters" | Out-Null
Write-Host "[OK] Directory created: $InstallDir" -ForegroundColor Green

# Step 3: Download agent files
Write-Host "[INFO] Downloading agent files..." -ForegroundColor Cyan

$agentUrl = "$ServerUrl/api/remote/agent/files"
$files = @("agent.py", "config.py", "executor.py", "system_info.py", "requirements.txt")
$adapterFiles = @("__init__.py", "base.py", "qwen_code.py", "claude_code.py", "openclaw.py")

foreach ($file in $files) {
    try {
        Invoke-WebRequest -Uri "$agentUrl/$file" -OutFile "$InstallDir\$file" -ErrorAction Stop
    } catch {
        Write-Host "[WARN] Could not download $file" -ForegroundColor Yellow
    }
}

foreach ($file in $adapterFiles) {
    try {
        Invoke-WebRequest -Uri "$agentUrl/cli_adapters/$file" -OutFile "$InstallDir\cli_adapters\$file" -ErrorAction Stop
    } catch {
        Write-Host "[WARN] Could not download cli_adapters/$file" -ForegroundColor Yellow
    }
}

New-Item -ItemType File -Force -Path "$InstallDir\__init__.py" | Out-Null
Write-Host "[OK] Agent files installed" -ForegroundColor Green

# Step 4: Install Python dependencies
Write-Host "[INFO] Installing Python dependencies..." -ForegroundColor Cyan
if (Test-Path "$InstallDir\requirements.txt") {
    python -m pip install -q -r "$InstallDir\requirements.txt"
}
Write-Host "[OK] Dependencies installed" -ForegroundColor Green

# Step 5: Optionally install CLI tool
if ($InstallCli) {
    Write-Host "[INFO] Installing CLI tool: $InstallCli..." -ForegroundColor Cyan
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        switch ($InstallCli) {
            "qwen-code-cli" {
                npm install -g "@anthropic-ai/qwen-code@latest" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[OK] qwen-code-cli installed" -ForegroundColor Green
                } else {
                    Write-Host "[WARN] Failed to install qwen-code-cli" -ForegroundColor Yellow
                }
            }
            "claude-code" {
                npm install -g "@anthropic-ai/claude-code@latest" 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[OK] Claude Code installed" -ForegroundColor Green
                } else {
                    Write-Host "[WARN] Failed to install Claude Code" -ForegroundColor Yellow
                }
            }
        }
    } else {
        Write-Host "[WARN] npm not found. Skipping CLI installation." -ForegroundColor Yellow
    }
}

# Step 6: Generate machine ID and save config
Write-Host "[INFO] Generating configuration..." -ForegroundColor Cyan
$machineId = [guid]::NewGuid().ToString()

$config = @{
    server_url = $ServerUrl
    machine_id = $machineId
    machine_name = $MachineName
    registration_token = $RegistrationToken
    cli_tool = $InstallCli
    heartbeat_interval = 60
    reconnect_backoff_max = 60
}

$config | ConvertTo-Json | Set-Content -Path "$InstallDir\config.json"
Write-Host "[OK] Configuration saved" -ForegroundColor Green

# Step 7: Register with server
Write-Host "[INFO] Registering with Open ACE server..." -ForegroundColor Cyan

$osType = "Windows"
$osVersion = [System.Environment]::OSVersion.Version.ToString()
$hostname = $env:COMPUTERNAME

$capabilities = @{
    os = "windows"
    os_version = $osVersion
    cpu_cores = [System.Environment]::ProcessorCount
}

# Check installed CLIs
foreach ($cli in @("qwen-code", "claude", "openclaw")) {
    $cmd = Get-Command $cli -ErrorAction SilentlyContinue
    $capabilities["${cli}_installed"] = ($null -ne $cmd)
}

$body = @{
    registration_token = $RegistrationToken
    machine_id = $machineId
    machine_name = $MachineName
    hostname = $hostname
    os_type = $osType
    os_version = $osVersion
    capabilities = $capabilities
    agent_version = "1.0.0"
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "$ServerUrl/api/remote/agent/register" -Method Post -Body $body -ContentType "application/json"
    if ($response.success) {
        Write-Host "[OK] Machine registered successfully!" -ForegroundColor Green
    } else {
        Write-Host "[ERROR] Registration failed" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "[ERROR] Registration failed: $_" -ForegroundColor Red
    exit 1
}

# Step 8: Install as Windows service (optional)
Write-Host "[INFO] To run as a service, use Task Scheduler or NSSM:" -ForegroundColor Cyan
Write-Host "  nssm install OpenACEAgent python $InstallDir\agent.py"
Write-Host ""

Write-Host "[OK] ============================================" -ForegroundColor Green
Write-Host "[OK] Open ACE Remote Agent installed successfully!" -ForegroundColor Green
Write-Host "[OK] ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Machine ID: $machineId"
Write-Host "Config: $InstallDir\config.json"
Write-Host ""
Write-Host "To start the agent: python $InstallDir\agent.py"
