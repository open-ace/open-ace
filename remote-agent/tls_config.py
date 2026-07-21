"""
Open ACE Remote Agent - TLS Configuration Management

Unified TLS configuration for main process and subprocesses.
Provides validation, security checks, and subprocess environment propagation.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import ssl
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

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
    def from_config(
        cls,
        config: Any,
        explicit_insecure: bool = False,
        ca_bundle_path: str | None = None,
    ) -> TLSConfig:
        """
        Create TLSConfig from AgentConfig.

        Args:
            config: AgentConfig instance
            explicit_insecure: Whether --insecure-skip-tls-verify CLI flag was used
            ca_bundle_path: Optional CLI override for the custom CA bundle

        Returns:
            TLSConfig instance
        """
        # Determine configuration source
        if explicit_insecure or ca_bundle_path is not None:
            source = "cli_param"
        elif os.environ.get("OPENACE_SKIP_SSL_VERIFY"):
            source = "env_var"
        elif hasattr(config, "_data") and "skip_ssl_verify" in config._data:
            source = "config_file"
        else:
            source = "default"

        effective_ca_bundle = (
            ca_bundle_path if ca_bundle_path is not None else config.ca_bundle_path
        )
        if explicit_insecure:
            effective_ca_bundle = None

        return cls(
            # The dangerous CLI switch is itself the explicit request to
            # disable verification.  A config-file value still requires the
            # switch for non-local HTTPS endpoints.
            skip_verify=explicit_insecure or (config.skip_ssl_verify and ca_bundle_path is None),
            ca_bundle_path=effective_ca_bundle,
            is_explicit_insecure=explicit_insecure,
            config_source=source,
            server_url=config.server_url,
        )

    @classmethod
    def from_env(cls) -> TLSConfig:
        """
        Create TLSConfig from environment variables (for subprocess use).

        Returns:
            TLSConfig instance
        """
        skip_verify = os.environ.get(ENV_TLS_SKIP_VERIFY, "false").lower() in ("true", "1", "yes")
        ca_bundle = os.environ.get(ENV_TLS_CA_BUNDLE)
        explicit_insecure = os.environ.get(ENV_TLS_EXPLICIT_INSECURE, "false").lower() in (
            "true",
            "1",
            "yes",
        )

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

        # Treat every non-local HTTPS endpoint as production-like.  Private
        # model/control-plane addresses need the same explicit acknowledgement
        # as public hosts; RFC1918 is not a safe proxy for "development".
        try:
            hostname = (urlsplit(self.server_url).hostname or "").lower()
            if hostname in {"localhost", "0.0.0.0"}:
                return False
            try:
                return not ipaddress.ip_address(hostname).is_loopback
            except ValueError:
                pass
            return bool(hostname)
        except Exception:
            # Fail closed for malformed non-local HTTPS URLs.
            return True

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
        return self.skip_verify and not self.is_explicit_insecure

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
                    warnings.append(
                        f"CA bundle file does not appear to be PEM format: {self.ca_bundle_path}"
                    )
                    self._ca_bundle_valid = False
                    return warnings

                # Let OpenSSL parse the bundle now so a truncated or malformed
                # PEM cannot pass startup and fail only on the first request.
                context = ssl.create_default_context()
                context.load_verify_locations(cafile=self.ca_bundle_path)

                # Calculate hash for change detection
                self._ca_bundle_hash = hashlib.sha256(ca_path.read_bytes()).hexdigest()
                self._ca_bundle_valid = True
            except Exception as e:
                warnings.append(f"Failed to validate CA bundle: {e}")
                self._ca_bundle_valid = False

        # Production mode security check
        if self.is_production_mode() and self.skip_verify:
            if not self.is_explicit_insecure:
                warnings.append(
                    "TLS verification disabled in production mode without explicit CLI parameter"
                )
            else:
                warnings.append(
                    "TLS verification disabled in production mode (explicit CLI parameter)"
                )

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

    @property
    def ca_bundle_valid(self) -> bool | None:
        """Return the most recent custom CA validation result."""
        return self._ca_bundle_valid

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
        """Return string representation of TLSConfig."""
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
