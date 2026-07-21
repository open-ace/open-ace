"""Acceptance coverage for secure remote-agent TLS defaults (issue #1892)."""

from __future__ import annotations

import importlib.util
import ipaddress
import json
import ssl
import sys
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

REPO_ROOT = Path(__file__).resolve().parents[3]
AGENT_DIR = REPO_ROOT / "remote-agent"
if str(AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(AGENT_DIR))

from tls_config import TLSConfig

from config import DEFAULTS, AgentConfig


def _load_agent_module(filename: str, name: str):
    spec = importlib.util.spec_from_file_location(name, AGENT_DIR / filename)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _config(tmp_path: Path, **values) -> AgentConfig:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    return AgentConfig(config_path=str(path))


def test_new_install_default_verifies_tls(tmp_path):
    """Built-in and file-backed defaults must keep certificate checks on."""
    assert DEFAULTS["skip_ssl_verify"] is False
    tls = _config(tmp_path, server_url="https://ace.example").get_tls_config()
    assert tls.skip_verify is False
    assert tls.get_verify_context() is True


@pytest.mark.parametrize(
    ("url", "production_like"),
    [
        ("https://ace.example", True),
        ("https://192.168.31.159", True),
        ("https://10.0.0.8", True),
        ("https://localhost:19888", False),
        ("https://127.0.0.1:19888", False),
        ("https://[::1]:19888", False),
    ],
)
def test_non_local_https_requires_explicit_acknowledgement(url, production_like):
    """Private network addresses are not exempt from the production guard."""
    tls = TLSConfig(skip_verify=True, server_url=url)
    assert tls.is_production_mode() is production_like
    assert tls.should_reject_startup() is production_like


def test_explicit_insecure_flag_really_disables_verification(tmp_path):
    """The dangerous switch changes behavior as well as audit metadata."""
    config = _config(tmp_path, server_url="https://192.168.31.159")
    tls = config.get_tls_config(explicit_insecure=True)
    assert tls.skip_verify is True
    assert tls.is_explicit_insecure is True
    assert tls.should_reject_startup() is False
    assert tls.get_verify_context() is False


def test_ca_bundle_cli_override_reenables_verification(monkeypatch, tmp_path):
    """A CLI CA override wins over a legacy insecure config value."""
    ca_file = tmp_path / "private-ca.pem"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n")
    fake_context = type("FakeContext", (), {"load_verify_locations": lambda self, **_: None})
    monkeypatch.setattr(ssl, "create_default_context", fake_context)
    config = _config(
        tmp_path,
        server_url="https://ace.internal",
        skip_ssl_verify=True,
    )
    tls = config.get_tls_config(ca_bundle_path=str(ca_file))
    assert tls.skip_verify is False
    assert tls.ca_bundle_path == str(ca_file)
    assert tls.validate() == []
    assert tls.ca_bundle_valid is True


def test_missing_ca_bundle_fails_validation(tmp_path):
    """A missing custom CA must not silently fall back to system trust."""
    missing = tmp_path / "missing.pem"
    tls = TLSConfig(ca_bundle_path=str(missing), server_url="https://ace.example")
    assert tls.validate() == [f"CA bundle file not found: {missing}"]
    assert tls.ca_bundle_valid is False


def test_custom_ca_is_loaded_into_ssl_context(monkeypatch, tmp_path):
    """WebSocket/urllib consumers receive an SSL context using the custom CA."""
    ca_file = tmp_path / "private-ca.pem"
    ca_file.write_text("-----BEGIN CERTIFICATE-----\ntest\n-----END CERTIFICATE-----\n")
    loaded = []

    class FakeContext:
        def load_verify_locations(self, *, cafile):
            loaded.append(cafile)

    monkeypatch.setattr(ssl, "create_default_context", lambda: FakeContext())
    context = TLSConfig(ca_bundle_path=str(ca_file)).get_ssl_context()
    assert isinstance(context, FakeContext)
    assert loaded == [str(ca_file)]


