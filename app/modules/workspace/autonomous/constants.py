"""Shared autonomous constants.

Holds constants that are used by both the orchestrator and the phase
handlers (``phases/*.py``) to avoid circular imports: the orchestrator
imports ``phases`` at module load (for ``resolve_phase_handler``), so
``phases/*.py`` cannot import back from the orchestrator module. Put
shared constants here instead and have both sides import them.
"""

MERGE_POLICY_PAUSE_REASON_PREFIX = "Merge blocked by repository policy:"
