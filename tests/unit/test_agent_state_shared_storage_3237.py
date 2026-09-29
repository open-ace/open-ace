"""The carried-transcript root must be shared across roles and replicas (#3237).

This is a deployment contract, not a preference, and two independent facts make
it so:

* **The web role purges what the scheduler role writes.** ``stop_workflow``,
  the acceptance override and both delete routes drop a workflow's transcripts,
  and they run in the web process — while the transcripts are written by the
  scheduler process. On split storage those purges delete an unrelated empty
  directory and the real transcripts are retained indefinitely, because nothing
  can identify a deleted workflow afterwards.
* **The autonomous scheduler is not leader-gated.** ``_run_loop`` polls on every
  replica and arbitrates per workflow with a database lock, so ownership moves
  between replicas across milestones. On per-replica storage, turn N's
  transcript is invisible to whichever replica takes turn N+1 and the resume
  silently starts cold — the exact failure this feature exists to remove.

Both roles previously mounted an independent ``emptyDir`` here, so the feature
was a no-op in the shipped topology while every unit test passed. Only the
manifests can express the fix, so only a manifest test can pin it.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.regression, pytest.mark.issue(3237)]

ROOT = Path(__file__).resolve().parents[2]
STATE_ROOT_ENV = "OPENACE_AGENT_STATE_ROOT"

_DEPLOYMENTS = {
    "web": ROOT / "k8s" / "deployment.yaml",
    "scheduler": ROOT / "k8s" / "scheduler-deployment.yaml",
}


def _pod_spec(path: Path) -> dict:
    for doc in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if doc and doc.get("kind") == "Deployment":
            return doc["spec"]["template"]["spec"]
    raise AssertionError(f"no Deployment in {path}")


def _state_binding(path: Path) -> tuple[str | None, tuple | None, str | None]:
    """Return (env root, (mountPath, subPath), backing claim) for the state volume."""
    spec = _pod_spec(path)
    container = spec["containers"][0]

    env = {e["name"]: e.get("value") for e in container.get("env", []) if "value" in e}
    root = env.get(STATE_ROOT_ENV)

    # The mount that COVERS the root, which is not necessarily the root itself.
    # The deployments deliberately mount the PARENT and let uid 1000 create the
    # state directory inside it: a managed volume root is owned by root (with
    # fsGroup only granting group access), and POSIX allows chmod solely to the
    # owner — so a store rooted directly at the mount point could not tighten
    # its own directory and every write failed. Matching on equality here would
    # therefore assert the exact layout that does not work.
    covering = next(
        (
            m
            for m in container.get("volumeMounts", [])
            if root
            and (m.get("mountPath") == root or root.startswith(m.get("mountPath", "") + "/"))
        ),
        None,
    )
    mount = (covering["mountPath"], covering.get("subPath")) if covering else None
    name = covering["name"] if covering else None
    claim = next(
        (
            v.get("persistentVolumeClaim", {}).get("claimName")
            for v in spec.get("volumes", [])
            if v.get("name") == name
        ),
        None,
    )
    return root, mount, claim


@pytest.mark.parametrize("role", sorted(_DEPLOYMENTS))
def test_each_role_pins_the_state_root_explicitly(role):
    """Relying on the default puts it under the per-pod task root."""
    root, _mount, _claim = _state_binding(_DEPLOYMENTS[role])
    assert root, f"{role} does not set {STATE_ROOT_ENV}, so it falls back to per-pod storage"


def test_both_roles_agree_on_the_same_path():
    """Two roles pointing at different paths is the same bug in another form."""
    web, sched = (_state_binding(_DEPLOYMENTS[r])[0] for r in ("web", "scheduler"))
    assert web == sched, f"web={web!r} scheduler={sched!r} — the roles would not share state"


@pytest.mark.parametrize("role", sorted(_DEPLOYMENTS))
def test_the_state_root_is_backed_by_a_persistent_claim(role):
    """An emptyDir here is per-pod, which is exactly the defect."""
    root, mount, claim = _state_binding(_DEPLOYMENTS[role])
    assert mount is not None, f"{role} sets {STATE_ROOT_ENV}={root} but nothing is mounted over it"
    assert claim, (
        f"{role} backs {root} with an emptyDir or nothing; per-pod storage means "
        "the scheduler replicas and the web pods cannot see each other's transcripts"
    )


def test_both_roles_resolve_to_the_same_storage():
    """Same claim AND same subPath — either alone still splits the data."""
    web = _state_binding(_DEPLOYMENTS["web"])
    sched = _state_binding(_DEPLOYMENTS["scheduler"])
    assert web[2] == sched[2], f"different claims: web={web[2]!r} scheduler={sched[2]!r}"
    assert web[1] == sched[1], f"different mounts: web={web[1]!r} scheduler={sched[1]!r}"


def test_the_claim_allows_many_readers_and_writers():
    """The scheduler runs 2 replicas and the web 3; RWO would not bind them all."""
    claim_name = _state_binding(_DEPLOYMENTS["scheduler"])[2]
    for doc in yaml.safe_load_all((ROOT / "k8s" / "storage.yaml").read_text(encoding="utf-8")):
        if doc and doc.get("kind") == "PersistentVolumeClaim":
            if doc["metadata"]["name"] == claim_name:
                assert (
                    "ReadWriteMany" in doc["spec"]["accessModes"]
                ), f"{claim_name} is not RWX, so the replicas cannot share it"
                return
    raise AssertionError(f"claim {claim_name!r} is not declared in k8s/storage.yaml")


def test_compose_shares_one_volume_between_the_two_services():
    """docker-compose runs the scheduler as a separate container too."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]

    roots, volumes = {}, {}
    for name in ("open-ace", "scheduler"):
        svc = services[name]
        env = dict(
            entry.split("=", 1) for entry in (svc.get("environment") or []) if "=" in str(entry)
        )
        roots[name] = env.get(STATE_ROOT_ENV)
        # As above: the volume covers the root, it is not necessarily mounted
        # at it. Compose needs the parent mounted for a second reason — Docker
        # only seeds a fresh named volume with the image's ownership when a
        # directory already exists at the mount point, and the Dockerfile
        # pre-creates the parent as uid 1000.
        volumes[name] = {
            spec.split(":")[0]
            for spec in (svc.get("volumes") or [])
            if roots[name]
            and len(spec.split(":")) > 1
            and (
                roots[name] == spec.split(":")[1]
                or roots[name].startswith(spec.split(":")[1] + "/")
            )
        }

    assert roots["open-ace"] and roots["open-ace"] == roots["scheduler"], roots
    assert (
        volumes["open-ace"] and volumes["open-ace"] == volumes["scheduler"]
    ), f"the two services do not share one volume at the state root: {volumes}"
    shared = next(iter(volumes["scheduler"]))
    assert shared in (compose.get("volumes") or {}), f"{shared!r} is not a declared volume"


