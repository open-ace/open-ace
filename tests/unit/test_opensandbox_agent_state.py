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


# ── #3319: qwen carries under a different on-disk layout ──────────────

_QWEN_DIR = "/home/agent/.qwen/projects/-workspace/chats"


@pytest.mark.issue(3319)
def test_qwen_export_reads_the_chats_path(sandbox):
    """qwen's transcript lives under .qwen/.../chats/, not .claude/."""
    provider, api, handle = sandbox
    api.uploaded[handle.sandbox_id][f"{_QWEN_DIR}/{_SID}.jsonl"] = b"QWEN\n"

    assert (
        provider.export_agent_state(handle, cli_session_id=_SID, cli_tool="qwen-code-cli")
        == b"QWEN\n"
    )
    # And the claude path is NOT where qwen looks.
    assert provider.export_agent_state(handle, cli_session_id=_SID) is None


@pytest.mark.issue(3319)
def test_qwen_import_writes_to_the_chats_path(sandbox):
    provider, api, handle = sandbox
    provider.import_agent_state(
        handle, cli_session_id=_SID, blob=b"QWEN\n", cli_tool="qwen-code-cli"
    )

    assert api.uploaded[handle.sandbox_id][f"{_QWEN_DIR}/{_SID}.jsonl"] == b"QWEN\n"


@pytest.mark.issue(3319)
def test_qwen_round_trip_preserves_bytes(sandbox):
    provider, _api, handle = sandbox
    blob = b'{"sessionId":"' + _SID.encode() + b'","type":"user"}\n'
    provider.import_agent_state(handle, cli_session_id=_SID, blob=blob, cli_tool="qwen-code-cli")

    assert (
        provider.export_agent_state(handle, cli_session_id=_SID, cli_tool="qwen-code-cli") == blob
    )


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


# ── the id is sandbox-controlled, so it is not a path fragment ────────


@pytest.mark.parametrize(
    "hostile_id",
    [
        "../../../../workspace/.git/hooks/pre-commit",
        "../escape",
        "a/b",
        "/etc/passwd",
        ".",
        "..",
        "",
        "   ",
    ],
)
def test_a_hostile_session_id_never_becomes_a_path(sandbox, hostile_id):
    """`cli_session_id` comes from the SANDBOX's own stdout.

    `_extract_stream_session_id` accepts any non-empty string the sandbox
    prints, and under this backend the sandbox is the untrusted party. Without
    validation a compromised sandbox could name a traversal and have the NEXT
    turn's sandbox receive attacker-chosen bytes at that path.
    """
    from app.modules.workspace.autonomous.sandbox.provider import SandboxError

    provider, api, handle = sandbox

    with pytest.raises(SandboxError):
        provider.import_agent_state(handle, cli_session_id=hostile_id, blob=b"x")

    assert not any(
        ".." in path or "hooks" in path for path in api.uploaded[handle.sandbox_id]
    ), f"a traversal escaped the transcript directory: {sorted(api.uploaded[handle.sandbox_id])}"


@pytest.mark.parametrize("hostile_id", ["../../etc/shadow", "a/b", ""])
def test_export_refuses_a_hostile_id_rather_than_reading_it(sandbox, hostile_id):
    """The read side needs the same guard: it addresses a file too."""
    provider, _api, handle = sandbox
    assert provider.export_agent_state(handle, cli_session_id=hostile_id) is None


def test_a_normal_uuid_session_id_still_works(sandbox):
    """The guard must not be so tight that it breaks the real format."""
    provider, api, handle = sandbox
    provider.import_agent_state(handle, cli_session_id=_SID, blob=b"OK\n")
    assert api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] == b"OK\n"


def test_an_oversized_transcript_is_refused_rather_than_returned(sandbox):
    """HOME is a 1Gi emptyDir and download_file buffers the whole body.

    The store re-checks the cap, but handing a multi-hundred-MB blob onward
    would already have cost the control plane the memory. The scheduler pod
    runs with a 512Mi limit.
    """
    from app.modules.workspace.autonomous.sandbox.agent_state_store import MAX_AGENT_STATE_BYTES

    provider, api, handle = sandbox
    api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] = b"x" * (
        MAX_AGENT_STATE_BYTES + 1
    )

    assert provider.export_agent_state(handle, cli_session_id=_SID) is None


