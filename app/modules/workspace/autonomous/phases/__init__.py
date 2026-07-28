"""Phase B (#2044) complex-phase handler registry.

A migrated complex phase (development/pr_review/merge) registers its module-level
``handle(ctx, deps) -> PhaseResult`` here. Thin phases (preparation/planning/
report/wait) stay as bound methods on ``AutonomousOrchestrator`` and are wired in
``advance()`` directly, so they are NOT in this table.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.modules.workspace.autonomous.phase_contract import PhaseResult

# name -> handle(ctx, deps). Populated as T10/T11/T12 land.
PHASE_HANDLERS: dict[str, Callable[..., PhaseResult | None]] = {}


def register_phase_handler(name: str, fn: Callable[..., PhaseResult | None]) -> None:
    PHASE_HANDLERS[name] = fn


def resolve_phase_handler(name: str) -> Callable[..., PhaseResult | None] | None:
    return PHASE_HANDLERS.get(name)


# Register the migrated handlers. Importing the module has the side effect of
# registration; the import is local so a malformed module surfaces at first use
# rather than at package import time of unrelated phases.
from app.modules.workspace.autonomous.phases import merge as _merge  # noqa: E402

register_phase_handler("merge", _merge.handle)
