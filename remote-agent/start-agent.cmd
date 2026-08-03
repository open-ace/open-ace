@echo off
rem ============================================================
rem  Open ACE Remote Agent - Windows launcher
rem  Double-click to start the agent and connect to the server.
rem  Usage:
rem    start-agent.cmd                       start agent
rem    start-agent.cmd -Status               show agent status
rem    start-agent.cmd -Stop                 stop agent
rem    start-agent.cmd -InstallAutoStart     auto-start at logon
rem ============================================================
chcp 65001 >nul

set "PS1=%~dp0start-agent.ps1"

if not exist "%PS1%" (
    echo [ERROR] start-agent.ps1 not found: %PS1%
    echo Please run the installer first.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%PS1%" %*

echo.
pause