def test_a_transcript_at_the_cap_is_still_returned(sandbox):
    """Off-by-one check: the cap is a limit, not a threshold."""
    from app.modules.workspace.autonomous.sandbox.agent_state_store import MAX_AGENT_STATE_BYTES

    provider, api, handle = sandbox
    blob = b"x" * MAX_AGENT_STATE_BYTES
    api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] = blob

    assert provider.export_agent_state(handle, cli_session_id=_SID) == blob


# ── the audit trail ───────────────────────────────────────────────────
#
# Both events below were unpinned until now, and that is not a coincidence:
# `agent_state_too_large` was DELETED during an earlier round of this very
# change and no test noticed. An event nothing asserts is an event the next
# refactor removes silently, which is how the refusal it records becomes
# invisible exactly when it starts mattering.


@pytest.fixture()
def audited(monkeypatch):
    """A provider whose emitted events are observable."""
    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    api = FakeOpenSandboxApi()
    events: list[tuple[str, dict]] = []
    provider = OpenSandboxProvider(
        _cfg(),
        api_factory=lambda endpoint: api,
        event_sink=lambda name, data: events.append((name, data)),
    )
    handle = provider.create(_spec())
    return provider, api, handle, events


def test_an_oversized_transcript_is_recorded_not_just_refused(audited):
    """The size refusal is the only place the real distribution is observable.

    Returning None quietly would mean a session line stops carrying history
    and keeps doing so every turn thereafter, with nothing in the audit trail
    to say why — indistinguishable from a turn that never started a
    conversation.
    """
    from app.modules.workspace.autonomous.sandbox.agent_state_store import MAX_AGENT_STATE_BYTES

    provider, api, handle, events = audited
    api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] = b"x" * (
        MAX_AGENT_STATE_BYTES + 1
    )

    assert provider.export_agent_state(handle, cli_session_id=_SID) is None

    emitted = [data for name, data in events if name == "agent_state_too_large"]
    assert emitted, f"the size refusal left no trace: {[n for n, _ in events]}"
    assert emitted[0]["limit_bytes"] == MAX_AGENT_STATE_BYTES
    assert emitted[0]["sandbox_id"] == handle.sandbox_id
    assert str(MAX_AGENT_STATE_BYTES) in emitted[0]["detail"]


def test_a_missing_transcript_is_not_recorded_as_oversized(audited):
    """The routine case must not look like the pathological one.

    A turn that never started a conversation has no transcript to fetch. If
    that emitted the same event, the signal would fire on almost every first
    turn and be worth nothing.
    """
    provider, _api, handle, events = audited

    assert provider.export_agent_state(handle, cli_session_id=_SID) is None

    assert not [n for n, _ in events if n == "agent_state_too_large"]


def test_a_hostile_id_is_recorded_at_export(audited):
    """Export is the FIRST place a hostile id surfaces.

    The sandbox prints the id during its own turn, so the export at the end of
    that turn is what tries to address it. The import refusal fires a turn
    later and only if the id reached the database — so without this the
    security-relevant event is the one nothing records.
    """
    provider, _api, handle, events = audited

    assert provider.export_agent_state(handle, cli_session_id="../../etc/passwd") is None

    refused = [data for name, data in events if name == "agent_state_id_refused"]
    assert refused, f"a path-traversal id was refused silently: {[n for n, _ in events]}"
    assert refused[0]["phase"] == "export"
    assert refused[0]["sandbox_id"] == handle.sandbox_id


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_no_session_id_is_not_an_incident(audited, empty):
    """A turn whose stream never yielded a session id is ordinary, not hostile.

    Emitting here would drown the real refusals in noise from every turn that
    simply had nothing to carry.
    """
    provider, _api, handle, events = audited

    assert provider.export_agent_state(handle, cli_session_id=empty) is None

    assert not [n for n, _ in events if n == "agent_state_id_refused"]


