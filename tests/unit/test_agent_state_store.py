"""Control-plane store for one CLI transcript per session line (#3237).

Mirrors what ``scripts/openace-run-as.sh`` already does with
``.claude-preserve``: hold the transcript across a HOME that gets wiped
between turns.
"""

from __future__ import annotations

import os
import pathlib
import time

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

    openace-run-as.sh guards its restore with ``if [ -d "$preserve_claude_dir" ]``
    and simply skips it — absent is not a failure, and must not fail closed.
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


def test_oversized_state_is_refused_and_not_written(store):
    with pytest.raises(AgentStateTooLarge):
        store.put("wf-1", "main", b"x" * (MAX_AGENT_STATE_BYTES + 1))
    assert store.get("wf-1", "main") is None


def test_an_unreadable_slot_raises_rather_than_reading_as_absent(store, monkeypatch):
    """Present-but-unreadable is the fail-closed case; absent is not.

    Conflating them would hand the CLI a mis-shaped tree — the hazard
    openace-run-as.sh's ``exit 70`` exists for.
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


def test_the_file_is_private(store):
    store.put("wf-1", "main", b"data")
    assert store.path_for("wf-1", "main").stat().st_mode & 0o077 == 0


@pytest.mark.parametrize(
    ("workflow_id", "line_id"),
    [
        ("../../etc", "main"),
        ("wf-1", "../../escape"),
        ("wf/1", "main"),
        ("", "main"),
        ("wf-1", ""),
        # Dot segments. `..` matched the first version of this validator, and
        # purge("..") then rmtree'd _root/.. — in production
        # /run/openace-agent-tasks, taking every live agent's per-task HOME and
        # every .claude-preserve directory with it.
        ("..", "main"),
        (".", "main"),
        ("...", "main"),
        ("wf-1", ".."),
        ("wf-1", "."),
        # Trailing newline. Python's `$` matches BEFORE one, so a `$`-anchored
        # pattern accepts "wf-1\n" — and the key is used verbatim as a path
        # component, so that workflow would own two slots and resume half its
        # history. `\Z` is what closes it.
        ("wf-1\n", "main"),
        ("wf-1", "main\n"),
        # Surrounding whitespace. Refused rather than stripped: stripping is
        # the "silently rewriting a key" the module comment forbids, because
        # " wf-1 " and "wf-1" stay two distinct database values while landing
        # in one slot.
        (" wf-1", "main"),
        ("wf-1 ", "main"),
        ("wf-1", " main"),
        ("wf-1", "main "),
    ],
)
def test_keys_that_are_not_plain_components_are_refused(store, workflow_id, line_id):
    """Both ids reach this from the database; neither is a trusted path fragment."""
    with pytest.raises(ValueError):
        store.put(workflow_id, line_id, b"nope")


def test_a_trailing_newline_is_not_the_same_slot(store):
    """The consequence the anchor prevents, stated as an outcome.

    If `wf-1\n` were accepted it would be a SEPARATE directory from `wf-1`,
    so the same workflow would resume from whichever spelling the caller
    happened to pass — losing every turn stored under the other one.
    """
    store.put("wf-1", "main", b"the real history")

    with pytest.raises(ValueError):
        store.put("wf-1\n", "main", b"a second slot")

    assert store.get("wf-1", "main") == b"the real history"
    assert [p.name for p in store.path_for("wf-1", "main").parent.parent.iterdir()] == ["wf-1"]


def test_the_store_and_the_provider_agree_on_what_a_safe_id_is(store):
    """One rule, two enforcers. They must not drift apart.

    The provider refuses a session id that the store would have accepted (or
    vice versa) only if one of them is wrong — and the loose one is where the
    bug lands.
    """
    from app.modules.workspace.autonomous.sandbox.opensandbox.provider import _SAFE_SESSION_ID

    for bad in ("wf-1\n", " wf-1", "wf-1 ", "..", ".", "wf/1", ""):
        assert not _SAFE_SESSION_ID.match(bad), f"the provider accepted {bad!r}"
        with pytest.raises(ValueError):
            store.put("wf-1", bad, b"nope")

    for good in ("wf-1", "main", "review", "a_b.c-d"):
        assert _SAFE_SESSION_ID.match(good), f"the provider refused {good!r}"
        store.put("wf-1", good, b"ok")


@pytest.mark.parametrize("op", ["purge", "discard", "get", "path_for"])
def test_no_operation_can_address_a_dot_segment(store, op):
    """Every entry point must refuse them, not just put().

    purge() is the dangerous one — it is the only operation that deletes a
    DIRECTORY tree rather than a file, so a `..` reaching it escapes the store
    entirely.
    """
    store.put("wf-1", "main", b"keep me")
    if op == "purge":
        store.purge("..")
    elif op == "discard":
        store.discard("..", "main")
    elif op == "get":
        with pytest.raises(ValueError):
            store.get("..", "main")
    else:
        with pytest.raises(ValueError):
            store.path_for("..", "main")
    # Nothing above the root was touched, and the real slot survives.
    assert store.path_for("wf-1", "main").parent.parent.exists()
    assert store.get("wf-1", "main") == b"keep me"


def test_purge_of_a_dot_segment_does_not_escape_the_root(tmp_path):
    """Models the production layout: the store is a SIBLING of live task trees.

    _root defaults to /run/openace-agent-tasks/agent-state, so escaping one
    level reaches the per-task HOME/TMP/XDG directories of every running agent.
    """
    task_root = tmp_path / "openace-agent-tasks"
    (task_root / "task-abc" / "home").mkdir(parents=True)
    (task_root / "task-abc.claude-preserve").mkdir(parents=True)
    store = AgentStateStore(root=str(task_root / "agent-state"))
    store.put("wf-1", "main", b"data")

    store.purge("..")

    assert (
        task_root / "task-abc" / "home"
    ).exists(), "purge escaped the store root and destroyed a live agent's HOME"
    assert (task_root / "task-abc.claude-preserve").exists()


def test_no_temp_file_is_left_behind(store):
    """Write-then-rename must not leave a .tmp sibling a reader could trip on."""
    store.put("wf-1", "main", b"data")
    siblings = list(store.path_for("wf-1", "main").parent.iterdir())
    assert [p.name for p in siblings] == ["main.jsonl"]


def test_reap_drops_slots_older_than_the_window(store):
    store.put("wf-old", "main", b"stale")
    store.put("wf-new", "main", b"fresh")
    old = store.path_for("wf-old", "main")
    ancient = time.time() - (8 * 24 * 3600)
    os.utime(old, (ancient, ancient))

    removed = store.reap(max_age_seconds=7 * 24 * 3600)

    assert removed == 1
    assert store.get("wf-old", "main") is None
    assert store.get("wf-new", "main") == b"fresh"


def test_reap_on_an_empty_root_is_a_noop(tmp_path):
    assert AgentStateStore(root=str(tmp_path / "nothing-here")).reap() == 0


# ── retention: a finished workflow keeps nothing ──────────────────────


def test_terminal_status_purges_the_workflows_transcripts(tmp_path):
    """Hooked into _update_workflow, which most status writes pass through.

    There are ~20 terminal status writes in the orchestrator; a call added
    after each would guarantee one gets missed on the next edit. It is not the
    ONLY path — autonomous_scheduler writes one directly and purges itself —
    so this is the main hook, not a chokepoint.
    """
    from app.modules.workspace.autonomous.orchestrator import purge_agent_state_if_terminal

    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-done", "main", b"MAIN")
    store.put("wf-done", "review", b"REVIEW")

    purge_agent_state_if_terminal("wf-done", {"status": "completed"}, store=store)

    assert store.get("wf-done", "main") is None
    assert store.get("wf-done", "review") is None


@pytest.mark.parametrize("status", ["completed", "failed", "cancelled"])
def test_every_terminal_status_drops_the_transcripts(tmp_path, status):
    from app.modules.workspace.autonomous.orchestrator import purge_agent_state_if_terminal

    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-1", "main", b"MAIN")

    purge_agent_state_if_terminal("wf-1", {"status": status}, store=store)

    assert store.get("wf-1", "main") is None


@pytest.mark.parametrize("status", ["running", "paused", "pending"])
def test_a_non_terminal_status_keeps_the_transcripts(tmp_path, status):
    """A paused workflow resumes later and still needs its history."""
    from app.modules.workspace.autonomous.orchestrator import purge_agent_state_if_terminal

    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-1", "main", b"MAIN")

    purge_agent_state_if_terminal("wf-1", {"status": status}, store=store)

    assert store.get("wf-1", "main") == b"MAIN"


def test_an_update_without_a_status_key_keeps_the_transcripts(tmp_path):
    from app.modules.workspace.autonomous.orchestrator import purge_agent_state_if_terminal

    store = AgentStateStore(root=str(tmp_path))
    store.put("wf-1", "main", b"MAIN")

    purge_agent_state_if_terminal("wf-1", {"error_message": "x"}, store=store)

    assert store.get("wf-1", "main") == b"MAIN"


def test_cleanup_failure_never_propagates(tmp_path):
    """Tidying up must not turn a completed workflow into a failed one."""
    from app.modules.workspace.autonomous.orchestrator import purge_agent_state_if_terminal

    class Exploding(AgentStateStore):
        def purge(self, workflow_id):
            raise OSError("disk gone")

    purge_agent_state_if_terminal(
        "wf-1", {"status": "completed"}, store=Exploding(root=str(tmp_path))
    )


# ── hardening found by independent review ─────────────────────────────


def test_the_root_itself_is_private(tmp_path):
    """The root holds one directory per workflow.

    A mode left to the umask lets any local user enumerate workflow ids, even
    though the per-workflow directories underneath are 0700.
    """
    store = AgentStateStore(root=str(tmp_path / "state"))
    store.put("wf-1", "main", b"data")

    assert store.path_for("wf-1", "main").parent.parent.stat().st_mode & 0o077 == 0


def test_the_transcript_is_never_briefly_world_readable(tmp_path, monkeypatch):
    """Created 0600, not created-then-chmod'ed.

    A chmod after the write leaves a window where a transcript — full
    conversation content, possibly tool output — is readable by anyone.
    """
    monkeypatch.setattr("os.umask", lambda mask: 0)
    store = AgentStateStore(root=str(tmp_path / "state"))

    seen: list[int] = []
    real_replace = pathlib.Path.replace

    def _replace(self, target):
        seen.append(self.stat().st_mode & 0o777)
        return real_replace(self, target)

    monkeypatch.setattr(pathlib.Path, "replace", _replace)
    store.put("wf-1", "main", b"secret")

    assert seen and all(
        mode & 0o077 == 0 for mode in seen
    ), f"the temp file was world-readable before the rename: {[oct(m) for m in seen]}"


def test_concurrent_writers_on_one_key_cannot_tear_the_slot(tmp_path):
    """A fixed temp name lets two writers interleave and rename a torn blob.

    get() cannot detect that — only an OSError raises CorruptAgentState — so
    the next turn would silently resume half a history.
    """
    import threading

    store = AgentStateStore(root=str(tmp_path / "state"))
    big_a = b"A" * 200_000
    big_b = b"B" * 200_000
    errors: list[BaseException] = []

    def write(blob):
        try:
            for _ in range(20):
                store.put("wf-1", "main", blob)
        except BaseException as exc:  # noqa: BLE001 - surfaced below
            errors.append(exc)

    threads = [
        threading.Thread(target=write, args=(big_a,)),
        threading.Thread(target=write, args=(big_b,)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, errors
    final = store.get("wf-1", "main")
    assert final in (big_a, big_b), (
        "the slot holds neither writer's blob intact — it was torn by an "
        f"interleaved write ({len(final or b'')} bytes)"
    )


def test_a_failed_write_leaves_no_temp_file_behind(tmp_path, monkeypatch):
    store = AgentStateStore(root=str(tmp_path / "state"))
    store.put("wf-1", "main", b"first")

    def _boom(self, target):
        raise OSError("rename failed")

    monkeypatch.setattr(pathlib.Path, "replace", _boom)
    with pytest.raises(OSError):
        store.put("wf-1", "main", b"second")

    leftovers = [p.name for p in store.path_for("wf-1", "main").parent.iterdir()]
    assert leftovers == ["main.jsonl"], leftovers
    assert store.get("wf-1", "main") == b"first", "the previous slot was destroyed"


# ── the retention CALL SITES, not just the functions ──────────────────
#
# Round 1 caught an entire feature whose functions were tested while nothing
# asserted they were CALLED. The fix for that finding then shipped with the
# same hole: deleting both scheduler call sites left the whole suite green.
# These close it.


def test_the_scheduler_purges_after_writing_a_terminal_recovery_status(monkeypatch):
    """autonomous_scheduler writes `status: failed` via repo.update_workflow.

    That bypasses WorkflowOrchestrator._update_workflow and therefore the purge
    hooked there, so this path has to purge itself.
    """
    from app.services import autonomous_scheduler as sched

    purged: list[str] = []
    monkeypatch.setattr(sched, "purge_agent_state_if_terminal", None, raising=False)

    class _Store:
        def purge(self, workflow_id):
            purged.append(workflow_id)

    from app.modules.workspace.autonomous import orchestrator as orch

    monkeypatch.setattr(orch, "AgentStateStore", lambda *a, **k: _Store(), raising=False)
    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.agent_state_store.AgentStateStore",
        lambda *a, **k: _Store(),
    )

    sched._purge_agent_state("wf-recovered")

    assert purged == ["wf-recovered"], (
        "the recovery sweep wrote a terminal status without dropping the "
        "workflow's carried transcripts"
    )


def test_scheduler_purge_never_raises(monkeypatch):
    """Cleanup must not break the sweep that walks many rows."""
    from app.services import autonomous_scheduler as sched

    def _boom(*a, **k):
        raise OSError("disk gone")

    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.agent_state_store.AgentStateStore", _boom
    )
    sched._purge_agent_state("wf-1")


def test_the_scheduler_reaps_orphans_at_startup(monkeypatch, tmp_path):
    """The reaper is the backstop for rows that never reach a terminal status.

    Without a caller it is decoration, and the spec promised one. Asserted on
    the CALL, because that is the part that was missing.
    """
    from app.services import autonomous_scheduler as sched

    reaped: list[int] = []

    class _Store:
        def reap(self, *a, **k):
            reaped.append(1)
            return 3

    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.agent_state_store.AgentStateStore",
        lambda *a, **k: _Store(),
    )

    assert sched._reap_agent_state() == 3
    assert reaped, "the reaper was never invoked"


def test_scheduler_reap_never_raises(monkeypatch):
    """A bounded leak beats a scheduler that will not start."""
    from app.services import autonomous_scheduler as sched

    def _boom(*a, **k):
        raise OSError("no such directory")

    monkeypatch.setattr(
        "app.modules.workspace.autonomous.sandbox.agent_state_store.AgentStateStore", _boom
    )
    assert sched._reap_agent_state() == 0


def test_scheduler_startup_wires_the_reaper():
    """Pins the CALL SITE, not the function.

    Deleting `_reap_agent_state()` from init_autonomous_scheduler left 10,274
    tests green. Reading the source is the cheap way to assert a call in a
    function whose other work needs a live database.
    """
    import inspect

    from app.services import autonomous_scheduler as sched

    source = inspect.getsource(sched.init_autonomous_scheduler)
    assert "_reap_agent_state()" in source, (
        "scheduler startup no longer reaps orphaned transcripts; the store is "
        "on tmpfs and a restart is exactly when stale entries accumulate"
    )


def test_the_recovery_sweep_wires_the_purge():
    """Same shape as above, for the terminal-status write that bypasses the hook."""
    import inspect

    from app.services import autonomous_scheduler as sched

    source = inspect.getsource(sched._reconcile_pending_transitions)
    assert "_purge_agent_state(" in source, (
        "the recovery sweep writes a terminal status without purging; that path "
        "does not go through _update_workflow"
    )
