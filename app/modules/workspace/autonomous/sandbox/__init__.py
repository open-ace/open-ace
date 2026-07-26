"""SandboxProvider contract package (Issue #2022 Phase 1).

Re-exports the stable contract surface so callers depend on the package, not
the internal module layout.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.sandbox.fake import FakeSandboxProvider
from app.modules.workspace.autonomous.sandbox.provider import (
    CapabilityUnsupported,
    SandboxError,
    SandboxProvider,
    is_current_generation,
    require_capabilities,
)
from app.modules.workspace.autonomous.sandbox.types import (
    ExecHandle,
    SandboxCapability,
    SandboxEvent,
    SandboxEventKind,
    SandboxHandle,
    SandboxSpec,
    SandboxStatus,
)

__all__ = [
    "CapabilityUnsupported",
    "ExecHandle",
    "FakeSandboxProvider",
    "SandboxCapability",
    "SandboxError",
    "SandboxEvent",
    "SandboxEventKind",
    "SandboxHandle",
    "SandboxProvider",
    "SandboxSpec",
    "SandboxStatus",
    "is_current_generation",
    "require_capabilities",
]