def test_a_non_api_sandbox_error_is_handled_without_touching_code(audited):
    """This is what the two separate `except` clauses are FOR.

    `OpenSandboxApiError` is the ONLY `SandboxError` subclass carrying
    `.code`; `SandboxError` itself, `CapabilityUnsupported`,
    `SandboxConfigError` and `OpenSandboxError` (which carries `reason_code`)
    do not. Collapsing the handlers into a single `except SandboxError as exc`
    therefore reads `.code` off an exception that does not have one, and the
    handler itself raises `AttributeError`.

    Scope, stated honestly. Every `raise` in `client.py` is an
    `OpenSandboxApiError`, and so is every raise in `FakeOpenSandboxApi`, so
    no bare `SandboxError` reaches this block through either implementation
    that exists today. And `_run_local` wraps the export in
    `except Exception` -> log -> `discard()`, so even if one did, the turn
    would survive with the same outcome as the `return None` path.

    So this is not a data-loss bug: it is a latent crash inside an error
    handler, on a Protocol seam typed by the base class, that the deliberate
    catch-all upstream would mask into a misleading log line. Worth pinning
    because the `except SandboxError` clause exists precisely to serve
    implementations other than today's two.
    """
    from app.modules.workspace.autonomous.sandbox.provider import SandboxError

    provider, api, handle, events = audited

    def _boom(*_a, **_k):
        raise SandboxError("execd went away")

    api.download_file = _boom

    # It PROPAGATES now rather than returning None: an infrastructure fault is
    # not an absent transcript, and the runner's log-and-discard path is what
    # must see it. What is being pinned here is that the handler does not blow
    # up reading `.code` off an exception that has none — the failure the
    # caller sees must be the real one, not an AttributeError from the handler.
    with pytest.raises(SandboxError, match="execd went away"):
        provider.export_agent_state(handle, cli_session_id=_SID)
    assert not [n for n, _ in events if n == "agent_state_too_large"]


def test_an_infrastructure_error_is_not_reported_as_an_absent_transcript(audited):
    """A 500 is not "there is no history here".

    Returning None told the caller the line had nothing to carry, so it
    discarded a perfectly good previous slot and logged nothing — silent
    continuity loss, which is the exact failure #3237 exists to prevent, and
    contrary to the log-and-drop contract the export path documents.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    provider, api, handle, _events = audited

    def _boom(*_a, **_k):
        raise OpenSandboxApiError("execd exploded", status_code=500, code="INTERNAL")

    api.download_file = _boom

    with pytest.raises(OpenSandboxApiError):
        provider.export_agent_state(handle, cli_session_id=_SID)


def test_a_missing_transcript_is_still_an_ordinary_none(audited):
    """NOT_FOUND remains the one API error that means "nothing to carry"."""
    provider, _api, handle, _events = audited

    assert provider.export_agent_state(handle, cli_session_id=_SID) is None


# ── the bounded download, at the client boundary ──────────────────────


class _FakeResponse:
    """Minimal stand-in for a streaming requests.Response."""

    def __init__(self, body: bytes, *, declared: str | None = None):
        self.content = body
        self.headers = {} if declared is None else {"Content-Length": declared}
        self._body = body
        self.closed = False
        self.read_bytes = 0

    def iter_content(self, chunk_size=8192):
        for i in range(0, len(self._body), chunk_size):
            chunk = self._body[i : i + chunk_size]
            self.read_bytes += len(chunk)
            yield chunk

    def close(self):
        self.closed = True


def _client_with(response, monkeypatch):
    """Returns (api, calls) where `calls` records the kwargs of each request.

    Recording them is the point. Round 1's lesson was that tests can exercise
    a feature's logic thoroughly while never observing the mechanism that makes
    it work — and `stream=True` IS the mechanism here. Without it `requests`
    buffers the whole body before anything measures it, `iter_content` walks an
    already-resident body, and every size assertion still passes while the OOM
    protection is gone.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import HttpOpenSandboxApi

    api = HttpOpenSandboxApi.__new__(HttpOpenSandboxApi)
    calls: list[dict] = []

    def _record(self, *a, **k):
        calls.append(k)
        return response

    monkeypatch.setattr(HttpOpenSandboxApi, "_execd_request", _record, raising=False)
    return api, calls


def test_a_bounded_download_refuses_on_content_length_without_reading(monkeypatch):
    """The point of the bound: refuse BEFORE the body is resident.

    Reading first and measuring second is what could OOM-kill the scheduler,
    whose pod limit is smaller than the sandbox's 1Gi HOME.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    response = _FakeResponse(b"x" * 100, declared="100")
    api, _calls = _client_with(response, monkeypatch)

    with pytest.raises(OpenSandboxApiError, match="over the"):
        api.download_file("sb-1", "/p", max_bytes=10)

    assert response.read_bytes == 0, "the body was read despite an oversized Content-Length"


def test_a_bounded_download_still_counts_when_the_header_lies(monkeypatch):
    """The header is a shortcut; the counter is the guarantee.

    A server may send no Content-Length, or a wrong one. Trusting it alone
    would leave the cap unenforced exactly when the server misbehaves.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    response = _FakeResponse(b"x" * 500_000, declared="1")
    api, _calls = _client_with(response, monkeypatch)

    with pytest.raises(OpenSandboxApiError, match="mid-transfer"):
        api.download_file("sb-1", "/p", max_bytes=1000)


