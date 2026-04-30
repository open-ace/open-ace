#!/usr/bin/env python3
"""
Open ACE Remote Agent - System Capability Detection

Gathers hardware info, OS details, and checks for installed CLI tools.
"""

import logging
import os
import platform
import shutil
import subprocess
from typing import Any, Dict

logger = logging.getLogger(__name__)

# CLI tools the agent can manage
KNOWN_CLI_TOOLS = [
    "qwen-code-cli",
    "claude-code",
    "openclaw",
    "aider",
    "cursor-cli",
]


def get_system_info() -> Dict[str, Any]:
    """
    Gather system hardware and OS information.

    Returns:
        Dict with os_type, os_version, hostname, cpu_cores, memory_mb,
        disk_free_gb, python_version.
    """
    info: Dict[str, Any] = {
        "os_type": platform.system(),
        "os_version": platform.release(),
        "os_platform": platform.platform(),
        "hostname": platform.node(),
        "architecture": platform.machine(),
        "python_version": platform.python_version(),
    }

    # CPU cores
    try:
        # os.cpu_count() returns logical cores
        info["cpu_cores"] = os.cpu_count() or 0
    except Exception:
        info["cpu_cores"] = 0

    # Memory (MB)
    info["memory_mb"] = _get_memory_mb()

    # Disk space (GB) for the root / home partition
    info["disk_free_gb"] = _get_disk_free_gb()

    return info


def _get_memory_mb() -> float:
    """Get total physical memory in megabytes."""
    try:
        import resource

        # On macOS, resource.getrusage returns ru_maxrss in bytes
        # On Linux it returns ru_maxrss in kilobytes
        # But we want total system memory, not process usage
        pass
    except ImportError:
        pass

    sysname = platform.system().lower()

    if sysname == "linux":
        return _get_memory_mb_linux()
    elif sysname == "darwin":
        return _get_memory_mb_macos()
    elif sysname == "windows":
        return _get_memory_mb_windows()
    else:
        return 0.0


def _get_memory_mb_linux() -> float:
    """Read total memory from /proc/meminfo."""
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    parts = line.split()
                    if len(parts) >= 2:
                        return int(parts[1]) / 1024.0
    except (OSError, ValueError):
        pass
    return 0.0


def _get_memory_mb_macos() -> float:
    """Get total memory on macOS using sysctl."""
    try:
        result = subprocess.run(
            ["sysctl", "-n", "hw.memsize"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            return int(result.stdout.strip()) / (1024 * 1024)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    return 0.0


def _get_memory_mb_windows() -> float:
    """Get total memory on Windows."""
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        c_ulonglong = ctypes.c_ulonglong

        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", c_ulonglong),
                ("ullAvailPhys", c_ulonglong),
                ("ullTotalPageFile", c_ulonglong),
                ("ullAvailPageFile", c_ulonglong),
                ("ullTotalVirtual", c_ulonglong),
                ("ullAvailVirtual", c_ulonglong),
                ("ullAvailExtendedVirtual", c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(stat)
        kernel32.GlobalMemoryStatusEx(ctypes.byref(stat))
        return stat.ullTotalPhys / (1024 * 1024)
    except Exception:
        return 0.0


def _get_disk_free_gb() -> float:
    """Get free disk space in gigabytes for the home directory."""
    try:
        home = os.path.expanduser("~")
        usage = shutil.disk_usage(home)
        return usage.free / (1024**3)
    except OSError:
        return 0.0


def check_cli_tool(name: str) -> Dict[str, Any]:
    """
    Check whether a CLI tool is installed and accessible.

    Uses the CLI adapter to resolve the actual executable name
    (e.g. 'qwen-code-cli' → 'qwen') before checking PATH.

    Args:
        name: Tool identifier (e.g. 'qwen-code-cli').

    Returns:
        Dict with 'installed' (bool), 'path' (str or None),
        and 'version' (str or None).
    """
    result: Dict[str, Any] = {
        "installed": False,
        "path": None,
        "version": None,
    }

    # Resolve the actual executable name via adapter
    exe_name = name
    try:
        from cli_adapters import get_adapter

        adapter = get_adapter(name)
        exe_name = adapter.get_executable_name()
    except Exception:
        pass  # Fall back to the raw tool name

    tool_path = shutil.which(exe_name)
    if not tool_path:
        # Also try the raw tool name if different
        if exe_name != name:
            tool_path = shutil.which(name)
    if not tool_path:
        return result

    result["installed"] = True
    result["path"] = tool_path

    # Try to get version
    try:
        proc = subprocess.run(
            [tool_path, "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        output = (proc.stdout or proc.stderr or "").strip()
        # Take just the first line of output
        if output:
            result["version"] = output.split("\n")[0]
    except (OSError, subprocess.TimeoutExpired):
        pass

    return result


def get_installed_tools() -> Dict[str, Dict[str, Any]]:
    """
    Check all known CLI tools and return their installation status.

    Returns:
        Dict mapping tool name to installation info.
    """
    tools = {}
    for tool_name in KNOWN_CLI_TOOLS:
        tools[tool_name] = check_cli_tool(tool_name)
        status = "installed" if tools[tool_name]["installed"] else "not found"
        logger.debug("CLI tool %s: %s", tool_name, status)
    return tools


def get_capabilities() -> Dict[str, Any]:
    """
    Build the full capabilities report to send to the server during
    registration and heartbeats.

    Returns:
        Dict suitable for the 'capabilities' field in the register message.
    """
    sys_info = get_system_info()
    tools = get_installed_tools()

    installed_list = [name for name, info in tools.items() if info["installed"]]

    return {
        "os_type": sys_info["os_type"],
        "os_version": sys_info["os_version"],
        "os_platform": sys_info["os_platform"],
        "hostname": sys_info["hostname"],
        "architecture": sys_info["architecture"],
        "cpu_cores": sys_info["cpu_cores"],
        "memory_mb": round(sys_info["memory_mb"], 1),
        "disk_free_gb": round(sys_info["disk_free_gb"], 1),
        "python_version": sys_info["python_version"],
        "cli_tools": installed_list,
        "cli_details": tools,
        "max_sessions": 5,
    }
