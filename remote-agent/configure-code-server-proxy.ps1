# Open ACE Remote Agent - code-server Proxy Configuration Script
#
# Issue #2424: VS Code extension downloads are extremely slow because code-server
# doesn't use the local HTTP proxy. Additionally, PowerShell 5.1's Set-Content
# -Encoding UTF8 produces UTF-8 BOM (EF BB BF), which breaks VS Code's JSON.parse.
#
# This script:
# 1. Auto-detects local HTTP proxy ports (7897/7890/10809/1080/2080)
# 2. Writes proxy settings to code-server's settings.json using UTF-8 without BOM
# 3. Idempotently merges with existing settings
# 4. Fixes existing settings.json with BOM by rewriting with node (if available)
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File configure-code-server-proxy.ps1
#   powershell -ExecutionPolicy Bypass -File configure-code-server-proxy.ps1 -ProxyUrl "http://127.0.0.1:7897"
#   powershell -ExecutionPolicy Bypass -File configure-code-server-proxy.ps1 -Status
#   powershell -ExecutionPolicy Bypass -File configure-code-server-proxy.ps1 -Clear
#
# The script is a no-op if no proxy is detected and no -ProxyUrl is provided.

param(
    [string]$ProxyUrl = "",
    [switch]$Status,
    [switch]$Clear
)

$ErrorActionPreference = "Continue"

function Write-Info  { Write-Host "[INFO] $args" -ForegroundColor Cyan }
function Write-Ok    { Write-Host "[OK]   $args" -ForegroundColor Green }
function Write-Warn  { Write-Host "[WARN] $args" -ForegroundColor Yellow }
function Write-ErrorMsg { Write-Host "[ERROR] $args" -ForegroundColor Red }

# ============================================================================
# 1. Locate code-server settings.json
# ============================================================================
# code-server stores user settings in:
# - Windows: $env:USERPROFILE\.local\share\code-server\User\settings.json
# - Linux/macOS: ~/.local/share/code-server/User/settings.json

$settingsDir = Join-Path $env:USERPROFILE ".local\share\code-server\User"
$settingsFile = Join-Path $settingsDir "settings.json"

if (-not (Test-Path $settingsDir)) {
    New-Item -ItemType Directory -Path $settingsDir -Force | Out-Null
}

# ============================================================================
# 2. Detect local HTTP proxy ports
# ============================================================================
$commonProxyPorts = @(7897, 7890, 10809, 1080, 2080)

function Test-ProxyPort {
    param([int]$Port)
    try {
        $tcp = New-Object System.Net.Sockets.TcpClient
        $connect = $tcp.BeginConnect("127.0.0.1", $Port, $null, $null)
        $wait = $connect.AsyncWaitHandle.WaitOne(1000)
        if ($wait -and $tcp.Connected) {
            $tcp.EndConnect($connect)
            $tcp.Close()
            return $true
        }
        $tcp.Close()
    } catch {}
    return $false
}

function Get-DetectedProxyUrl {
    foreach ($port in $commonProxyPorts) {
        if (Test-ProxyPort $port) {
            return "http://127.0.0.1:$port"
        }
    }
    return ""
}

# ============================================================================
# 3. Read existing settings.json (handle BOM)
# ============================================================================
function Read-SettingsJson {
    param([string]$Path)

    if (-not (Test-Path $Path)) {
        return @{}
    }

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $content = [System.Text.Encoding]::UTF8.GetString($bytes)

    # Check for UTF-8 BOM (EF BB BF)
    if ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF) {
        Write-Warn "settings.json has UTF-8 BOM, stripping it"
        $content = $content.Substring(1)  # Remove BOM character
    }

    try {
        $settings = $content | ConvertFrom-Json
        # Convert to hashtable for easier manipulation
        $hash = @{}
        $settings.PSObject.Properties | ForEach-Object {
            $hash[$_.Name] = $_.Value
        }
        return $hash
    } catch {
        Write-Warn "Failed to parse settings.json: $_"
        # Try to fix with node if available
        $node = Get-Command node -ErrorAction SilentlyContinue
        if ($node) {
            Write-Info "Attempting to fix settings.json with node..."
            $tempFile = "$Path.bak"
            Copy-Item $Path $tempFile -Force
            try {
                $fixed = node -e "console.log(JSON.stringify(JSON.parse(require('fs').readFileSync('$Path', 'utf8'))))"
                if ($LASTEXITCODE -eq 0 -and $fixed) {
                    $hash = $fixed | ConvertFrom-Json
                    $result = @{}
                    $hash.PSObject.Properties | ForEach-Object {
                        $result[$_.Name] = $_.Value
                    }
                    Write-Ok "Fixed settings.json with node"
                    return $result
                }
            } catch {
                Write-Warn "Node fix failed: $_"
            }
        }
        return @{}
    }
}