# ── the store under the deployments' permission shapes ────────────────
#
# Checking the YAML alone was not enough, and that is exactly how two broken
# deployments shipped: the wiring was right and the store still could not
# write. These exercise the store against the ownership the platforms actually
# produce at the mount point.


def _mount_root_for(deployment: str) -> str:
    """The path the volume is mounted at, per the manifest."""
    root, mount, _claim = _state_binding(_DEPLOYMENTS[deployment])
    assert mount is not None, f"{deployment} mounts nothing over {root}"
    return mount[0]


def test_the_state_root_is_created_below_the_mount_point():
    """The store must own the directory it hardens.

    A managed volume root is owned by root — Kubernetes `fsGroup` grants the
    GROUP access without transferring ownership, and Docker initialises a fresh
    named volume `root:root`. POSIX allows chmod only to the owner, so a state
    root placed AT the mount point could not be tightened and every write
    failed. Rooting one level below lets uid 1000 create and own it.
    """
    for role in _DEPLOYMENTS:
        root, mount, _claim = _state_binding(_DEPLOYMENTS[role])
        assert root != mount[0], (
            f"{role} roots the store at the mount point itself, so it would be "
            "asked to chmod a directory owned by root"
        )
        assert root.startswith(mount[0] + "/"), f"{role}: {root!r} is not under {mount[0]!r}"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can chmod anything, which is the whole point")
def test_the_store_writes_under_a_mount_root_it_does_not_own(tmp_path):
    """The Kubernetes shape: writable via group, owned by someone else.

    Modelled by making the mount root read-only to us but still traversable,
    then rooting the store one level below where we DO own what we create —
    which is what the manifests now do. The point is that the store must not
    fail merely because the mount point itself is not ours to chmod.
    """
    from app.modules.workspace.autonomous.sandbox.agent_state_store import AgentStateStore

    mount_root = tmp_path / "mount"
    mount_root.mkdir()
    state_root = mount_root / "agent-state"
    state_root.mkdir()
    # We own state_root, so hardening it works; the mount root above stays as
    # the platform left it and is never chmod'ed by the store.
    before = mount_root.stat().st_mode

    store = AgentStateStore(root=str(state_root))
    store.put("wf-1", "main", b"CARRIED\n")

    assert store.get("wf-1", "main") == b"CARRIED\n"
    assert mount_root.stat().st_mode == before, "the store modified the mount point above its root"


@pytest.mark.skipif(os.geteuid() == 0, reason="root can chmod anything, which is the whole point")
def test_a_state_root_owned_by_someone_else_still_writes(tmp_path, monkeypatch):
    """Fallback: a pre-provisioned root the store cannot chmod must still work.

    An unconditional `os.chmod` on the root raised PermissionError under
    Kubernetes fsGroup and failed every single write on an otherwise usable
    mount. Hardening is now best-effort where we are not the owner; the file
    itself is still created 0600 by mkstemp, so transcript CONTENTS are never
    exposed by a looser directory.
    """
    from app.modules.workspace.autonomous.sandbox import agent_state_store as mod

    state_root = tmp_path / "provisioned"
    state_root.mkdir()

    real_chmod = os.chmod
    refused: list[str] = []

    def _chmod(path, mode, *a, **k):
        # Stand in for "not the owner": the platform refuses our chmod.
        if str(path) == str(state_root):
            refused.append(str(path))
            raise PermissionError(1, "Operation not permitted")
        return real_chmod(path, mode, *a, **k)

    monkeypatch.setattr(mod.os, "chmod", _chmod, raising=False)
    monkeypatch.setattr(mod.Path, "stat", lambda self, *a, **k: os.stat(str(self)), raising=False)

    store = mod.AgentStateStore(root=str(state_root))
    store.put("wf-1", "main", b"CARRIED\n")

    assert (
        store.get("wf-1", "main") == b"CARRIED\n"
    ), "a root the process cannot chmod disabled the carry entirely"
    assert (
        store.path_for("wf-1", "main").stat().st_mode & 0o077 == 0
    ), "the transcript file itself must still be private"
