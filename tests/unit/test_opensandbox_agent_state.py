"""Moving the CLI transcript in and out of an ephemeral sandbox (#3237).

Asserted at the WIRE level against ``FakeOpenSandboxApi`` — the path and
filename the provider really uses — because a hand-written fake that encoded
the assumption rather than upstream's behaviour is exactly what hid the #2023
defects until a real server was run.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import FakeOpenSandboxApi
from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
    _AGENT_STATE_DIR,
    OpenSandboxProvider,
)
from app.modules.workspace.autonomous.sandbox.provider import (
    AGENT_STATE_CARRIED,
    agent_state_persistence,
)
from tests.unit.test_opensandbox_provider import _cfg, _spec

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]

_SID = "c53d4b8d-872a-495d-8f1f-e72ab563999a"


@pytest.fixture()
def sandbox(monkeypatch):
    """A provider wired to the in-memory API, with one live sandbox handle."""
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    api = FakeOpenSandboxApi()
    provider = OpenSandboxProvider(_cfg(), api_factory=lambda endpoint: api)
    handle = provider.create(_spec())
    return provider, api, handle


def test_opensandbox_declares_carried():
    assert agent_state_persistence(OpenSandboxProvider) == AGENT_STATE_CARRIED


def test_the_transcript_dir_matches_what_the_cli_really_writes():
    """The CLI encodes its cwd; inside the sandbox that cwd is always /workspace.

    Verified against a real CLI (2.1.170): turn 1 wrote
    ``$HOME/.claude/projects/<encoded>/<id>.jsonl`` and
    ``_encode_project_path`` predicted ``<encoded>`` byte-for-byte. Pinning
    both halves here stops the two drifting apart.
    """
    assert AutonomousAgentRunner._encode_project_path("/workspace") == "-workspace"
    assert _AGENT_STATE_DIR == "/home/agent/.claude/projects/-workspace"


def test_export_reads_exactly_the_transcript_path(sandbox):
    provider, api, handle = sandbox
    api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] = b"LINE\n"

    assert provider.export_agent_state(handle, cli_session_id=_SID) == b"LINE\n"


def test_export_returns_none_when_the_turn_wrote_no_transcript(sandbox):
    """Not an error: a turn that never started a session has nothing to carry."""
    provider, _api, handle = sandbox
    assert provider.export_agent_state(handle, cli_session_id=_SID) is None


def test_export_without_a_session_id_makes_no_api_call(sandbox):
    """A turn whose stream never yielded a session id has nothing to ask for."""
    provider, api, handle = sandbox
    before = len(api.uploaded[handle.sandbox_id])

    assert provider.export_agent_state(handle, cli_session_id="") is None
    assert len(api.uploaded[handle.sandbox_id]) == before


def test_import_writes_to_the_path_the_cli_will_read(sandbox):
    provider, api, handle = sandbox
    provider.import_agent_state(handle, cli_session_id=_SID, blob=b"LINE\n")

    assert api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] == b"LINE\n"


def test_a_round_trip_preserves_the_bytes(sandbox):
    """What the next turn reads must be exactly what the last one wrote."""
    provider, _api, handle = sandbox
    blob = b'{"type":"user","message":{"role":"user","content":"hi"}}\n'
    provider.import_agent_state(handle, cli_session_id=_SID, blob=blob)

    assert provider.export_agent_state(handle, cli_session_id=_SID) == blob


def test_no_credential_file_is_ever_carried(sandbox):
    """Only the transcript moves.

    Credentials must never round-trip through the control plane: the sandbox
    environment is constructed, never inherited, and `build_env` already mints
    the proxy token fresh each turn. A real CLI confirmed this one file is
    sufficient for --resume to resolve.
    """
    provider, api, handle = sandbox
    provider.import_agent_state(handle, cli_session_id=_SID, blob=b"LINE\n")

    written = set(api.uploaded[handle.sandbox_id])
    assert written == {f"{_AGENT_STATE_DIR}/{_SID}.jsonl"}
    assert not any(".credentials" in p or ".claude.json" in p for p in written)
