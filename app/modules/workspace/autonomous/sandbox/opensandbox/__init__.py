"""OpenSandbox production sandbox backend (Issue #2023).

Implements the frozen ``#2022`` :class:`SandboxProvider` contract over
OpenSandbox (``opensandbox-group/OpenSandbox``, Apache-2.0), whose Kubernetes
runtime supplies the gVisor / Kata isolation this repository could not offer
with ``LegacyPosixProvider`` (four POSIX capabilities) or
``RemoteMachineProvider`` (none — the ``#2082`` fix).

Design note — why capabilities come from operator attestations
--------------------------------------------------------------
An agent running inside the sandbox can reach execd itself: every command
execd spawns inherits execd's own environment (``runtime/command.go`` sets
``cmd.Env = mergeEnvs(os.Environ(), extraEnv)``), so the agent can read
``EXECD_ACCESS_TOKEN``, and ``POST /command`` accepts a caller-supplied
``uid: 0``. Any *in-band* mechanism — a ``ulimit`` prefix on the command
string, a ``uid`` argument — is therefore bypassable from inside and cannot
support a capability claim. Enforcement lives at the pod/kernel layer
(``securityContext``, kubelet ``podPidsLimit``, the cluster ``NetworkPolicy``,
the egress sidecar), which the provider cannot observe through the API. Those
properties are declared as operator *attestations* in the backend config, and
a capability is declared only when its attestation is present — so a missing
attestation makes a spec requiring it fail closed rather than degrade quietly.
"""

from __future__ import annotations

from app.modules.workspace.autonomous.sandbox.opensandbox.config import (
    Attestations,
    ChangesetLimits,
    EndpointConfig,
    PoolConfig,
    SandboxBackendConfig,
    SandboxConfigError,
    candidate_backend_config_paths,
    load_backend_config,
    parse_backend_config,
    resolve_backend_config_path,
)

__all__ = [
    "Attestations",
    "ChangesetLimits",
    "EndpointConfig",
    "PoolConfig",
    "SandboxBackendConfig",
    "SandboxConfigError",
    "candidate_backend_config_paths",
    "load_backend_config",
    "parse_backend_config",
    "resolve_backend_config_path",
]
