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
#   -CaBundlePath        PEM CA bundle for a private/self-signed server
#   -InsecureSkipTlsVerify
#                       Disable TLS verification (dangerous, explicit only)

param(
    [Parameter(Mandatory=$true)]
    [string]$ServerUrl,

    [Parameter(Mandatory=$true)]
    [string]$RegistrationToken,

    [string]$MachineName = $env:COMPUTERNAME,
    [string]$InstallCli = "qwen-code-cli",
    [string]$InstallDir = "$env:USERPROFILE\.open-ace-agent",
    [string]$CaBundlePath = "",
    [switch]$InsecureSkipTlsVerify,
    [switch]$SkipCodeServer
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

if ($CaBundlePath -and $InsecureSkipTlsVerify) {
    Write-Host "[ERROR] -CaBundlePath and -InsecureSkipTlsVerify are mutually exclusive" -ForegroundColor Red
    exit 1
}
if ($CaBundlePath -and -not (Test-Path -Path $CaBundlePath -PathType Leaf)) {
    Write-Host "[ERROR] CA bundle not found: $CaBundlePath" -ForegroundColor Red
    exit 1
}
if ($CaBundlePath) { $CaBundlePath = (Resolve-Path -Path $CaBundlePath).Path }
if ($InsecureSkipTlsVerify) {
    Write-Host "[WARN] TLS certificate verification is explicitly disabled" -ForegroundColor Yellow
}

$curlPath = "$env:SYSTEMROOT\System32\curl.exe"
$serverCurlTlsArgs = @("--ssl-no-revoke")
if ($CaBundlePath) {
    $serverCurlTlsArgs += @("--cacert", $CaBundlePath)
} elseif ($InsecureSkipTlsVerify) {
    $serverCurlTlsArgs += "--insecure"
}

function Invoke-ServerDownload {
    param([string]$Source, [string]$Destination)
    if ($CaBundlePath -or $InsecureSkipTlsVerify) {
        & $curlPath -fsSL @serverCurlTlsArgs -o $Destination $Source
        if ($LASTEXITCODE -ne 0) { throw "curl failed with exit code $LASTEXITCODE" }
    } else {
        Start-BitsTransfer -Source $Source -Destination $Destination -ErrorAction Stop | Out-Null
    }
}

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
$files = @(
    "agent.py",
    "config.py",
    "configure-code-server-proxy.ps1",
    "constants.py",
    "env_security.py",
    "executor.py",
    "system_info.py",
    "requirements.txt",
    "terminal_menu.py",
    "terminal_server.py",
    "terminal_relay.py",
    "websocket_proxy.py",
    "session_sync.py",
    "openace_cli.py",
    "cli_settings.py",
    "zcode_app_server.py",
    "tls_config.py",
    "start-agent.cmd",
    "start-agent.ps1",
    "start-agent.sh"
)
$adapterFiles = @("__init__.py", "base.py", "qwen_code.py", "claude_code.py", "codex_cli.py", "codex_jsonl_parser.py", "openclaw.py", "usage_parser.py", "zcode.py")

foreach ($file in $files) {
    $downloaded = $false
    for ($retry = 1; $retry -le 3; $retry++) {
        try {
            Invoke-ServerDownload -Source "$agentUrl/$file" -Destination "$InstallDir\$file"
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
            Invoke-ServerDownload -Source "$agentUrl/cli_adapters/$file" -Destination "$InstallDir\cli_adapters\$file"
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

# Install the user-facing Open ACE CLI wrapper.
Write-Host "[INFO] Installing openace command..." -ForegroundColor Cyan
$openaceCmd = "$InstallDir\openace.cmd"
@"
@echo off
python "$InstallDir\openace_cli.py" %*
"@ | Set-Content -Path $openaceCmd -Encoding ASCII

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($userPath.Split(';') -contains $InstallDir)) {
    [Environment]::SetEnvironmentVariable("Path", "$userPath;$InstallDir", "User")
    Write-Host "[WARN] $InstallDir was added to your user PATH. Restart the shell before using openace globally." -ForegroundColor Yellow
}
Write-Host "[OK] openace command installed: $openaceCmd" -ForegroundColor Green

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

# Step 5.5: Install git and code-server
Write-Host "[INFO] Checking for git and code-server..." -ForegroundColor Cyan

# Install git if not present
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd) {
    $gitVer = git --version 2>&1
    Write-Host "[OK] git already installed: $gitVer" -ForegroundColor Green
} else {
    Write-Host "[INFO] git not found, attempting to install..." -ForegroundColor Cyan
    $gitInstalled = $false
    try {
        $wingetCmd = Get-Command winget -ErrorAction SilentlyContinue
        if ($wingetCmd) {
            winget install Git.Git --accept-source-agreements --accept-package-agreements 2>&1 | Out-Null
            $gitInstalled = $true
        }
    } catch {
        # winget not available
    }
    if ($gitInstalled -and (Get-Command git -ErrorAction SilentlyContinue)) {
        Write-Host "[OK] git installed: $(git --version 2>&1)" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Failed to install git. Remote workspace will be missing file changes panel." -ForegroundColor Yellow
        Write-Host "       Please install git manually: https://git-scm.com/download/win" -ForegroundColor Yellow
    }
}

# ============================================================================
# Helper functions for code-server installation
# ============================================================================

# Function: Get-NpmGlobalPrefix
# Description: Dynamically get npm global prefix directory
function Get-NpmGlobalPrefix {
    $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
    if (-not $npmCmd) {
        return $null
    }

    try {
        $prefix = (npm config get prefix 2>&1).Trim()
        if ($prefix -and (Test-Path -Path $prefix -PathType Container)) {
            return $prefix
        }
    } catch {
        # npm command failed, will use default
    }

    # Fallback to default
    $defaultPrefix = "$env:APPDATA\npm"
    Write-Host "[WARN] Could not determine npm prefix, using default: $defaultPrefix" -ForegroundColor Yellow
    return $defaultPrefix
}

# Function: Test-NpmGlobalWritable
# Description: Check if npm global directory is writable
function Test-NpmGlobalWritable {
    param([string]$Prefix)

    if (-not $Prefix) {
        return $false
    }

    $nodeModulesPath = Join-Path $Prefix "node_modules"

    # Ensure node_modules directory exists
    if (-not (Test-Path -Path $nodeModulesPath)) {
        try {
            New-Item -ItemType Directory -Path $nodeModulesPath -Force | Out-Null
        } catch {
            return $false
        }
    }

    # Test write permission by creating a temporary file
    $testFile = Join-Path $nodeModulesPath ".write_test_$([System.Guid]::NewGuid().ToString())"
    try {
        [System.IO.File]::WriteAllText($testFile, "test")
        Remove-Item -Path $testFile -Force -ErrorAction SilentlyContinue
        return $true
    } catch {
        return $false
    }
}

# Function: Test-NodeVersionCompatibility
# Description: Check Node.js version compatibility for code-server
# Returns: @{Compatible=$true/$false; Version="v18.17.0"; Major=18; Minor=17; Warning=$null}
function Test-NodeVersionCompatibility {
    $nodeCmd = Get-Command node -ErrorAction SilentlyContinue
    if (-not $nodeCmd) {
        return @{
            Compatible = $false
            Version = "not found"
            Major = 0
            Minor = 0
            Warning = "Node.js not found. code-server requires Node.js v18+ (recommended: v20 or v22 LTS)"
        }
    }

    try {
        $versionOutput = (node --version 2>&1).Trim()

        # Parse version string (formats: v18.17.0, 18.17.0, 18.17)
        if ($versionOutput -match '^v?(\d+)\.(\d+)(?:\.(\d+))?') {
            $major = [int]$matches[1]
            $minor = [int]$matches[2]

            $result = @{
                Compatible = $true
                Version = $versionOutput
                Major = $major
                Minor = $minor
                Warning = $null
            }

            if ($major -lt 18) {
                $result.Compatible = $false
                $result.Warning = "Node.js version $versionOutput is not compatible with code-server. code-server requires Node.js v18+ (recommended: v20 or v22 LTS)"
            } elseif ($major -eq 17) {
                # v17 is a non-LTS interim version
                $result.Compatible = $false
                $result.Warning = "Node.js v17.x is a non-LTS interim version and may not be compatible with code-server. Please use Node.js v18+ LTS (recommended: v20 or v22)"
            } elseif ($major -eq 18) {
                # v18 is supported but not recommended
                $result.Warning = "Node.js v18 is compatible but v20 or v22 LTS is recommended for best compatibility with code-server"
            }

            return $result
        } else {
            return @{
                Compatible = $false
                Version = $versionOutput
                Major = 0
                Minor = 0
                Warning = "Could not parse Node.js version: $versionOutput"
            }
        }
    } catch {
        return @{
            Compatible = $false
            Version = "error"
            Major = 0
            Minor = 0
            Warning = "Failed to check Node.js version: $_"
        }
    }
}

# Function: Clear-CodeServerResidue
# Description: Clean up residual code-server files and detect running processes
function Clear-CodeServerResidue {
    param([string]$NpmPrefix)

    if (-not $NpmPrefix) {
        return
    }

    # Check for running code-server processes using WMI (compatible with PowerShell 5.1)
    # CommandLine property on Get-Process only works in PowerShell 7+
    try {
        $codeServerProcesses = Get-WmiObject Win32_Process -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -eq 'node.exe' -and $_.CommandLine -and $_.CommandLine -match 'code-server' }

        if ($codeServerProcesses) {
            Write-Host "[WARN] Detected running code-server process(es). Please close them before reinstalling." -ForegroundColor Yellow
            Write-Host "       PIDs: $($codeServerProcesses.ProcessId -join ', ')" -ForegroundColor Yellow
            Write-Host "       You can close them manually or they will be replaced on next install." -ForegroundColor Yellow
        }
    } catch {
        # WMI query failed, skip process detection (not critical)
    }

    # Clean up residual files
    $pathsToClean = @(
        Join-Path $NpmPrefix "node_modules\code-server"
        Join-Path $NpmPrefix "code-server"
        Join-Path $NpmPrefix "code-server.cmd"
        Join-Path $NpmPrefix "code-server.ps1"
    )

    $cleanedCount = 0
    foreach ($path in $pathsToClean) {
        if (Test-Path -Path $path) {
            try {
                Remove-Item -Path $path -Recurse -Force -ErrorAction SilentlyContinue
                $cleanedCount++
            } catch {
                # Ignore cleanup errors
            }
        }
    }

    if ($cleanedCount -gt 0) {
        Write-Host "[INFO] Cleaned up $cleanedCount previous code-server installation file(s)" -ForegroundColor Cyan
    }
}

# Function: Test-CodeServerExecution
# Description: Verify code-server is actually executable after installation
function Test-CodeServerExecution {
    $csCmd = Get-Command code-server -ErrorAction SilentlyContinue
    if (-not $csCmd) {
        return @{ Success = $false; Error = "code-server command not found" }
    }

    try {
        $versionOutput = code-server --version 2>&1
        $exitCode = $LASTEXITCODE

        if ($exitCode -eq 0 -and $versionOutput -match '\d+\.\d+') {
            return @{ Success = $true; Version = ($versionOutput | Select-Object -First 1) }
        } else {
            return @{ Success = $false; Error = "Exit code: $exitCode, Output: $versionOutput" }
        }
    } catch {
        return @{ Success = $false; Error = $_.ToString() }
    }
}

# Function: Get-NpmRegistry
# Description: Get current npm registry URL
function Get-NpmRegistry {
    try {
        $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
        if ($npmCmd) {
            return (npm config get registry 2>&1).Trim()
        }
    } catch {
        # Ignore errors
    }
    return "https://registry.npmjs.org"
}

# Function: Invoke-NpmInstall
# Description: Run npm install using System.Diagnostics.Process with timeout
function Invoke-NpmInstall {
    param(
        [string]$Package,
        [int]$TimeoutSeconds = 300
    )

    $npmPath = (Get-Command npm).Source
    $stdoutFile = "$env:TEMP\npm_install_stdout_$([System.Guid]::NewGuid()).log"
    $stderrFile = "$env:TEMP\npm_install_stderr_$([System.Guid]::NewGuid()).log"

    try {
        $process = New-Object System.Diagnostics.Process
        $process.StartInfo.FileName = $npmPath
        $process.StartInfo.Arguments = "install -g $Package"
        $process.StartInfo.UseShellExecute = $false
        $process.StartInfo.RedirectStandardOutput = $true
        $process.StartInfo.RedirectStandardError = $true
        $process.StartInfo.CreateNoWindow = $true
        $process.EnableRaisingEvents = $true

        $null = $process.Start()

        # Read output asynchronously
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()

        # Wait with timeout
        $exited = $process.WaitForExit($TimeoutSeconds * 1000)

        if (-not $exited) {
            # Timeout - kill the process tree
            Write-Host "[WARN] npm install timed out after $TimeoutSeconds seconds" -ForegroundColor Yellow
            try {
                # Kill the process and its children
                $process.Kill()
            } catch {
                Write-Host "[WARN] Could not terminate npm process: $_" -ForegroundColor Yellow
            }

            # Wait for async tasks to complete (max 5 seconds) to capture any output
            try {
                $null = [System.Threading.Tasks.Task]::WaitAll(@($stdoutTask, $stderrTask), 5000)
            } catch {
                # Ignore task wait errors
            }

            return @{
                Success = $false
                Timeout = $true
                Stderr = $stderrTask.Result
            }
        }

        # Process completed
        $stdout = $stdoutTask.Result
        $stderr = $stderrTask.Result

        # Save output to files for diagnosis
        $stdout | Out-File -FilePath $stdoutFile -Encoding utf8
        $stderr | Out-File -FilePath $stderrFile -Encoding utf8

        return @{
            Success = ($process.ExitCode -eq 0)
            ExitCode = $process.ExitCode
            Stdout = $stdout
            Stderr = $stderr
            StdoutFile = $stdoutFile
            StderrFile = $stderrFile
        }
    } finally {
        if ($process -and -not $process.HasExited) {
            try { $process.Kill() } catch { }
        }
    }
}

# Function: Show-InstallationFailureDiagnosis
# Description: Print detailed diagnosis for npm install failure
function Show-InstallationFailureDiagnosis {
    param(
        [int]$ExitCode,
        [string]$Stderr,
        [string]$Stdout,
        [bool]$Timeout
    )

    if ($Timeout) {
        Write-Host "" -ForegroundColor Yellow
        Write-Host "[DIAGNOSIS] Installation timed out" -ForegroundColor Yellow
        Write-Host "  Possible causes:" -ForegroundColor White
        Write-Host "    - Slow network connection" -ForegroundColor Gray
        Write-Host "    - Large package size" -ForegroundColor Gray
        Write-Host "    - npm registry connectivity issues" -ForegroundColor Gray
        Write-Host "" -ForegroundColor White
        Write-Host "  Suggestions:" -ForegroundColor White
        Write-Host "    - Try again later" -ForegroundColor Gray
        Write-Host "    - Use a npm mirror (China: npm config set registry https://registry.npmmirror.com)" -ForegroundColor Gray
        return
    }

    Write-Host "" -ForegroundColor Yellow
    Write-Host "[DIAGNOSIS] Installation failed (exit code: $ExitCode)" -ForegroundColor Yellow

    # Check for common error patterns
    $errorPatterns = @{
        "EACCES|EPERM" = @{
            Diagnosis = "Permission denied"
            Suggestion = "Run PowerShell as Administrator or configure npm to use user directory:`n            npm config set prefix `"`$env:USERPROFILE\npm-global`""
        }
        "ENOGIT" = @{
            Diagnosis = "git not found"
            Suggestion = "Install git from https://git-scm.com/download/win"
        }
        "ETIMEDOUT|ECONNREFUSED" = @{
            Diagnosis = "Network connectivity issue"
            Suggestion = "Check your network connection and proxy settings"
        }
        "gyp ERR!" = @{
            Diagnosis = "Native module compilation failed"
            Suggestion = "Install Visual Studio Build Tools:`n            1. Download from https://visualstudio.microsoft.com/downloads/`n            2. Select 'Desktop development with C++' workload`n            Or run: npm install -g windows-build-tools (as Administrator)"
        }
        "EPROTO|CERT_HAS_EXPIRED" = @{
            Diagnosis = "TLS certificate issue"
            Suggestion = "Update Node.js to the latest version or check your system certificates"
        }
        "ENOTFOUND" = @{
            Diagnosis = "DNS resolution failed"
            Suggestion = "Check your DNS settings"
        }
        "ENOENT" = @{
            Diagnosis = "File or directory not found"
            Suggestion = "Try cleaning residual files and reinstalling"
        }
    }

    $matched = $false
    foreach ($pattern in $errorPatterns.Keys) {
        if ($Stderr -match $pattern) {
            $matched = $true
            $info = $errorPatterns[$pattern]
            Write-Host "  Error: $($info.Diagnosis)" -ForegroundColor White
            Write-Host "" -ForegroundColor White
            Write-Host "  Suggestion:" -ForegroundColor White
            Write-Host "    $($info.Suggestion)" -ForegroundColor Gray
            break
        }
    }

    if (-not $matched) {
        # Show stderr output (limited)
        if ($Stderr) {
            Write-Host "  npm stderr:" -ForegroundColor White
            $stderrLines = $Stderr -split "`n" | Select-Object -First 10
            foreach ($line in $stderrLines) {
                Write-Host "    $line" -ForegroundColor Gray
            }
            if (($Stderr -split "`n").Count -gt 10) {
                Write-Host "    ... (truncated)" -ForegroundColor Gray
            }
        }
    }

    # Check npm registry
    $registry = Get-NpmRegistry
    if ($registry -match "registry.npmjs.org") {
        Write-Host "" -ForegroundColor White
        Write-Host "  Your npm registry is set to the official source." -ForegroundColor White
        Write-Host "  If you're in China, try using a mirror:" -ForegroundColor White
        Write-Host "    npm config set registry https://registry.npmmirror.com" -ForegroundColor Gray
    }
}

# Install code-server if not present
$codeServerAvailable = $false

if ($SkipCodeServer) {
    Write-Host "[INFO] Skipping code-server installation (-SkipCodeServer)" -ForegroundColor Cyan
} else {
    # Step 1: Check if already installed
    $csCmd = Get-Command code-server -ErrorAction SilentlyContinue
    if ($csCmd) {
        # Verify it's actually runnable
        Write-Host "[INFO] code-server command found, verifying installation..." -ForegroundColor Cyan
        $execTest = Test-CodeServerExecution
        if ($execTest.Success) {
            Write-Host "[OK] code-server already installed and working: $($execTest.Version)" -ForegroundColor Green
            $codeServerAvailable = $true
        } else {
            Write-Host "[WARN] code-server command exists but is not working: $($execTest.Error)" -ForegroundColor Yellow
            Write-Host "[INFO] Will attempt to reinstall..." -ForegroundColor Cyan
        }
    }

    if (-not $codeServerAvailable) {
        # Step 2: Get npm global prefix
        Write-Host "[INFO] Checking npm configuration..." -ForegroundColor Cyan
        $npmCmd = Get-Command npm -ErrorAction SilentlyContinue
        if (-not $npmCmd) {
            Write-Host "[WARN] npm not found. Skipping code-server installation." -ForegroundColor Yellow
            Write-Host "       To install code-server:" -ForegroundColor Yellow
            Write-Host "         1. Install Node.js LTS from https://nodejs.org/" -ForegroundColor Yellow
            Write-Host "         2. Run: npm install -g code-server" -ForegroundColor Yellow
        } else {
            $npmPrefix = Get-NpmGlobalPrefix

            # Step 3: Permission check
            Write-Host "[INFO] npm global directory: $npmPrefix" -ForegroundColor Cyan
            $writable = Test-NpmGlobalWritable -Prefix $npmPrefix
            if (-not $writable) {
                Write-Host "" -ForegroundColor Yellow
                Write-Host "[WARN] No write permission to npm global directory: $npmPrefix" -ForegroundColor Yellow
                Write-Host "       code-server installation requires write access to this directory." -ForegroundColor Yellow
                Write-Host "" -ForegroundColor White
                Write-Host "       Options:" -ForegroundColor White
                Write-Host "         1. Run PowerShell as Administrator" -ForegroundColor Gray
                Write-Host "         2. Configure npm to use user directory:" -ForegroundColor Gray
                Write-Host "            npm config set prefix "`$env:USERPROFILE\npm-global"" -ForegroundColor Gray
                Write-Host "" -ForegroundColor White
                Write-Host "       Skipping code-server installation." -ForegroundColor Yellow
            } else {
                # Step 4: Node.js version check
                Write-Host "[INFO] Checking Node.js version..." -ForegroundColor Cyan
                $nodeCheck = Test-NodeVersionCompatibility

                if (-not $nodeCheck.Compatible) {
                    Write-Host "" -ForegroundColor Yellow
                    Write-Host "[WARN] $($nodeCheck.Warning)" -ForegroundColor Yellow
                    Write-Host "       Current Node.js version: $($nodeCheck.Version)" -ForegroundColor Yellow
                    Write-Host "" -ForegroundColor White
                    Write-Host "       To install code-server:" -ForegroundColor White
                    Write-Host "         1. Install Node.js LTS (v20 or v22) from https://nodejs.org/" -ForegroundColor Gray
                    Write-Host "         2. Run the installer again or: npm install -g code-server" -ForegroundColor Gray
                    Write-Host "" -ForegroundColor White
                    Write-Host "       Skipping code-server installation." -ForegroundColor Yellow
                } else {
                    if ($nodeCheck.Warning) {
                        Write-Host "[WARN] $($nodeCheck.Warning)" -ForegroundColor Yellow
                    } else {
                        Write-Host "[OK] Node.js version compatible: $($nodeCheck.Version)" -ForegroundColor Green
                    }

                    # Step 5: Clean up residual files
                    Write-Host "[INFO] Cleaning up previous installation files..." -ForegroundColor Cyan
                    Clear-CodeServerResidue -NpmPrefix $npmPrefix

                    # Step 6: Install code-server
                    Write-Host "[INFO] Installing code-server..." -ForegroundColor Cyan
                    Write-Host "[INFO] This may take a few minutes, please wait..." -ForegroundColor Cyan

                    $prevErrorAction = $ErrorActionPreference
                    $ErrorActionPreference = "Continue"

                    $installResult = Invoke-NpmInstall -Package "code-server" -TimeoutSeconds 300

                    $ErrorActionPreference = $prevErrorAction

                    if ($installResult.Success) {
                        # Step 7: Verify installation
                        Write-Host "[INFO] Verifying code-server installation..." -ForegroundColor Cyan
                        $execTest = Test-CodeServerExecution

                        if ($execTest.Success) {
                            Write-Host "[OK] code-server installed successfully: $($execTest.Version)" -ForegroundColor Green
                            $codeServerAvailable = $true
                        } else {
                            Write-Host "[WARN] code-server installed but not executable" -ForegroundColor Yellow
                            Write-Host "       Error: $($execTest.Error)" -ForegroundColor Yellow
                            Write-Host "" -ForegroundColor White
                            Write-Host "       code-server will be marked as unavailable." -ForegroundColor Yellow
                            Write-Host "       You may need to upgrade Node.js or reinstall code-server manually." -ForegroundColor Yellow
                        }
                    } else {
                        # Step 8: Show diagnosis
                        Show-InstallationFailureDiagnosis `
                            -ExitCode $installResult.ExitCode `
                            -Stderr $installResult.Stderr `
                            -Stdout $installResult.Stdout `
                            -Timeout $installResult.Timeout

                        Write-Host "" -ForegroundColor White
                        Write-Host "[WARN] Failed to install code-server. Remote workspace will be missing VSCode editor." -ForegroundColor Yellow
                        Write-Host "       You can install it manually later: https://coder.com/docs/code-server/latest/install" -ForegroundColor Yellow
                    }

                    # Clean up temp files
                    if ($installResult.StdoutFile) { Remove-Item $installResult.StdoutFile -ErrorAction SilentlyContinue }
                    if ($installResult.StderrFile) { Remove-Item $installResult.StderrFile -ErrorAction SilentlyContinue }
                }
            }
        }
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
    skip_ssl_verify = [bool]$InsecureSkipTlsVerify
    allow_insecure_tls = [bool]$InsecureSkipTlsVerify
    ca_bundle_path = $null
    code_server_available = $codeServerAvailable
}
if ($CaBundlePath) { $config.ca_bundle_path = $CaBundlePath }

$config | ConvertTo-Json | Set-Content -Path "$InstallDir\config.json"
Write-Host "[OK] Configuration saved" -ForegroundColor Green

# Step 7: Register with server
Write-Host "[INFO] Registering with Open ACE server..." -ForegroundColor Cyan

$osType = "Windows"
$osVersion = [System.Environment]::OSVersion.Version.ToString()
$hostname = $env:COMPUTERNAME

# Get local IP address (prefer non-loopback)
$localIp = "127.0.0.1"
try {
    $ipAddresses = [System.Net.Dns]::GetHostAddresses($hostname) | Where-Object { $_.AddressFamily -eq "InterNetwork" -and $_.ToString() -ne "127.0.0.1" }
    if ($ipAddresses.Count -gt 0) {
        $localIp = $ipAddresses[0].ToString()
    }
} catch {
    # Fallback: use loopback
}

$capabilities = @{
    os = "windows"
    os_version = $osVersion
    cpu_cores = [System.Environment]::ProcessorCount
}

foreach ($cli in @("qwen", "claude", "openclaw")) {
    $cmd = Get-Command $cli -ErrorAction SilentlyContinue
    $capabilities["${cli}_installed"] = ($null -ne $cmd)
}

# Check git and code-server (use tracked variable for code-server availability)
$capabilities["has_git"] = ($null -ne (Get-Command git -ErrorAction SilentlyContinue))
$capabilities["has_code_server"] = $codeServerAvailable

$body = @{
    registration_token = $RegistrationToken
    machine_id = $machineId
    machine_name = $MachineName
    hostname = $hostname
    os_type = $osType
    os_version = $osVersion
    capabilities = $capabilities
    agent_version = "1.0.0"
    ip_address = $localIp
} | ConvertTo-Json

try {
    $bodyFile = "$env:TEMP\agent_register_$([System.Guid]::NewGuid()).json"
    $body | Out-File -FilePath $bodyFile -Encoding utf8 -NoNewline

    $responseFile = "$env:TEMP\agent_response_$([System.Guid]::NewGuid()).json"
    & $curlPath -s @serverCurlTlsArgs -X POST -H "Content-Type: application/json" -d "@$bodyFile" -o $responseFile "$ServerUrl/api/remote/agent/register"

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

        # Extract agent_token from registration response and save to config
        if ($response.machine -and $response.machine.agent_token) {
            try {
                $config.agent_token = $response.machine.agent_token
                $config | ConvertTo-Json | Set-Content -Path "$InstallDir\config.json"
                Write-Host "[OK] Agent token saved to configuration" -ForegroundColor Green
            } catch {
                Write-Host "[WARNING] Failed to save agent_token: $_" -ForegroundColor Yellow
            }
        } else {
            Write-Host "[INFO] No agent_token in response (server may not support token auth yet)" -ForegroundColor Cyan
        }
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
    $agentArguments = "`"$InstallDir\agent.py`""
    if ($InsecureSkipTlsVerify) { $agentArguments += " --insecure-skip-tls-verify" }
    $action = New-ScheduledTaskAction -Execute "python" -Argument $agentArguments -WorkingDirectory $InstallDir
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
$agentStartArgs = @("`"$InstallDir\agent.py`"")
if ($InsecureSkipTlsVerify) { $agentStartArgs += "--insecure-skip-tls-verify" }
$agentProc = Start-Process -FilePath "python" -ArgumentList $agentStartArgs -WorkingDirectory $InstallDir -WindowStyle Hidden -PassThru
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
