"""Control-plane store for CLI session transcripts (#3237).

The OpenSandbox backend gives every turn its own sandbox, so the CLI's
transcript — which is what ``--resume`` reads — dies with the pod. This holds
one transcript per session line between turns, the same job
``scripts/openace-run-as.sh`` does with ``.claude-preserve`` for the isolated
Legacy path.

Deliberately knows nothing about providers, sandboxes or the CLI: it stores
bytes under a key. That keeps the fail-closed decisions in one place (the
runner, which has the context to make them) rather than spread across a
storage class.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from app.modules.workspace.autonomous.task_isolation import DEFAULT_TASK_ROOT

logger = logging.getLogger(__name__)

# An order of magnitude over the largest transcript measured across real
# autonomous runs (n=31: median 0.1 MB, p90 1.1 MB, max 3.2 MB), matching the
# shape of ChangesetLimits. Growth becomes a refusal, never a silent hang.
MAX_AGENT_STATE_BYTES = 16 * 1024 * 1024

# Bounds orphans the way the `.claude-preserve` sibling reaper already does for
# the Legacy path (#2403).
DEFAULT_MAX_AGE_SECONDS = 7 * 24 * 3600

# A workflow id and a session id both reach this from the database, so neither
# is a trusted path fragment. Anything that is not a plain component is refused
# rather than sanitised — silently rewriting a key would make two different
# workflows share one slot.
#
# The leading class is NOT the same as the rest: it excludes `.`, which is what
# makes the dot segments unrepresentable. An earlier version allowed them, and
# `purge("..")` then resolved to `_root/..` — in production
# `/run/openace-agent-tasks` — where `shutil.rmtree` would take out every live
# agent's per-task HOME/TMP/XDG and every `.claude-preserve` directory with it.
# Verified destructive against a temp tree modelling that layout.
_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")


class AgentStateError(Exception):
    """Base for store failures."""


class AgentStateTooLarge(AgentStateError):
    """The transcript exceeded :data:`MAX_AGENT_STATE_BYTES`."""


class CorruptAgentState(AgentStateError):
    """A slot exists but could not be read.

    Distinct from absent ON PURPOSE. Absent means "first turn, or a reboot
    cleared tmpfs" and is handled by starting a fresh session. This means we
    can see state we cannot trust, which is the case ``openace-run-as.sh``
    aborts on with ``exit 70`` rather than hand the CLI a mis-shaped tree.
    """


class AgentStateStore:
    """One CLI transcript per (workflow, session line).

    Keyed by the line's TRACKING session id, never by ``cli_session_id``: the
    tracking id is the stable per-line identity ``SESSION_LINE_FIELDS`` stores
    on the workflow row and survives a force-fresh, which is exactly when the
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
        path = self.path_for(workflow_id, line_id)
        if len(blob) > MAX_AGENT_STATE_BYTES:
            raise AgentStateTooLarge(
                f"agent state is {len(blob)} bytes, over the {MAX_AGENT_STATE_BYTES} "
                "limit; refusing to store it"
            )
        # 0o700 on BOTH levels. The root holds one directory per workflow, so a
        # mode left to the umask would let any local user enumerate workflow ids.
        self._root.mkdir(parents=True, exist_ok=True)
        os.chmod(self._root, 0o700)
        path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(path.parent, 0o700)
        # Write-then-rename so a crash mid-write cannot leave a slot that is
        # present but truncated — which get() would have to treat as corrupt,
        # turning a tidy restart into a refused turn.
        #
        # A UNIQUE temp name, not "<key>.jsonl.tmp": two writers on one key
        # would otherwise interleave on the same file and rename a torn blob
        # into the slot, which get() cannot detect (only an OSError raises
        # CorruptAgentState) and which would silently resume half a history.
        # Created 0600 by mkstemp rather than chmod'ed afterwards, so the
        # transcript is never briefly world-readable.
        fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(blob)
            tmp.replace(path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise

    def get(self, workflow_id: str, line_id: str) -> bytes | None:
        path = self.path_for(workflow_id, line_id)
        if not path.exists():
            return None
        try:
            return path.read_bytes()
        except OSError as exc:
            raise CorruptAgentState(
                f"agent state for {workflow_id}/{line_id} exists but could not be read "
                f"({exc}); refusing to guess whether history is present"
            ) from exc

    def discard(self, workflow_id: str, line_id: str) -> None:
        try:
            self.path_for(workflow_id, line_id).unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.warning("Failed to discard agent state %s/%s", workflow_id, line_id)

    def purge(self, workflow_id: str) -> None:
        try:
            shutil.rmtree(self._workflow_dir(workflow_id), ignore_errors=True)
        except (OSError, ValueError):
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
        candidate = str(value or "").strip()
        if not _SAFE_KEY.match(candidate):
            raise ValueError(f"unsafe agent-state key {value!r}")
        return candidate
