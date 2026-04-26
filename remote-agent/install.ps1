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

# Trap unhandled errors to prevent silent exits
trap {
    Write-Host "[ERROR] Script failed: $_" -ForegroundColor Red
    Write-Host $_.ScriptStackTrace -ForegroundColor Red
    exit 1
}

Write-Host "Open ACE Remote Agent Installer" -ForegroundColor Blue
Write-Host "================================" -ForegroundColor Blue
Write-Host "Server: $ServerUrl"
Write-Host "Machine name: $MachineName"
Write-Host "Install CLI: $InstallCli"
Write-Host "Install dir: $InstallDir"
Write-Host ""

$ServerUrl = $ServerUrl.TrimEnd('/')

# Step 1: Check prerequisites
Write-Host "[INFO] Checking prerequisites..." -ForegroundColor Cyan

try {
    $pythonVersion = python --version 2>&1
    Write-Host "[OK] Python found: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Python 3 is not installed. Please install Python 3.8+ first." -ForegroundColor Red
    exit 1
}

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
    $downloaded = $false
    for ($retry = 1; $retry -le 3; $retry++) {
        try {
            Start-BitsTransfer -Source "$agentUrl/$file" -Destination "$InstallDir\$file" -ErrorAction Stop | Out-Null
            $downloaded = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($downloaded) {
        Write-Host "  [OK] Downloaded $file" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Could not download $file after 3 retries" -ForegroundColor Yellow
    }
}

foreach ($file in $adapterFiles) {
    $downloaded = $false
    for ($retry = 1; $retry -le 3; $retry++) {
        try {
            Start-BitsTransfer -Source "$agentUrl/cli_adapters/$file" -Destination "$InstallDir\cli_adapters\$file" -ErrorAction Stop | Out-Null
            $downloaded = $true
            break
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }
    if ($downloaded) {
        Write-Host "  [OK] Downloaded cli_adapters/$file" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Could not download cli_adapters/$file after 3 retries" -ForegroundColor Yellow
    }
}

New-Item -ItemType File -Force -Path "$InstallDir\__init__.py" | Out-Null
Write-Host "[OK] Agent files installed" -ForegroundColor Green

# Step 4: Install Python dependencies
Write-Host "[INFO] Installing Python dependencies..." -ForegroundColor Cyan
if (Test-Path "$InstallDir\requirements.txt") {
    $pipOutFile = "$env:TEMP\pip_install_out_$([System.Guid]::NewGuid()).log"
    $pipErrFile = "$env:TEMP\pip_install_err_$([System.Guid]::NewGuid()).log"
    $pipProc = Start-Process -FilePath "python" -ArgumentList "-m", "pip", "install", "-r", "$InstallDir\requirements.txt" -NoNewWindow -Wait -RedirectStandardOutput $pipOutFile -RedirectStandardError $pipErrFile -PassThru
    if ($pipProc.ExitCode -ne 0) {
        Write-Host "[WARN] pip install failed (exit code $($pipProc.ExitCode))" -ForegroundColor Yellow
        if (Test-Path $pipErrFile) { Get-Content $pipErrFile }
    } else {
        Write-Host "[OK] Dependencies installed" -ForegroundColor Green
    }
    Remove-Item $pipOutFile -ErrorAction SilentlyContinue
    Remove-Item $pipErrFile -ErrorAction SilentlyContinue
} else {
    Write-Host "[WARN] requirements.txt not found, skipping" -ForegroundColor Yellow
}

# Step 5: Optionally install CLI tool
if ($InstallCli) {
    Write-Host "[INFO] Installing CLI tool: $InstallCli..." -ForegroundColor Cyan
    if (Get-Command npm -ErrorAction SilentlyContinue) {
        # Temporarily relax error handling for npm (it may output warnings to stderr)
        $prevErrorAction = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        
        switch ($InstallCli) {
            "qwen-code-cli" {
                npm install -g "@qwen-code/qwen-code@latest" 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[OK] qwen-code-cli installed" -ForegroundColor Green
                } else {
                    Write-Host "[WARN] Failed to install qwen-code-cli" -ForegroundColor Yellow
                }
            }
            "claude-code" {
                npm install -g "@anthropic-ai/claude-code@latest" 2>&1 | Out-Null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "[OK] Claude Code installed" -ForegroundColor Green
                } else {
                    Write-Host "[WARN] Failed to install Claude Code" -ForegroundColor Yellow
                }
            }
        }
        
        # Restore error handling
        $ErrorActionPreference = $prevErrorAction
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

foreach ($cli in @("qwen", "claude", "openclaw")) {
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
    $bodyFile = "$env:TEMP\agent_register_$([System.Guid]::NewGuid()).json"
    $body | Out-File -FilePath $bodyFile -Encoding utf8 -NoNewline

    $responseFile = "$env:TEMP\agent_response_$([System.Guid]::NewGuid()).json"
    $curlPath = "$env:SYSTEMROOT\System32\curl.exe"
    & $curlPath -s -X POST -H "Content-Type: application/json" -d "@$bodyFile" -o $responseFile "$ServerUrl/api/remote/agent/register" 2>&1 | Out-Null

    if (-not (Test-Path $responseFile)) {
        Write-Host "[ERROR] Registration failed: no response from server" -ForegroundColor Red
        exit 1
    }

    $responseRaw = Get-Content $responseFile -Raw
    $response = $responseRaw | ConvertFrom-Json
    Remove-Item $bodyFile -ErrorAction SilentlyContinue
    Remove-Item $responseFile -ErrorAction SilentlyContinue

    if ($response.success) {
        Write-Host "[OK] Machine registered successfully!" -ForegroundColor Green
    } elseif ($response.error) {
        Write-Host "[ERROR] Registration failed: $($response.error)" -ForegroundColor Red
        Write-Host "       This may happen if the machine is already registered. Delete it from the server first to re-register." -ForegroundColor Yellow
        exit 1
    } else {
        Write-Host "[ERROR] Registration failed. Response: $responseRaw" -ForegroundColor Red
        exit 1
    }
} catch {
    Write-Host "[ERROR] Registration failed: $_" -ForegroundColor Red
    exit 1
}

# Step 8: Set up auto-start via Windows Task Scheduler
Write-Host "[INFO] Setting up auto-start..." -ForegroundColor Cyan
try {
    $taskName = "OpenACEAgent"
    $action = New-ScheduledTaskAction -Execute "python" -Argument "`"$InstallDir\agent.py`"" -WorkingDirectory $InstallDir
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    $principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description "Open ACE Remote Agent - Auto-start" -Force | Out-Null
    Write-Host "[OK] Auto-start configured (Task Scheduler)" -ForegroundColor Green
} catch {
    Write-Host "[WARN] Failed to configure auto-start: $_" -ForegroundColor Yellow
    Write-Host "       Manual setup: nssm install OpenACEAgent python $InstallDir\agent.py" -ForegroundColor Yellow
}

# Step 9: Start the agent immediately
Write-Host "[INFO] Starting Open ACE Remote Agent..." -ForegroundColor Cyan
$agentProc = Start-Process -FilePath "python" -ArgumentList "`"$InstallDir\agent.py`"" -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru
if ($agentProc -and $agentProc.Id) {
    Write-Host "[OK] Agent started (PID: $($agentProc.Id))" -ForegroundColor Green
} else {
    Write-Host "[WARN] Failed to start agent. Start manually: python $InstallDir\agent.py" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "[OK] ============================================" -ForegroundColor Green
Write-Host "[OK] Open ACE Remote Agent installed successfully!" -ForegroundColor Green
Write-Host "[OK] ============================================" -ForegroundColor Green
Write-Host ""
Write-Host "Machine ID: $machineId"
Write-Host "Config: $InstallDir\config.json"
Write-Host "Agent PID: $($agentProc.Id)"
