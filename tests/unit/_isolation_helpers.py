"""Build ``workspace.isolation`` configs in tests (Issue #3446)."""

from __future__ import annotations

from app.services.workspace_isolation_config import BACKEND_LEVEL, IsolationConfig


def iso(backend: str = "shared", **fields) -> IsolationConfig:
    """An IsolationConfig for *backend* at the level that backend provides."""
    return IsolationConfig(level=BACKEND_LEVEL[backend], backend=backend, **fields)