def test_self_signed_https_round_trip_with_custom_ca(tmp_path):
    """A self-signed server is reachable when its certificate is the configured CA."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=1))
        .not_valid_after(now + timedelta(hours=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cert_path = tmp_path / "server-and-ca.pem"
    key_path = tmp_path / "server.key"
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"secure")

        def log_message(self, *_args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    server.socket = server_context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        tls = TLSConfig(ca_bundle_path=str(cert_path))
        assert tls.validate() == []
        url = f"https://127.0.0.1:{server.server_port}/"
        with urllib.request.urlopen(url, context=tls.get_ssl_context(), timeout=5) as response:
            assert response.read() == b"secure"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_agent_daemon_exposes_mutually_exclusive_tls_flags():
    """The recovery command printed by the daemon must be executable."""
    agent = _load_agent_module("agent.py", "remote_agent_issue_1892")
    insecure = agent.build_arg_parser().parse_args(["--insecure-skip-tls-verify"])
    custom_ca = agent.build_arg_parser().parse_args(["--ca-bundle", "/tmp/ca.pem"])
    assert insecure.insecure_skip_tls_verify is True
    assert custom_ca.ca_bundle == "/tmp/ca.pem"
    with pytest.raises(SystemExit):
        agent.build_arg_parser().parse_args(
            ["--ca-bundle", "/tmp/ca.pem", "--insecure-skip-tls-verify"]
        )


def test_openace_network_commands_expose_tls_flags():
    """Login, menu, and shell can use private CAs or explicit insecure mode."""
    cli = _load_agent_module("openace_cli.py", "openace_cli_issue_1892")
    for command in ("login", "menu", "shell"):
        args = cli.build_parser().parse_args([command, "--ca-bundle", "/tmp/ca.pem"])
        assert args.ca_bundle == "/tmp/ca.pem"
        args = cli.build_parser().parse_args([command, "--insecure-skip-tls-verify"])
        assert args.insecure_skip_tls_verify is True


def test_openace_urllib_path_uses_tls_context(monkeypatch):
    """Both CLI urllib request chains share the validated TLS context helper."""
    cli = _load_agent_module("openace_cli.py", "openace_cli_urlopen_issue_1892")
    sentinel_context = object()
    captured = {}

    class FakeTLS:
        def get_ssl_context(self):
            return sentinel_context

    def fake_urlopen(request, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(cli, "_effective_tls_config", lambda: FakeTLS())
    monkeypatch.setattr(cli.urllib.request, "urlopen", fake_urlopen)
    request = cli.urllib.request.Request("https://ace.example/api/test")
    cli._urlopen(request)
    assert captured == {"timeout": 30, "context": sentinel_context}


def test_installers_persist_and_use_tls_options():
    """Install downloads, registration, config, and services share TLS policy."""
    shell = (AGENT_DIR / "install.sh").read_text(encoding="utf-8")
    powershell = (AGENT_DIR / "install.ps1").read_text(encoding="utf-8")

    assert "--ca-bundle" in shell
    assert "SERVER_CURL_TLS_ARGS" in shell
    assert '"skip_ssl_verify": ${INSECURE_SKIP_TLS_VERIFY}' in shell
    assert "agent.py${AGENT_INSECURE_ARG}" in shell

    assert "[string]$CaBundlePath" in powershell
    assert "$serverCurlTlsArgs" in powershell
    assert "skip_ssl_verify = [bool]$InsecureSkipTlsVerify" in powershell
    assert 'agentArguments += " --insecure-skip-tls-verify"' in powershell


def test_remote_agent_docs_describe_secure_default_and_migration():
    """English and Chinese guides must not retain the legacy insecure default."""
    for relative_path in ("docs/en/REMOTE-AGENT.md", "docs/cn/REMOTE-AGENT.md"):
        content = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "| skip_ssl_verify | false |" in content
        assert "ca_bundle_path" in content
        assert "--insecure-skip-tls-verify" in content
        assert "--ca-bundle" in content