def test_a_bounded_download_with_no_length_header_still_bounds(monkeypatch):
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    response = _FakeResponse(b"x" * 500_000)
    api, _calls = _client_with(response, monkeypatch)

    with pytest.raises(OpenSandboxApiError, match="mid-transfer"):
        api.download_file("sb-1", "/p", max_bytes=1000)


def test_a_bounded_download_returns_a_body_within_the_limit(monkeypatch):
    response = _FakeResponse(b"small", declared="5")
    api, _calls = _client_with(response, monkeypatch)

    assert api.download_file("sb-1", "/p", max_bytes=1000) == b"small"


def test_an_unbounded_download_is_unchanged(monkeypatch):
    """The ChangeSet path must keep its existing behaviour.

    Its sizes are pre-declared in a manifest and bounded there, so it has no
    need of the streaming path and must not pay for it.
    """
    response = _FakeResponse(b"whatever", declared="8")
    api, _calls = _client_with(response, monkeypatch)

    assert api.download_file("sb-1", "/p") == b"whatever"
    assert response.read_bytes == 0, "the unbounded path should use .content"


def test_a_bounded_download_actually_streams(monkeypatch):
    """`stream=True` IS the bound. Without it the body is already resident.

    Every size assertion in this file passes with the flag removed, because
    the fake response yields from a body it already holds. Only observing the
    request kwargs catches it.
    """
    response = _FakeResponse(b"small", declared="5")
    api, calls = _client_with(response, monkeypatch)

    api.download_file("sb-1", "/p", max_bytes=1000)

    assert calls and calls[0].get("stream") is True, (
        f"the bounded download did not stream; requests would buffer the whole "
        f"body before the cap could apply: {calls}"
    )


def test_an_unbounded_download_does_not_stream(monkeypatch):
    """The ChangeSet path must keep using .content, unchanged."""
    response = _FakeResponse(b"whatever", declared="8")
    api, calls = _client_with(response, monkeypatch)

    api.download_file("sb-1", "/p")

    assert (
        calls and "stream" not in calls[0]
    ), f"the unbounded path changed shape; ChangeSet fetches share this method: {calls}"


def test_a_bounded_download_closes_the_response_on_success(monkeypatch):
    response = _FakeResponse(b"small", declared="5")
    api, _calls = _client_with(response, monkeypatch)

    api.download_file("sb-1", "/p", max_bytes=1000)

    assert response.closed, "a streamed response was left open"


def test_a_bounded_download_closes_the_response_when_it_refuses(monkeypatch):
    """The leak that matters: refusing mid-transfer must not strand a socket."""
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    response = _FakeResponse(b"x" * 500_000)
    api, _calls = _client_with(response, monkeypatch)

    with pytest.raises(OpenSandboxApiError):
        api.download_file("sb-1", "/p", max_bytes=1000)

    assert response.closed, "the connection leaked when the cap was exceeded"


def test_the_real_clients_boundary_is_inclusive(monkeypatch):
    """Exactly max_bytes is allowed — asserted against the CLIENT, not the fake.

    The fake has its own independent boundary check, so an at-the-cap test
    routed through it leaves the real client's edge unexercised and lets the
    two implementations disagree by one byte.
    """
    body = b"x" * 1000
    response = _FakeResponse(body, declared="1000")
    api, _calls = _client_with(response, monkeypatch)

    assert api.download_file("sb-1", "/p", max_bytes=1000) == body


def test_the_fake_and_the_client_agree_at_the_boundary():
    """Both refuse at cap+1 and both allow at cap.

    A fake that disagrees with the client is how a cap looks enforced in tests
    and is not in production.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.client import OpenSandboxApiError

    api = FakeOpenSandboxApi()
    api.sandboxes["sb-x"] = {"id": "sb-x", "status": {"state": "Running"}, "metadata": {}}
    api.uploaded["sb-x"] = {"/p": b"x" * 1000}

    assert api.download_file("sb-x", "/p", max_bytes=1000) == b"x" * 1000
    with pytest.raises(OpenSandboxApiError):
        api.download_file("sb-x", "/p", max_bytes=999)