# ============================================================================
# 4. Write settings.json without BOM
# ============================================================================
function Write-SettingsJson {
    param(
        [string]$Path,
        [hashtable]$Settings
    )

    # Convert hashtable to JSON
    $json = $Settings | ConvertTo-Json -Depth 10

    # Write using .NET UTF-8 encoding without BOM
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($Path, $json, $utf8NoBom)
}

# ============================================================================
# 5. Status check
# ============================================================================
if ($Status) {
    if (-not (Test-Path $settingsFile)) {
        Write-Warn "settings.json not found: $settingsFile"
        Write-Host "  No proxy configured yet." -ForegroundColor Gray
    } else {
        $settings = Read-SettingsJson $settingsFile
        $proxy = $settings["http.proxy"]
        $strictSSL = $settings["http.proxyStrictSSL"]

        if ($proxy) {
            Write-Ok "Proxy configured: $proxy"
            Write-Host "  http.proxyStrictSSL: $strictSSL" -ForegroundColor Gray
        } else {
            Write-Warn "No proxy configured in settings.json"
            Write-Host "  To configure: run this script without -Status" -ForegroundColor Gray
        }
    }

    # Show detected proxy
    $detected = Get-DetectedProxyUrl
    if ($detected) {
        Write-Ok "Local proxy detected: $detected"
    } else {
        Write-Host "No local proxy detected on ports: $($commonProxyPorts -join ', ')" -ForegroundColor Gray
    }
    exit 0
}

# ============================================================================
# 6. Clear proxy settings
# ============================================================================
if ($Clear) {
    if (-not (Test-Path $settingsFile)) {
        Write-Warn "settings.json not found, nothing to clear"
        exit 0
    }

    $settings = Read-SettingsJson $settingsFile
    if ($settings.ContainsKey("http.proxy")) {
        $settings.Remove("http.proxy")
        Write-Info "Removing http.proxy"
    }
    if ($settings.ContainsKey("http.proxyStrictSSL")) {
        $settings.Remove("http.proxyStrictSSL")
        Write-Info "Removing http.proxyStrictSSL"
    }

    Write-SettingsJson $settingsFile $settings
    Write-Ok "Proxy settings cleared from $settingsFile"
    exit 0
}

# ============================================================================
# 7. Determine proxy URL
# ============================================================================
if ($ProxyUrl) {
    # Use explicit URL
    $targetProxy = $ProxyUrl
    Write-Info "Using specified proxy: $targetProxy"
} else {
    # Auto-detect
    $targetProxy = Get-DetectedProxyUrl
    if (-not $targetProxy) {
        Write-Warn "No local proxy detected on ports: $($commonProxyPorts -join ', ')"
        Write-Host "  If you have a proxy, specify it with -ProxyUrl" -ForegroundColor Gray
        Write-Host "  Example: powershell -ExecutionPolicy Bypass -File configure-code-server-proxy.ps1 -ProxyUrl http://127.0.0.1:7897" -ForegroundColor Gray
        exit 0
    }
    Write-Info "Auto-detected proxy: $targetProxy"
}

# ============================================================================
# 8. Apply settings
# ============================================================================
$settings = Read-SettingsJson $settingsFile

$existingProxy = $settings["http.proxy"]
if ($existingProxy -eq $targetProxy) {
    Write-Ok "Proxy already configured: $targetProxy"
    exit 0
}

$settings["http.proxy"] = $targetProxy
$settings["http.proxyStrictSSL"] = $false

Write-SettingsJson $settingsFile $settings

Write-Ok "Proxy configured: $targetProxy"
Write-Host "  settings.json: $settingsFile" -ForegroundColor Gray
Write-Host "  http.proxyStrictSSL: false" -ForegroundColor Gray
Write-Host ""
Write-Host "VS Code extension downloads will now use the proxy." -ForegroundColor White
Write-Host "Restart VS Code / code-server for changes to take effect." -ForegroundColor White
