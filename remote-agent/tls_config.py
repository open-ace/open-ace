"""
Open ACE Remote Agent - TLS Configuration Management

Unified TLS configuration for main process and subprocesses.
Provides validation, security checks, and subprocess environment propagation.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import socket
import ssl
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger("openace-agent.tls-config")

# Environment variable names for subprocess propagation
ENV_TLS_SKIP_VERIFY = "OPEN_ACE_TLS_SKIP_VERIFY"
ENV_TLS_CA_BUNDLE = "OPEN_ACE_TLS_CA_BUNDLE"
ENV_TLS_EXPLICIT_INSECURE = "OPEN_ACE_TLS_EXPLICIT_INSECURE"

# System default CA bundle paths (common locations)
SYSTEM_CA_PATHS = [
    "/etc/ssl/certs/ca-certificates.crt",  # Debian/Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",  # RHEL/CentOS
    "/etc/ssl/ca-bundle.pem",  # OpenSUSE
    "/etc/pki/ca-trust/extracted/pem/tls-ca-bundle.pem",  # Fedora
    "/etc/ssl/cert.pem",  # Alpine
    "/usr/local/share/certs/ca-root-nss.crt",  # FreeBSD
]


class TLSConfig:
    """
    Unified TLS configuration for remote agent.

    Handles configuration priority, validation, and subprocess propagation.
    """

    def __init__(
        self,
        skip_verify: bool = False,
        ca_bundle_path: str | None = None,
        is_explicit_insecure: bool = False,
        config_source: str = "default",
        server_url: str = "",
    ):
        """
        Initialize TLS configuration.

        Args:
            skip_verify: Whether to skip TLS certificate verification
            ca_bundle_path: Path to custom CA bundle file
            is_explicit_insecure: Whether skip_verify was set via explicit CLI param
            config_source: Source of configuration (default/env_var/config_file/cli_param)
            server_url: Server URL for production mode detection
        """
        self.skip_verify = skip_verify
        self.ca_bundle_path = ca_bundle_path
        self.is_explicit_insecure = is_explicit_insecure
        self.config_source = config_source
        self.server_url = server_url
        self._ca_bundle_hash: str | None = None
        self._ca_bundle_valid: bool | None = None

    @classmethod
    def from_config(cls, config: Any, explicit_insecure: bool = False) -> "TLSConfig":
        """
        Create TLSConfig from AgentConfig.

        Args:
            config: AgentConfig instance
            explicit_insecure: Whether --insecure-skip-tls-verify CLI flag was used

        Returns:
            TLSConfig instance
        """
        # Determine configuration source
        if explicit_insecure:
            source = "cli_param"
        elif os.environ.get("OPENACE_SKIP_SSL_VERIFY"):
            source = "env_var"
        elif hasattr(config, "_data") and "skip_ssl_verify" in config._data:
            source = "config_file"
        else:
            source = "default"

        return cls(
            skip_verify=config.skip_ssl_verify,
            ca_bundle_path=config.ca_bundle_path,
            is_explicit_insecure=explicit_insecure,
            config_source=source,
            server_url=config.server_url,
        )

    @classmethod
    def from_env(cls) -> "TLSConfig":
        """
        Create TLSConfig from environment variables (for subprocess use).

        Returns:
            TLSConfig instance
        """
        skip_verify = os.environ.get(ENV_TLS_SKIP_VERIFY, "false").lower() in ("true", "1", "yes")
        ca_bundle = os.environ.get(ENV_TLS_CA_BUNDLE)
        explicit_insecure = os.environ.get(ENV_TLS_EXPLICIT_INSECURE, "false").lower() in ("true", "1", "yes")

        return cls(
            skip_verify=skip_verify,
            ca_bundle_path=ca_bundle if ca_bundle else None,
            is_explicit_insecure=explicit_insecure,
            config_source="subprocess_env",
        )

    def is_production_mode(self) -> bool:
        """
        Determine if running in production mode.

        Production mode is detected by:
        1. server_url starts with https:// and is not localhost
        2. OPENACE_ENV=production environment variable

        Returns:
            True if production mode, False otherwise
        """
        # Check environment variable
        if os.environ.get("OPENACE_ENV") == "production":
            return True

        # Check server URL
        if not self.server_url:
            return False

        # Must start with https://
        if not self.server_url.startswith("https://"):
            return False

        # Extract hostname
        try:
            # Remove protocol and path
            url = self.server_url.replace("https://", "").split("/")[0]
            hostname = url.split(":")[0]  # Remove port if present

            # Check for localhost variants
            localhost_variants = ["localhost", "127.0.0.1", "0.0.0.0", "::1"]
            if hostname.lower() in localhost_variants:
                return False

            # Check for local/private IP ranges (might be development)
            try:
                ip = socket.gethostbyname(hostname)
                # Private IP ranges: 10.x.x.x, 172.16-31.x.x, 192.168.x.x
                parts = ip.split(".")
                if len(parts) == 4:
                    first = int(parts[0])
                    second = int(parts[1])
                    # 10.0.0.0/8 - private
                    if first == 10:
                        return False
                    # 172.16.0.0/12 - private
                    if first == 172 and 16 <= second <= 31:
                        return False
                    # 192.168.0.0/16 - private
                    if first == 192 and second == 168:
                        return False
            except (socket.gaierror, ValueError):
                # Can't resolve, assume production
                pass

            return True
        except Exception:
            return False

    def should_reject_startup(self) -> bool:
        """
        Check if startup should be rejected due to insecure configuration in production.

        Returns:
            True if startup should be rejected, False otherwise
        """
        # Only reject in production mode
        if not self.is_production_mode():
            return False

        # Only reject if skip_verify is True without explicit CLI param
        if self.skip_verify and not self.is_explicit_insecure:
            return True

        return False

    def get_verify_context(self) -> str | bool:
        """
        Get verify parameter for requests library.

        Returns:
            False if skip_verify, CA bundle path if set, True otherwise
        """
        if self.skip_verify:
            return False
        if self.ca_bundle_path:
            return self.ca_bundle_path
        return True

    def get_ssl_context(self) -> ssl.SSLContext:
        """
        Get SSL context for urllib/websockets.

        Returns:
            SSLContext configured for this TLS config
        """
        if self.skip_verify:
            # Create unverified context
            context = ssl._create_unverified_context()
            return context

        # Create default context
        context = ssl.create_default_context()

        if self.ca_bundle_path:
            try:
                context.load_verify_locations(cafile=self.ca_bundle_path)
            except Exception as e:
                logger.error(f"Failed to load CA bundle from {self.ca_bundle_path}: {e}")
                raise

        return context

    def validate(self) -> list[str]:
        """
        Validate TLS configuration and return warnings.

        Returns:
            List of warning messages
        """
        warnings = []

        # Validate CA bundle if specified
        if self.ca_bundle_path:
            ca_path = Path(self.ca_bundle_path)

            # Check existence
            if not ca_path.exists():
                warnings.append(f"CA bundle file not found: {self.ca_bundle_path}")
                self._ca_bundle_valid = False
                return warnings

            # Check readability
            if not os.access(self.ca_bundle_path, os.R_OK):
                warnings.append(f"CA bundle file not readable: {self.ca_bundle_path}")
                self._ca_bundle_valid = False
                return warnings

            # Validate PEM format (basic check)
            try:
                content = ca_path.read_text(encoding="utf-8", errors="ignore")
                if "-----BEGIN CERTIFICATE-----" not in content:
                    warnings.append(f"CA bundle file does not appear to be PEM format: {self.ca_bundle_path}")
                    self._ca_bundle_valid = False
                    return warnings

                # Calculate hash for change detection
                self._ca_bundle_hash = hashlib.sha256(ca_path.read_bytes()).hexdigest()
                self._ca_bundle_valid = True
            except Exception as e:
                warnings.append(f"Failed to validate CA bundle: {e}")
                self._ca_bundle_valid = False

        # Production mode security check
        if self.is_production_mode() and self.skip_verify:
            if not self.is_explicit_insecure:
                warnings.append("TLS verification disabled in production mode without explicit CLI parameter")
            else:
                warnings.append("TLS verification disabled in production mode (explicit CLI parameter)")

        return warnings

    def check_ca_bundle_status(self) -> bool:
        """
        Check if CA bundle file is still valid (for runtime monitoring).

        Returns:
            True if CA bundle is valid or not specified, False if invalid
        """
        if not self.ca_bundle_path:
            return True

        ca_path = Path(self.ca_bundle_path)

        # Check existence
        if not ca_path.exists():
            return False

        # Check hash if we have one
        if self._ca_bundle_hash:
            try:
                current_hash = hashlib.sha256(ca_path.read_bytes()).hexdigest()
                return current_hash == self._ca_bundle_hash
            except Exception:
                return False

        return True

    def find_system_ca_bundle(self) -> str | None:
        """
        Find system default CA bundle path.

        Returns:
            Path to system CA bundle if found, None otherwise
        """
        for path in SYSTEM_CA_PATHS:
            if os.path.exists(path) and os.access(path, os.R_OK):
                return path
        return None

    def to_subprocess_env(self) -> dict[str, str]:
        """
        Export configuration as environment variables for subprocess.

        Returns:
            Dict of environment variable names to values
        """
        env = {
            ENV_TLS_SKIP_VERIFY: "true" if self.skip_verify else "false",
            ENV_TLS_EXPLICIT_INSECURE: "true" if self.is_explicit_insecure else "false",
        }

        if self.ca_bundle_path:
            env[ENV_TLS_CA_BUNDLE] = self.ca_bundle_path

        return env

    def to_audit_dict(self) -> dict[str, Any]:
        """
        Export configuration as audit log dict.

        Returns:
            Dict with TLS configuration details
        """
        return {
            "event": "tls_config",
            "skip_verify": self.skip_verify,
            "ca_bundle_path": self.ca_bundle_path,
            "is_explicit_insecure": self.is_explicit_insecure,
            "config_source": self.config_source,
            "is_production_mode": self.is_production_mode(),
            "ca_bundle_valid": self._ca_bundle_valid,
        }

    def __repr__(self) -> str:
        return (
            f"TLSConfig(skip_verify={self.skip_verify}, "
            f"ca_bundle={self.ca_bundle_path}, "
            f"explicit_insecure={self.is_explicit_insecure}, "
            f"source={self.config_source})"
        )


def print_tls_security_warning(config: TLSConfig) -> None:
    """
    Print security warning to stderr for production insecure config.

    Args:
        config: TLSConfig instance
    """
    if not config.is_production_mode() or not config.skip_verify:
        return

    # Print to stderr to ensure visibility regardless of log level
    print(
        "\n"
        "╔══════════════════════════════════════════════════════════════════╗\n"
        "║                    ⚠️  SECURITY WARNING ⚠️                        ║\n"
        "╠══════════════════════════════════════════════════════════════════╣\n"
        "║  TLS certificate verification is DISABLED in production mode.   ║\n"
        "║  This exposes your agent token, control commands, and execution ║\n"
        "║  outputs to man-in-the-middle attacks.                          ║\n"
        "╠══════════════════════════════════════════════════════════════════╣\n"
        "║  To fix this:                                                   ║\n"
        "║  1. Set 'skip_ssl_verify': false in config.json                 ║\n"
        "║  2. Or use --ca-bundle to specify your private CA certificate   ║\n"
        "║  3. Only use --insecure-skip-tls-verify for testing purposes    ║\n"
        "╚══════════════════════════════════════════════════════════════════╝\n",
        file=sys.stderr,
        flush=True,
    )


def print_tls_startup_rejection() -> None:
    """
    Print startup rejection message for production insecure config.
    """
    print(
        "\n"
        "╔══════════════════════════════════════════════════════════════════╗\n"
        "║                   🚫 STARTUP REJECTED 🚫                          ║\n"
        "╠══════════════════════════════════════════════════════════════════╣\n"
        "║  TLS verification disabled in production environment is not      ║\n"
        "║  allowed without explicit confirmation.                          ║\n"
        "║                                                                  ║\n"
        "║  This is a security requirement to prevent accidental exposure   ║\n"
        "║  of sensitive data.                                              ║\n"
        "╠══════════════════════════════════════════════════════════════════╣\n"
        "║  To start the agent in production with TLS disabled:            ║\n"
        "║                                                                  ║\n"
        "║    python agent.py --insecure-skip-tls-verify                    ║\n"
        "║                                                                  ║\n"
        "║  WARNING: Only use this if you understand the security risks.   ║\n"
        "╚══════════════════════════════════════════════════════════════════╝\n",
        file=sys.stderr,
        flush=True,
    )