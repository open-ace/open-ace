# Agent State Across Ephemeral Sandboxes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Carry the CLI's conversation transcript between turns so `--resume` works under the OpenSandbox backend, and refuse up front when it cannot.

**Architecture:** Providers declare whether agent state survives between turns (`persists` / `carried` / `ephemeral`, defaulting to `ephemeral`). A control-plane store holds one transcript per session line. `OpenSandboxProvider` gains two duck-typed methods that move exactly one file in and out of the sandbox. `_run_local` gates `--resume` on the transcript actually being in place.

**Tech Stack:** Python 3.10+, pytest, existing `sandbox/` provider seam (#2022), `FakeOpenSandboxApi` for wire-level tests.

**Spec:** `docs/superpowers/specs/2026-08-31-agent-state-across-ephemeral-sandboxes-design.md`

**Issue:** #3237

---

## File Structure

| File | Responsibility |
| --- | --- |
| `app/modules/workspace/autonomous/sandbox/provider.py` | **Modify.** Add the three persistence constants and `agent_state_persistence(provider)` helper next to the existing capability helpers. |
| `app/modules/workspace/autonomous/sandbox/agent_state_store.py` | **Create.** `AgentStateStore` — put/get/discard/purge/reap for one transcript per line. No knowledge of providers or sandboxes. |
| `app/modules/workspace/autonomous/sandbox/legacy_posix.py` | **Modify.** Declare `persists` (one line). |
| `app/modules/workspace/autonomous/sandbox/remote_machine.py` | **Modify.** Declare `persists` (one line). |
| `app/modules/workspace/autonomous/sandbox/fake.py` | **Modify.** Declare `persists` (one line), so existing tests keep resuming. |
| `app/modules/workspace/autonomous/sandbox/opensandbox/provider.py` | **Modify.** Declare `carried`; add `export_agent_state` / `import_agent_state`. |
| `app/modules/workspace/autonomous/agent_runner.py` | **Modify.** Extract argv construction; gate `--resume`; import after upload; export before destroy; refuse up front. |
| `tests/unit/test_agent_state_store.py` | **Create.** Store unit tests. |
| `tests/unit/test_agent_state_persistence.py` | **Create.** Declaration + refusal + runner-wiring tests. |
| `tests/unit/test_opensandbox_agent_state.py` | **Create.** Provider export/import at the wire level. |
| `docs/sandbox-backends.md` | **Modify.** §6 reason code, §7 limitation rewrite. |

Every test carries `pytest.mark.regression` and `pytest.mark.issue(3237)`, per `CLAUDE.md`'s test-placement rule. All tests go in `tests/unit/` — never `tests/issues/`.

---

## Task 1: Providers declare whether agent state survives

**Files:**
- Modify: `app/modules/workspace/autonomous/sandbox/provider.py`
- Modify: `app/modules/workspace/autonomous/sandbox/legacy_posix.py`
- Modify: `app/modules/workspace/autonomous/sandbox/remote_machine.py`
- Modify: `app/modules/workspace/autonomous/sandbox/fake.py`
- Test: `tests/unit/test_agent_state_persistence.py`

**Why the default is `ephemeral`:** an absent declaration must remove the ability to resume, never grant it — the #2023 attestation rule. That means Legacy/Remote/Fake **must** declare `persists` in this same task, or the primary local path stops resuming.

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_agent_state_persistence.py`:

```python
"""Agent-state persistence declaration (#3237).

A provider that does not say its HOME survives between turns must not be
trusted to resume: `--resume` would be sent into a fresh HOME and the CLI
would answer "No conversation found with session ID: <id>". The default is
therefore the refusing one.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider
from app.modules.workspace.autonomous.sandbox.provider import (
    AGENT_STATE_CARRIED,
    AGENT_STATE_EPHEMERAL,
    AGENT_STATE_PERSISTS,
    agent_state_persistence,
)
from app.modules.workspace.autonomous.sandbox.remote_machine import RemoteMachineProvider

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]


def test_an_undeclared_provider_is_treated_as_ephemeral():
    class Undeclared:
        pass

    assert agent_state_persistence(Undeclared()) == AGENT_STATE_EPHEMERAL


def test_legacy_declares_persists_because_its_home_is_the_hosts():
    assert agent_state_persistence(LegacyPosixProvider()) == AGENT_STATE_PERSISTS


def test_remote_declares_persists_because_the_remote_home_is_durable():
    assert agent_state_persistence(RemoteMachineProvider()) == AGENT_STATE_PERSISTS


def test_the_fake_declares_persists_so_existing_resume_tests_still_hold():
    assert agent_state_persistence(FakeSandboxProvider()) == AGENT_STATE_PERSISTS


def test_the_three_states_are_distinct():
    assert len({AGENT_STATE_PERSISTS, AGENT_STATE_CARRIED, AGENT_STATE_EPHEMERAL}) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q`
Expected: FAIL with `ImportError: cannot import name 'AGENT_STATE_CARRIED'`

- [ ] **Step 3: Add the constants and helper**

In `app/modules/workspace/autonomous/sandbox/provider.py`, after the
`implied_required_capabilities` function, add:

```python
# ── Agent-state persistence across turns (#3237) ──────────────────────
#
# NOT a SandboxCapability: that enum is the frozen #2022 contract and must not
# grow. This is a provider attribute the runner reads defensively, in the same
# duck-typed style as `agent_turn_policy` and `apply_changes`.
#
#   persists  — HOME is durable between turns (Legacy's host HOME, Remote's
#               machine). Nothing to do.
#   carried   — HOME is ephemeral, but the provider can export/import the CLI
#               transcript (OpenSandbox).
#   ephemeral — HOME is ephemeral and nothing carries it. A resuming turn is
#               refused rather than sent into an empty HOME.
AGENT_STATE_PERSISTS = "persists"
AGENT_STATE_CARRIED = "carried"
AGENT_STATE_EPHEMERAL = "ephemeral"


def agent_state_persistence(provider: object) -> str:
    """How this provider's agent state behaves between turns.

    Defaults to ``ephemeral`` for a provider that declares nothing: an absent
    declaration must remove the ability to resume, never grant it. That is the
    #2023 attestation rule — a capability nothing enforces is the defect.
    """
    declared = getattr(provider, "agent_state_persistence", AGENT_STATE_EPHEMERAL)
    if declared in (AGENT_STATE_PERSISTS, AGENT_STATE_CARRIED, AGENT_STATE_EPHEMERAL):
        return str(declared)
    return AGENT_STATE_EPHEMERAL
```

- [ ] **Step 4: Declare it on the three existing providers**

In `app/modules/workspace/autonomous/sandbox/legacy_posix.py`, inside
`class LegacyPosixProvider`, directly under the class docstring:

```python
    # The agent runs on the host under its own HOME, which outlives any turn,
    # and the isolated launcher preserves `.claude` across task-tree wipes
    # (scripts/openace-run-as.sh `.claude-preserve`). Nothing to carry.
    agent_state_persistence = AGENT_STATE_PERSISTS
```

Add to that file's imports:

```python
from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_PERSISTS
```

In `app/modules/workspace/autonomous/sandbox/remote_machine.py`, inside
`class RemoteMachineProvider`, directly under the class docstring:

```python
    # The remote machine's HOME is durable between turns, so the CLI's own
    # transcript is already where `--resume` looks for it.
    agent_state_persistence = AGENT_STATE_PERSISTS
```

Add the same import to that file.

In `app/modules/workspace/autonomous/sandbox/fake.py`, inside
`class FakeSandboxProvider`, directly under the class docstring:

```python
    # The fake models a durable HOME, so tests that exercise --resume through
    # it keep working. A test that wants the ephemeral behaviour overrides this
    # on the instance.
    agent_state_persistence = AGENT_STATE_PERSISTS
```

Add the same import to that file.

- [ ] **Step 5: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q`
Expected: PASS, 5 passed

- [ ] **Step 6: Run the existing sandbox suites for regressions**

Run: `python3 -m pytest tests/unit -q -k "sandbox or opensandbox"`
Expected: PASS, 532 passed

- [ ] **Step 7: Commit**

```bash
git add app/modules/workspace/autonomous/sandbox/provider.py app/modules/workspace/autonomous/sandbox/legacy_posix.py app/modules/workspace/autonomous/sandbox/remote_machine.py app/modules/workspace/autonomous/sandbox/fake.py tests/unit/test_agent_state_persistence.py
git commit -m "feat(#3237): providers declare whether agent state survives a turn

Defaults to ephemeral so an absent declaration removes the ability to
resume rather than granting it."
```

---

## Task 2: The transcript store

**Files:**
- Create: `app/modules/workspace/autonomous/sandbox/agent_state_store.py`
- Test: `tests/unit/test_agent_state_store.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_agent_state_store.py`:

```python
"""Control-plane store for one CLI transcript per session line (#3237).

Mirrors what scripts/openace-run-as.sh already does with `.claude-preserve`:
hold the transcript across a HOME that gets wiped between turns.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.sandbox.agent_state_store import (
    MAX_AGENT_STATE_BYTES,
    AgentStateStore,
    AgentStateTooLarge,
    CorruptAgentState,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]


@pytest.fixture()
def store(tmp_path):
    return AgentStateStore(root=str(tmp_path / "state"))


def test_round_trip(store):
    store.put("wf-1", "sess-a", b'{"type":"user"}\n')
    assert store.get("wf-1", "sess-a") == b'{"type":"user"}\n'


def test_absent_is_none_not_an_error(store):
    """An absent slot is a first turn, or tmpfs cleared by a reboot.

    openace-run-as.sh guards its restore with `if [ -d "$preserve_claude_dir" ]`
    and simply skips — absent is not a failure, and must not fail one closed.
    """
    assert store.get("wf-1", "never-written") is None


def test_lines_do_not_collide(store):
    store.put("wf-1", "main", b"MAIN")
    store.put("wf-1", "review", b"REVIEW")
    assert store.get("wf-1", "main") == b"MAIN"
    assert store.get("wf-1", "review") == b"REVIEW"


def test_workflows_do_not_collide(store):
    store.put("wf-1", "main", b"ONE")
    store.put("wf-2", "main", b"TWO")
    assert store.get("wf-1", "main") == b"ONE"


def test_put_replaces_rather_than_appends(store):
    store.put("wf-1", "main", b"first")
    store.put("wf-1", "main", b"second")
    assert store.get("wf-1", "main") == b"second"


def test_oversized_state_is_refused_not_written(store):
    with pytest.raises(AgentStateTooLarge):
        store.put("wf-1", "main", b"x" * (MAX_AGENT_STATE_BYTES + 1))
    assert store.get("wf-1", "main") is None


def test_an_unreadable_slot_raises_rather_than_reading_as_absent(store, monkeypatch):
    """Present-but-unreadable is the fail-closed case; absent is not.

    Conflating them would hand the CLI a mis-shaped tree — the hazard
    openace-run-as.sh's `exit 70` exists for.
    """
    store.put("wf-1", "main", b"data")
    path = store.path_for("wf-1", "main")

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_bytes", boom)
    with pytest.raises(CorruptAgentState):
        store.get("wf-1", "main")
    assert path.exists()


def test_discard_removes_one_line_only(store):
    store.put("wf-1", "main", b"MAIN")
    store.put("wf-1", "review", b"REVIEW")
    store.discard("wf-1", "main")
    assert store.get("wf-1", "main") is None
    assert store.get("wf-1", "review") == b"REVIEW"


def test_discard_is_idempotent(store):
    store.discard("wf-1", "never-written")


def test_purge_removes_the_whole_workflow(store):
    store.put("wf-1", "main", b"MAIN")
    store.put("wf-1", "review", b"REVIEW")
    store.put("wf-2", "main", b"OTHER")
    store.purge("wf-1")
    assert store.get("wf-1", "main") is None
    assert store.get("wf-1", "review") is None
    assert store.get("wf-2", "main") == b"OTHER"


def test_the_directory_is_private(store):
    store.put("wf-1", "main", b"data")
    assert store.path_for("wf-1", "main").parent.stat().st_mode & 0o077 == 0


def test_keys_that_escape_the_root_are_refused(store):
    """A workflow id is not a path component to be trusted."""
    with pytest.raises(ValueError):
        store.put("../../etc", "main", b"nope")
    with pytest.raises(ValueError):
        store.put("wf-1", "../../escape", b"nope")


def test_reap_drops_slots_older_than_the_window(store, tmp_path):
    import os
    import time

    store.put("wf-old", "main", b"stale")
    store.put("wf-new", "main", b"fresh")
    old = store.path_for("wf-old", "main")
    ancient = time.time() - (8 * 24 * 3600)
    os.utime(old, (ancient, ancient))

    store.reap(max_age_seconds=7 * 24 * 3600)

    assert store.get("wf-old", "main") is None
    assert store.get("wf-new", "main") == b"fresh"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_agent_state_store.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named '...agent_state_store'`

- [ ] **Step 3: Write the store**

Create `app/modules/workspace/autonomous/sandbox/agent_state_store.py`:

```python
"""Control-plane store for CLI session transcripts (#3237).

The OpenSandbox backend gives every turn its own sandbox, so the CLI's
transcript — which is what ``--resume`` reads — dies with the pod. This holds
one transcript per session line between turns, the same job
``scripts/openace-run-as.sh`` does with ``.claude-preserve`` for the isolated
Legacy path.

Deliberately knows nothing about providers, sandboxes or the CLI: it stores
bytes under a key. That keeps the fail-closed decisions in one place (the
runner) rather than spread across a storage class.
"""

from __future__ import annotations

import logging
import os
import re
import time
from pathlib import Path

from app.modules.workspace.autonomous.task_isolation import DEFAULT_TASK_ROOT

logger = logging.getLogger(__name__)

# An order of magnitude over the largest transcript measured across real
# autonomous runs (n=31: median 0.1 MB, p90 1.1 MB, max 3.2 MB), matching the
# shape of ChangesetLimits. Growth becomes a refusal, never a silent hang.
MAX_AGENT_STATE_BYTES = 16 * 1024 * 1024

# Bounds orphans the same way the `.claude-preserve` sibling reaper does.
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600

_SAFE_KEY = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


class AgentStateError(Exception):
    """Base for store failures."""


class AgentStateTooLarge(AgentStateError):
    """The transcript exceeded MAX_AGENT_STATE_BYTES."""


class CorruptAgentState(AgentStateError):
    """A slot exists but could not be read.

    Distinct from absent ON PURPOSE. Absent means "first turn, or a reboot
    cleared tmpfs" and is handled by starting fresh. This means we can see
    state we cannot trust, which is the case openace-run-as.sh aborts on.
    """


class AgentStateStore:
    """One CLI transcript per (workflow, session line).

    Keyed by the line's TRACKING session id, never by ``cli_session_id``: the
    tracking id is stable across a force-fresh, which is exactly when the
    transcript id changes.
    """

    def __init__(self, root: str | None = None) -> None:
        self._root = Path(
            root
            or os.environ.get("OPENACE_AGENT_STATE_ROOT")
            or f"{DEFAULT_TASK_ROOT.rstrip('/')}/agent-state"
        )

    def path_for(self, workflow_id: str, line_id: str) -> Path:
        return self._workflow_dir(workflow_id) / f"{self._key(line_id)}.jsonl"

    def put(self, workflow_id: str, line_id: str, blob: bytes) -> None:
        if len(blob) > MAX_AGENT_STATE_BYTES:
            raise AgentStateTooLarge(
                f"agent state is {len(blob)} bytes, over the "
                f"{MAX_AGENT_STATE_BYTES} limit; refusing to store it"
            )
        path = self.path_for(workflow_id, line_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        # Write-then-rename so a crash mid-write cannot leave a slot that is
        # present but truncated — which get() would have to treat as corrupt.
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_bytes(blob)
        os.chmod(tmp, 0o600)
        tmp.replace(path)

    def get(self, workflow_id: str, line_id: str) -> bytes | None:
        path = self.path_for(workflow_id, line_id)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CorruptAgentState(
                f"agent state for {workflow_id}/{line_id} exists but could not be "
                f"read ({exc}); refusing to guess whether history is present"
            ) from exc

    def discard(self, workflow_id: str, line_id: str) -> None:
        try:
            self.path_for(workflow_id, line_id).unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to discard agent state %s/%s", workflow_id, line_id)

    def purge(self, workflow_id: str) -> None:
        import shutil

        try:
            shutil.rmtree(self._workflow_dir(workflow_id), ignore_errors=True)
        except OSError:
            logger.warning("Failed to purge agent state for %s", workflow_id)

    def reap(self, max_age_seconds: int = DEFAULT_MAX_AGE_SECONDS) -> int:
        """Drop slots older than the window. Returns how many were removed."""
        if not self._root.exists():
            return 0
        cutoff = time.time() - max_age_seconds
        removed = 0
        for path in self._root.glob("*/*.jsonl"):
            try:
                if path.stat().st_mtime < cutoff:
                    path.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    def _workflow_dir(self, workflow_id: str) -> Path:
        return self._root / self._key(workflow_id)

    @staticmethod
    def _key(value: str) -> str:
        """Refuse anything that is not a plain path component.

        A workflow id and a session id both reach this from the database, so
        neither is a trusted path fragment.
        """
        candidate = str(value or "").strip()
        if not _SAFE_KEY.match(candidate):
            raise ValueError(f"unsafe agent-state key {value!r}")
        return candidate
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_agent_state_store.py -q`
Expected: PASS, 13 passed

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/sandbox/agent_state_store.py tests/unit/test_agent_state_store.py
git commit -m "feat(#3237): control-plane store for one CLI transcript per session line

Absent and corrupt are distinct: absent is a first turn or a cleared
tmpfs and starts fresh; corrupt is the case openace-run-as.sh aborts on."
```

---

## Task 3: OpenSandbox exports and imports the transcript

**Files:**
- Modify: `app/modules/workspace/autonomous/sandbox/opensandbox/provider.py`
- Test: `tests/unit/test_opensandbox_agent_state.py`

- [ ] **Step 1: Write the failing test**

Create `tests/unit/test_opensandbox_agent_state.py`:

```python
"""Moving the CLI transcript in and out of an ephemeral sandbox (#3237).

Asserted at the WIRE level against FakeOpenSandboxApi — the path and filename
the provider really uses — because a hand-written fake that encoded the
assumption instead of upstream's behaviour is what hid the #2023 defects.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
from app.modules.workspace.autonomous.sandbox.opensandbox.fake_server import (
    FakeOpenSandboxApi,
)
from app.modules.workspace.autonomous.sandbox.opensandbox.provider import (
    OpenSandboxProvider,
    _AGENT_STATE_DIR,
)
from app.modules.workspace.autonomous.sandbox.provider import (
    AGENT_STATE_CARRIED,
    agent_state_persistence,
)

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]

_SID = "c53d4b8d-872a-495d-8f1f-e72ab563999a"


def test_opensandbox_declares_carried():
    assert (
        agent_state_persistence(OpenSandboxProvider.__new__(OpenSandboxProvider))
        == AGENT_STATE_CARRIED
    )


def test_the_transcript_dir_matches_what_the_cli_really_writes():
    """The CLI encodes its cwd; inside the sandbox that cwd is always /workspace.

    Verified against a real CLI (2.1.170): turn 1 wrote
    $HOME/.claude/projects/<encoded>/<id>.jsonl and _encode_project_path
    predicted <encoded> byte-for-byte. Pinning both halves here stops the two
    drifting apart.
    """
    assert AutonomousAgentRunner._encode_project_path("/workspace") == "-workspace"
    assert _AGENT_STATE_DIR == "/home/agent/.claude/projects/-workspace"


def test_export_reads_exactly_the_transcript_path(opensandbox_provider):
    provider, api, handle = opensandbox_provider
    api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] = b"LINE\n"

    assert provider.export_agent_state(handle, cli_session_id=_SID) == b"LINE\n"


def test_export_returns_none_when_the_turn_wrote_no_transcript(opensandbox_provider):
    """Not an error: a turn that never started a session has nothing to carry."""
    provider, _api, handle = opensandbox_provider
    assert provider.export_agent_state(handle, cli_session_id=_SID) is None


def test_import_writes_to_the_path_the_cli_will_read(opensandbox_provider):
    provider, api, handle = opensandbox_provider
    provider.import_agent_state(handle, cli_session_id=_SID, blob=b"LINE\n")

    assert api.uploaded[handle.sandbox_id][f"{_AGENT_STATE_DIR}/{_SID}.jsonl"] == b"LINE\n"


def test_no_credential_file_is_ever_carried(opensandbox_provider):
    """Only the transcript moves. Credentials must not round-trip the control plane."""
    provider, api, handle = opensandbox_provider
    provider.import_agent_state(handle, cli_session_id=_SID, blob=b"LINE\n")

    written = set(api.uploaded[handle.sandbox_id])
    assert written == {f"{_AGENT_STATE_DIR}/{_SID}.jsonl"}
    assert not any(".credentials" in p or ".claude.json" in p for p in written)
```

Add this fixture at the top of the same file, after `_SID`:

```python
@pytest.fixture()
def opensandbox_provider(monkeypatch):
    """A provider wired to the in-memory API, with one live sandbox handle."""
    from tests.unit.test_opensandbox_provider import _cfg, _spec

    monkeypatch.setenv("OSB_KEY", "k")
    monkeypatch.setenv("OSB_EXECD_TOKEN", "t")
    api = FakeOpenSandboxApi()
    provider = OpenSandboxProvider(_cfg(), api_factory=lambda endpoint: api)
    handle = provider.create(_spec())
    return provider, api, handle
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_opensandbox_agent_state.py -q`
Expected: FAIL with `ImportError: cannot import name '_AGENT_STATE_DIR'`

- [ ] **Step 3: Implement export/import on the provider**

In `app/modules/workspace/autonomous/sandbox/opensandbox/provider.py`, add
next to the other module constants (near `_WORKSPACE`):

```python
# Where the CLI keeps its conversation transcript inside the sandbox.
#
# The directory name is the CLI's encoding of its own cwd, and inside the
# sandbox that cwd is always _WORKSPACE — `_exec_command` passes it — so the
# value is a constant rather than a host-derived path. Verified against a real
# CLI (2.1.170): the directory it created matched
# `AutonomousAgentRunner._encode_project_path("/workspace")` exactly, and
# test_the_transcript_dir_matches_what_the_cli_really_writes pins both halves.
_AGENT_STATE_DIR = "/home/agent/.claude/projects/-workspace"
```

Add the `carried` declaration inside `class OpenSandboxProvider`, under the
class docstring:

```python
    # HOME is an emptyDir that dies with the pod, so the CLI transcript has to
    # be moved in and out by hand. See export/import_agent_state below.
    agent_state_persistence = AGENT_STATE_CARRIED
```

Add to the imports in that file:

```python
from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_CARRIED
```

Add the two methods to `OpenSandboxProvider`, after `apply_changes`:

```python
    def export_agent_state(self, handle: SandboxHandle, *, cli_session_id: str) -> bytes | None:
        """Read the CLI transcript out before the sandbox is destroyed.

        Returns ``None`` when the turn wrote no transcript — a run that never
        started a session has nothing to carry, and that is not an error.
        """
        if not cli_session_id:
            return None
        path = f"{_AGENT_STATE_DIR}/{cli_session_id}.jsonl"
        try:
            return self._api.download_file(handle.sandbox_id, path)
        except SandboxError:
            return None

    def import_agent_state(
        self, handle: SandboxHandle, *, cli_session_id: str, blob: bytes
    ) -> None:
        """Place the transcript where ``--resume`` will look for it.

        Only this one file. Not `.claude.json`, not `.credentials.json`, not
        settings: the sandbox environment is constructed, never inherited, and
        a credential must not round-trip through the control plane. Verified
        against a real CLI that this file alone is sufficient for `--resume` to
        resolve, with the original session id preserved.
        """
        path = f"{_AGENT_STATE_DIR}/{cli_session_id}.jsonl"
        self._api.upload_file(handle.sandbox_id, path, blob, 0o600)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_opensandbox_agent_state.py -q`
Expected: PASS, 6 passed

- [ ] **Step 5: Mutation-test the path constant**

Temporarily change `_AGENT_STATE_DIR` to `/home/agent/.claude/projects/-work`.

Run: `python3 -m pytest tests/unit/test_opensandbox_agent_state.py -q`
Expected: FAIL on `test_the_transcript_dir_matches_what_the_cli_really_writes`.
A test that still passes here is not testing the constant — fix it before moving on.
Then revert the change.

- [ ] **Step 6: Commit**

```bash
git add app/modules/workspace/autonomous/sandbox/opensandbox/provider.py tests/unit/test_opensandbox_agent_state.py
git commit -m "feat(#3237): OpenSandbox exports and imports the CLI transcript

One file only. A real CLI confirmed that restoring just this file into an
otherwise empty HOME lets --resume resolve with the session id preserved."
```

---

## Task 4: Gate `--resume` on the transcript actually being in place

**Files:**
- Modify: `app/modules/workspace/autonomous/agent_runner.py:2636-2648` (extract argv construction)
- Modify: `app/modules/workspace/autonomous/agent_runner.py:2704-2760` (import after upload)
- Test: `tests/unit/test_agent_state_persistence.py`

**The ordering problem:** `cmd` is built at line 2641, *before* `provider.create` at 2704, so `--resume` is baked into argv before we can know whether the transcript landed. Rather than move a 60-line block containing an early `return`, extract the pure argv construction into a helper and re-derive it if the import outcome differs from what was planned.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_agent_state_persistence.py`:

```python
def test_argv_carries_resume_only_when_the_transcript_is_there():
    """The whole point: --resume must not be sent into an empty HOME."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    adapter = _ClaudeLikeAdapter()

    with_state = runner._build_agent_argv(
        adapter, "sid-1", "/workspace", "opus", None, None, resume=True
    )
    without_state = runner._build_agent_argv(
        adapter, "sid-1", "/workspace", "opus", None, None, resume=False
    )

    assert "--resume" in with_state
    assert "--resume" not in without_state


class _ClaudeLikeAdapter:
    """Minimal stand-in with the two methods _build_agent_argv calls."""

    def build_start_args(self, session_id, project_path, model, **kw):
        args = ["claude", "--print"]
        if kw.get("resume"):
            args += ["--resume", session_id]
        return args

    def provides_full_command(self):
        return True

    def get_executable_name(self):
        return "claude"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q -k argv`
Expected: FAIL with `AttributeError: 'AutonomousAgentRunner' object has no attribute '_build_agent_argv'`

- [ ] **Step 3: Extract the argv helper**

In `app/modules/workspace/autonomous/agent_runner.py`, add this method to
`AutonomousAgentRunner`, directly above `_run_local`:

```python
    def _build_agent_argv(
        self,
        adapter,
        resume_target: str,
        project_path: str,
        model: str,
        permission_mode: str | None,
        allowed_tools: list[str] | None,
        *,
        resume: bool,
    ) -> list[str]:
        """Build the adapter's argv. PURE — no I/O, no side effects.

        Extracted so the sandbox path can re-derive it once it knows whether the
        agent state actually landed: `cmd` is otherwise built before the sandbox
        exists, which would bake `--resume` in before we could know (#3237).
        """
        return adapter.build_start_args(
            resume_target,
            project_path,
            model,
            permission_mode=permission_mode,
            allowed_tools=allowed_tools,
            resume=resume,
        )
```

Then replace lines 2641-2648 (the existing `adapter_args = adapter.build_start_args(...)`
call) with:

```python
        adapter_args = self._build_agent_argv(
            adapter,
            resume_target,
            project_path,
            model,
            permission_mode,
            allowed_tools,
            resume=resume,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q -k argv`
Expected: PASS

- [ ] **Step 5: Run the full runner suite for regressions**

Run: `python3 -m pytest tests/unit -q -k "agent_runner or sandbox"`
Expected: PASS, no new failures

- [ ] **Step 6: Commit**

```bash
git add app/modules/workspace/autonomous/agent_runner.py tests/unit/test_agent_state_persistence.py
git commit -m "refactor(#3237): extract pure argv construction from _run_local

Needed so the sandbox path can re-derive argv once it knows whether the
transcript landed; argv is otherwise fixed before the sandbox exists."
```

---

## Task 5: Wire import, export, and the up-front refusal into `_run_local`

**Files:**
- Modify: `app/modules/workspace/autonomous/agent_runner.py` (`_run_local`)
- Test: `tests/unit/test_agent_state_persistence.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_agent_state_persistence.py`:

```python
def test_a_resuming_turn_on_an_ephemeral_provider_is_refused_before_create():
    """Free by construction: no sandbox, no tokens, no wasted invocation.

    This is what converts today's guaranteed failed run — --resume into an
    empty HOME, "No conversation found", then the #2035 recovery retrying
    fresh — into an up-front refusal.
    """
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)

    class Ephemeral:
        pass

    decision = runner._plan_agent_state(
        Ephemeral(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert decision.refuse is True
    assert decision.reason_code == "agent_state_unavailable"


def test_a_non_resuming_turn_on_an_ephemeral_provider_is_fine():
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)

    class Ephemeral:
        pass

    decision = runner._plan_agent_state(
        Ephemeral(), workflow_id="wf-1", tracking_session_id="sid-1", resume=False
    )
    assert decision.refuse is False
    assert decision.resume is False


def test_a_persisting_provider_resumes_without_any_transfer(tmp_path):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.legacy_posix import LegacyPosixProvider

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    decision = runner._plan_agent_state(
        LegacyPosixProvider(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert decision.refuse is False
    assert decision.resume is True
    assert decision.blob is None


def test_a_carried_provider_with_no_stored_state_starts_fresh(monkeypatch, tmp_path):
    """Absent is not a failure — first turn, or tmpfs cleared by a reboot."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore
    from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_CARRIED

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    runner._agent_state_store = AgentStateStore(root=str(tmp_path))

    class Carried:
        agent_state_persistence = AGENT_STATE_CARRIED

    decision = runner._plan_agent_state(
        Carried(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert decision.refuse is False
    assert decision.resume is False
    assert decision.blob is None


def test_a_carried_provider_with_stored_state_plans_to_resume(tmp_path):
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore
    from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_CARRIED

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-1", "sid-1", b"TRANSCRIPT\n")
    runner._agent_state_store = store

    class Carried:
        agent_state_persistence = AGENT_STATE_CARRIED

    decision = runner._plan_agent_state(
        Carried(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert decision.refuse is False
    assert decision.resume is True
    assert decision.blob == b"TRANSCRIPT\n"


def test_a_corrupt_slot_is_refused_rather_than_read_as_absent(tmp_path, monkeypatch):
    """Present-but-unreadable is the mis-shaped-tree hazard exit 70 exists for."""
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore
    from app.modules.workspace.autonomous.sandbox.provider import AGENT_STATE_CARRIED

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-1", "sid-1", b"data")
    runner._agent_state_store = store

    def boom(*args, **kwargs):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_bytes", boom)

    class Carried:
        agent_state_persistence = AGENT_STATE_CARRIED

    decision = runner._plan_agent_state(
        Carried(), workflow_id="wf-1", tracking_session_id="sid-1", resume=True
    )
    assert decision.refuse is True
    assert decision.reason_code == "agent_state_unavailable"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q -k "plan or ephemeral or carried or corrupt"`
Expected: FAIL with `AttributeError: ... has no attribute '_plan_agent_state'`

- [ ] **Step 3: Implement the planning decision**

In `app/modules/workspace/autonomous/agent_runner.py`, add near the other
module-level dataclasses:

```python
@dataclass
class _AgentStatePlan:
    """What to do about agent state for one turn, decided BEFORE create.

    Deciding before the sandbox exists is what makes the refusal free: no pod
    is started and no tokens are spent on a turn that could not have resumed.
    """

    resume: bool
    blob: bytes | None = None
    refuse: bool = False
    reason_code: str = ""
    detail: str = ""
```

Add this method to `AutonomousAgentRunner`, above `_build_agent_argv`:

```python
    def _plan_agent_state(
        self,
        provider: object,
        *,
        workflow_id: str,
        tracking_session_id: str,
        resume: bool,
    ) -> _AgentStatePlan:
        """Decide whether this turn can resume, before anything is created.

        Mirrors scripts/openace-run-as.sh point for point rather than applying
        one blanket policy — the launcher fails closed only where failing is
        free and continuing would corrupt:

          * an `ephemeral` provider asked to resume  -> refuse (exit-70 analogue)
          * a slot present but unreadable            -> refuse (same)
          * a slot simply absent                     -> start fresh, no failure
            (the `if [ -d "$preserve_claude_dir" ]` guard around its restore)
        """
        from app.modules.workspace.autonomous.sandbox.agent_state_store import (
            CorruptAgentState,
        )
        from app.modules.workspace.autonomous.sandbox.provider import (
            AGENT_STATE_CARRIED,
            AGENT_STATE_PERSISTS,
            agent_state_persistence,
        )

        if not resume:
            return _AgentStatePlan(resume=False)

        mode = agent_state_persistence(provider)
        if mode == AGENT_STATE_PERSISTS:
            return _AgentStatePlan(resume=True)

        if mode != AGENT_STATE_CARRIED:
            return _AgentStatePlan(
                resume=False,
                refuse=True,
                reason_code="agent_state_unavailable",
                detail=(
                    f"provider {type(provider).__name__} does not carry agent state "
                    "between turns, so --resume would be sent into an empty HOME and "
                    "the turn would be wasted. Refusing before the sandbox is created."
                ),
            )

        try:
            blob = self._agent_state_store.get(workflow_id, tracking_session_id)
        except CorruptAgentState as exc:
            return _AgentStatePlan(
                resume=False,
                refuse=True,
                reason_code="agent_state_unavailable",
                detail=str(exc),
            )

        if blob is None:
            # Absent, not broken: first turn on this line, or tmpfs cleared.
            return _AgentStatePlan(resume=False)
        return _AgentStatePlan(resume=True, blob=blob)
```

Add the store to `AutonomousAgentRunner.__init__`:

```python
        from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

        self._agent_state_store = AgentStateStore()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q`
Expected: PASS, all tests

- [ ] **Step 5: Wire the plan into `_run_local`**

In `_run_local`, immediately before `resume_target = ...` (line ~2640), insert:

```python
        # #3237: decide agent state BEFORE building argv or creating anything.
        # The provider is resolved below for the sandbox path; resolve the plan
        # against it here so a refusal costs nothing.
        state_plan = self._plan_agent_state(
            self._peek_sandbox_provider("local", tenant_id=tenant_id, project_path=project_path),
            workflow_id=workflow_id,
            tracking_session_id=session_id,
            resume=resume,
        )
        if state_plan.refuse:
            return AgentTaskResult(
                session_id=session_id,
                tracking_session_id=session_id,
                success=False,
                error=state_plan.detail,
                error_code=state_plan.reason_code,
            )
        resume = state_plan.resume
```

Then, in the sandbox branch after `provider.upload_workspace(sandbox_handle, None)`
(line ~2750), insert:

```python
            # #3237: place the transcript before the agent starts. Restore is
            # best-effort — openace-run-as.sh's restore is `|| true` — because
            # the fallback (a fresh session) is correct, merely worse.
            if state_plan.blob is not None and hasattr(provider, "import_agent_state"):
                try:
                    provider.import_agent_state(
                        sandbox_handle,
                        cli_session_id=resume_session_id or "",
                        blob=state_plan.blob,
                    )
                except Exception as exc:  # noqa: BLE001 - degrade, never fail the turn
                    logger.warning("Agent state import failed, starting fresh: %s", exc)
                    cmd = self._build_agent_argv(
                        adapter,
                        resume_target,
                        project_path,
                        model,
                        permission_mode,
                        allowed_tools,
                        resume=False,
                    )
```

And before `provider.destroy(sandbox_handle)` (line ~2896), insert:

```python
            # #3237: capture the transcript before the pod goes. Log-only on
            # failure — the agent already did the work, and openace-run-as.sh's
            # exit-trap capture logs rather than exits for exactly that reason.
            if hasattr(provider, "export_agent_state"):
                captured = ""
                try:
                    captured = getattr(session, "cli_session_id", "") or ""
                    blob = provider.export_agent_state(sandbox_handle, cli_session_id=captured)
                    if blob is not None:
                        self._agent_state_store.put(workflow_id, session_id, blob)
                except Exception as exc:  # noqa: BLE001 - never discard finished work
                    logger.warning("Agent state export failed for %s: %s", captured[:8], exc)
                    self._agent_state_store.discard(workflow_id, session_id)
```

- [ ] **Step 6: Add `_peek_sandbox_provider`**

`_select_sandbox_provider` has side effects (it emits and can raise). Add a
non-raising peek next to it:

```python
    def _peek_sandbox_provider(self, workspace_type: str, **kwargs) -> object:
        """Resolve the provider for a planning decision, never raising.

        A config problem must surface from the real _select_sandbox_provider
        call below, with its reason code — not from this lookahead.
        """
        try:
            return self._select_sandbox_provider(workspace_type, **kwargs)
        except Exception:  # noqa: BLE001 - planning only
            return self._sandbox_provider
```

- [ ] **Step 7: Run the suites**

Run: `python3 -m pytest tests/unit -q -k "agent_runner or sandbox or opensandbox or agent_state"`
Expected: PASS, no new failures

- [ ] **Step 8: Commit**

```bash
git add app/modules/workspace/autonomous/agent_runner.py tests/unit/test_agent_state_persistence.py
git commit -m "feat(#3237): gate --resume on the transcript being in place

Refuse before create when the provider cannot carry state; import after
upload; export before destroy. Failure semantics mirror
openace-run-as.sh point for point."
```

---

## Task 6: Purge the store when a workflow reaches a terminal state

**Files:**
- Modify: `app/modules/workspace/autonomous/orchestrator.py`
- Test: `tests/unit/test_agent_state_store.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/test_agent_state_store.py`:

```python
def test_the_orchestrator_purges_on_terminal_state(tmp_path, monkeypatch):
    """A finished workflow's transcripts are dead weight and stale secrets."""
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-done", "main", b"MAIN")

    from app.modules.workspace.autonomous import orchestrator as orch

    orch.purge_agent_state("wf-done", store=store)
    assert store.get("wf-done", "main") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_agent_state_store.py -q -k purges`
Expected: FAIL with `AttributeError: module ... has no attribute 'purge_agent_state'`

- [ ] **Step 3: Implement**

In `app/modules/workspace/autonomous/orchestrator.py`, add at module level:

```python
def purge_agent_state(workflow_id: str, store: object | None = None) -> None:
    """Drop a finished workflow's carried CLI transcripts (#3237).

    Called when a workflow reaches a terminal state. Best-effort: failing to
    tidy up must never turn a completed workflow into a failed one.
    """
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    try:
        (store or AgentStateStore()).purge(workflow_id)
    except Exception:  # noqa: BLE001 - cleanup must not fail a finished workflow
        logger.warning("Failed to purge agent state for %s", workflow_id, exc_info=True)
```

Then call it wherever the orchestrator marks a workflow `completed`, `failed`
or `cancelled`. Find those sites with:

```bash
grep -n '"status": "completed"\|"status": "failed"\|"status": "cancelled"' app/modules/workspace/autonomous/orchestrator.py
```

Add `purge_agent_state(self._workflow_id)` after each status write.

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_agent_state_store.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/orchestrator.py tests/unit/test_agent_state_store.py
git commit -m "feat(#3237): purge carried transcripts when a workflow ends"
```

---

## Task 7: Documentation

**Files:**
- Modify: `docs/sandbox-backends.md`

- [ ] **Step 1: Add the reason code to §6**

Add this row to the reason-code table, after `egress_probe_unavailable`:

```markdown
| `agent_state_unavailable` | a resuming session line on a provider that cannot carry the CLI transcript, or a stored transcript that exists but cannot be read | refused before the sandbox is created, so no tokens are spent; check `OPENACE_AGENT_STATE_ROOT` is writable |
```

- [ ] **Step 2: Rewrite the §7 limitation**

Replace the paragraph beginning **"Multi-turn `--resume` does not carry session
history."** with:

```markdown
**Multi-turn `--resume` carries the CLI transcript, and nothing else.** Each
turn gets a fresh sandbox with an empty `HOME`, so the transcript that
`--resume` reads is exported before the sandbox is destroyed and imported into
the next one (#3237). Exactly one file moves —
`$HOME/.claude/projects/-workspace/<id>.jsonl` — never `.claude.json`,
`.credentials.json` or settings: the sandbox environment is constructed, never
inherited. A real CLI confirms that this one file is sufficient for `--resume`
to resolve with the session id preserved.

A session line whose stored transcript is absent — its first turn, or a control
plane restart that cleared tmpfs — simply starts a fresh session; that is not a
failure. A line on a provider that cannot carry state at all, or one whose
stored transcript exists but cannot be read, is refused with
`agent_state_unavailable` *before* the sandbox is created, so a turn that could
not have resumed costs nothing.
```

- [ ] **Step 3: Commit**

```bash
git add docs/sandbox-backends.md
git commit -m "docs(#3237): record what --resume now carries, and what it refuses"
```

---

## Task 8: The two secondary breakages (separate commit, same root cause)

**Files:**
- Modify: `app/modules/workspace/autonomous/agent_runner.py` (`_replay_usage_from_jsonl`, `_recover_response_text_from_jsonl`)
- Test: `tests/unit/test_agent_state_persistence.py`

Both read the **host's** `~/.claude/projects` via `_claude_projects_root`, so
under the sandbox they silently find nothing — removing the recovery net for
large-context turns whose assistant stream events were dropped.

- [ ] **Step 1: Write the failing test**

```python
def test_response_recovery_reads_the_carried_transcript_under_a_sandbox(tmp_path):
    """The host's ~/.claude/projects is empty under a sandbox provider.

    Without this the large-context recovery net silently no-ops on exactly the
    turns it exists for.
    """
    from app.modules.workspace.autonomous.agent_runner import AutonomousAgentRunner
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    runner = AutonomousAgentRunner.__new__(AutonomousAgentRunner)
    store = AgentStateStore(root=str(tmp_path))
    line = (
        b'{"type":"assistant","message":{"content":[{"type":"text","text":"RECOVERED"}]}}\n'
    )
    store.put("wf-1", "sid-1", line)
    runner._agent_state_store = store

    text = runner._recover_response_text_from_store("wf-1", "sid-1")
    assert "RECOVERED" in text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q -k recovery`
Expected: FAIL with `AttributeError: ... '_recover_response_text_from_store'`

- [ ] **Step 3: Implement**

```python
    def _recover_response_text_from_store(self, workflow_id: str, tracking_session_id: str) -> str:
        """Recover assistant text from the CARRIED transcript (#3237).

        The sibling `_recover_response_text_from_jsonl` reads the host's
        ~/.claude/projects, which is empty for a run that happened inside a
        sandbox. Same parsing, different source.
        """
        import json

        try:
            blob = self._agent_state_store.get(workflow_id, tracking_session_id)
        except Exception:  # noqa: BLE001 - recovery must never raise
            return ""
        if not blob:
            return ""
        chunks: list[str] = []
        for raw in blob.decode("utf-8", errors="replace").splitlines():
            if not raw.strip():
                continue
            try:
                event = json.loads(raw)
            except ValueError:
                continue
            if event.get("type") != "assistant":
                continue
            for block in (event.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "text":
                    chunks.append(str(block.get("text") or ""))
        return "".join(chunks)
```

Then, in `_recover_final_response_text`, after the existing JSONL attempt:

```python
        if not recovered:
            recovered = self._recover_response_text_from_store(
                session.workflow_id, session.session_id
            )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/unit/test_agent_state_persistence.py -q -k recovery`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/modules/workspace/autonomous/agent_runner.py tests/unit/test_agent_state_persistence.py
git commit -m "fix(#3237): recover assistant text from the carried transcript

The host-path readers silently no-op under a sandbox provider, removing
the large-context recovery net on exactly the turns it exists for."
```

---

## Task 9: Live verification

Source reading missed five defects on #2023 that real runs caught. This task is
not optional.

- [ ] **Step 1: Run the full unit suite**

Run: `python3 -m pytest tests/unit -q`
Expected: no new failures versus main. `tests/unit/test_fetch_wrapper_2543.py`
has 5 pre-existing failures on macOS (sudo/TCC environment); confirm they also
fail on a clean `main` worktree before dismissing them.

- [ ] **Step 2: Lint**

Run: `python3 -m pre_commit run --files $(git diff --name-only origin/main...HEAD)`
Expected: all hooks pass

- [ ] **Step 3: Two consecutive turns against a real sandbox**

Against a live OpenSandbox server, run one agent turn on a named session line,
let the sandbox be destroyed, then run a second turn on the same line. Confirm:

1. The second turn's argv contains `--resume <id>`.
2. The stream's `result` event has **no** `errors` entry containing
   `No conversation found with session ID`.
3. The `session_id` in the second turn's stream equals the first turn's.

Point 3 is the real assertion: a preserved session id means the transcript
resolved, not that a new session quietly started.

- [ ] **Step 4: Record what the run established**

Update the spec's §8 with what the sandbox run showed — including anything that
differed from this plan's assumptions. If something differed, fix the code
before proceeding.

- [ ] **Step 5: Push**

```bash
./scripts/push.sh
```

Never `git push` directly — `CLAUDE.md` requires `scripts/push.sh`, which runs
branch-scoped lint first and folds formatter autofixes into the pushed commit.

- [ ] **Step 6: Open the PR and request an independent review**

Open the PR against `main`, then dispatch an independent review agent and
iterate to zero findings before requesting merge approval.

---

## Self-Review

**Spec coverage:**

| Spec section | Task |
| --- | --- |
| §5.1 declared persistence, default ephemeral | Task 1 |
| §5.2 the seam, one file, size cap | Tasks 2, 3 |
| §5.3 the store, keying, retention | Tasks 2, 6 |
| §5.4 ordering in `_run_local` | Tasks 4, 5 |
| §5.5 three-point failure semantics | Task 5 (refuse / log / best-effort, one test each) |
| §6 testing | Tasks 1-5, 9 |
| §7 secondary breakages, separate commit | Task 8 |
| §8 live verification | Task 9 |
| §9 reboot risk (absent ≠ failure) | Task 2, Task 5 |

**Type consistency:** `_AgentStatePlan` fields (`resume`, `blob`, `refuse`,
`reason_code`, `detail`) are used identically in Tasks 4 and 5.
`agent_state_persistence` is the attribute name on providers and the helper
function name in `sandbox/provider.py` — the helper reads the attribute via
`getattr`, so the shared name is deliberate, not a collision.
`_AGENT_STATE_DIR`, `MAX_AGENT_STATE_BYTES`, `CorruptAgentState` and
`AgentStateTooLarge` are each defined once and imported where used.

**Known gap, deliberately left to Task 9:** the export in Task 5 keys off
`session.cli_session_id`, which is captured from the stream during the run. A
turn that produced no stream session id exports nothing — correct, but only
verifiable against a real sandbox.
