"""Mechanical acceptance gates (#2335 S4).

Five conservative, deterministic static-analysis checks that fold into the
acceptance verifier's issue-level aggregation alongside the scope gate and the
LLM verifier verdicts. Each gate returns ``list[ItemVerdict]``.

Conservatism contract (NEVER violate):
- CONFIRMED only on a definitive positive signal.
- REJECTED only on a definitive negative signal — never a guess.
- INDETERMINATE only when the gate genuinely probed its domain and could not
  conclude (e.g. it tried to read changed-file content and got nothing).
- SILENT (return ``[]``) when the gate's domain is not touched by the diff
  (e.g. no security file changed, no repo/service module, no deploy path).
  This is critical: an INDETERMINATE on a not-applicable case would flip every
  otherwise-confirmed issue to ``indeterminate`` (paused), blocking legit work.

The aggregator (``aggregate_verdicts``) treats an empty contribution as "this
gate has nothing to say", so a clean diff with no security/deploy/repo-service
changes still lets scope+verifier CONFIRMED reach ``confirmed``.

The gates are kept pure-ish: they take the changed paths + a ``read_file``
content reader so they are unit-testable with plain dicts (no real git). The
``read_file`` callable defaults to a ``git show <merge_sha>:<path>`` reader
built from ``gh``; tests inject a dict-backed reader.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TYPE_CHECKING

from app.modules.workspace.autonomous.acceptance_verdicts import ItemVerdict
from app.modules.workspace.autonomous.evidence import Verdict

if TYPE_CHECKING:
    from app.modules.workspace.autonomous.acceptance_snapshot import AcceptanceSnapshot

# A "security/data-loss" path heuristic — the paths whose changes warrant a
# negative test, a regression scan, etc. Conservative (broad) on purpose so we
# don't miss a sensitive path; the gate still defaults to INDETERMINATE, never
# a false REJECTED, when the heuristic over-matches.
_SECURITY_PATH_RE = re.compile(
    r"(security|auth|retention|delete|archive|lock|permission)", re.IGNORECASE
)


# --------------------------------------------------------------------------- #
# Content reader
# --------------------------------------------------------------------------- #
def _default_reader(gh, merge_sha: str) -> Callable[[str], str]:
    """Build a read_file(path) reader from ``git show <merge_sha>:<path>``.

    Returns ``""`` on any failure (path absent at SHA, git error). Callers treat
    an empty string as "no matchable content" -> INDETERMINATE, never a false
    REJECTED.
    """

    def _read(path: str) -> str:
        try:
            res = gh._run_git(["show", f"{merge_sha}:{path}"])  # noqa: SLF001
            txt = getattr(res, "stdout", "")
            return txt if isinstance(txt, str) else ""
        except Exception:
            return ""

    return _read


def _resolve_reader(
    gh,
    merge_sha: str,
    read_file: Callable[[str], str] | None,
) -> Callable[[str], str]:
    if read_file is not None:
        return read_file
    return _default_reader(gh, merge_sha)


def _is_test_path(path: str) -> bool:
    p = path.lower()
    return p.startswith("tests/") or p.startswith("test/") or p.startswith("test_")


def _is_security_path(path: str) -> bool:
    return bool(_SECURITY_PATH_RE.search(path))


# --------------------------------------------------------------------------- #
# Gate 1: negative_test_gate
# --------------------------------------------------------------------------- #
_NEG_TEST_ASSERTION_RE = re.compile(r"\b(assert|raises|pytest\.raises|fail|self\.assertRaises)\b")


def negative_test_gate(
    gh,
    snapshot: AcceptanceSnapshot,
    base_sha: str,
    merge_sha: str,
    *,
    read_file: Callable[[str], str] | None = None,
) -> list[ItemVerdict]:
    """Security/data-loss changed files must have failure-path test coverage.

    - CONFIRMED if at least one security path has a corresponding test file
      whose content references the changed symbol/module AND an assertion.
    - REJECTED only if a security file changed and NO changed test file touches
      it (definitive absence of negative coverage).
    - INDETERMINATE when no security file changed, or coverage can't be probed.
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    reader = _resolve_reader(gh, merge_sha, read_file)
    security_files = [p for p in changed if _is_security_path(p) and not _is_test_path(p)]
    if not security_files:
        # Not applicable: no security/data-loss path changed. Return [] so the
        # gate is silent in the aggregation (an INDETERMINATE here would flip
        # every confirmed case to indeterminate).
        return []
    test_files = [p for p in changed if _is_test_path(p)]

    confirmed_any = False
    missing: list[str] = []
    for sec in security_files:
        stem = re.sub(r"\W+", "_", _strip_ext(sec)).lower()
        covered = False
        for tf in test_files:
            content = reader(tf) or ""
            if stem and stem in content.lower() and _NEG_TEST_ASSERTION_RE.search(content):
                covered = True
                break
        if covered:
            confirmed_any = True
        else:
            missing.append(sec)

    if missing and not confirmed_any:
        return [
            ItemVerdict(
                item=f"negative-test:{missing[0]}",
                verdict=Verdict.REJECTED,
                evidence=[
                    {
                        "ref": f"missing-test:{missing[0]}",
                        "note": f"security/data-loss file changed without a failure-path test: {missing[0]}",
                    }
                ],
                rationale=(
                    "A security/data-loss path was changed but no changed test file "
                    "references it with an assertion (pytest.raises/assert/fail)."
                ),
            )
        ]
    # CONFIRMED if at least one security change has a failure-path test; else
    # [] (probed but inconclusive — don't emit a noise INDETERMINATE that would
    # flip a confirmed issue to indeterminate).
    if confirmed_any:
        return [
            ItemVerdict(
                item="negative-test:coverage",
                verdict=Verdict.CONFIRMED,
                evidence=[
                    {"ref": f"test:{tf}", "note": "failure-path test present"} for tf in test_files
                ],
                rationale="At least one security/data-loss change has failure-path test coverage.",
            )
        ]
    return []


def _strip_ext(path: str) -> str:
    """Return the path basename without its file extension, for symbol matching."""
    base = path.rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return base


# --------------------------------------------------------------------------- #
# Gate 2: legacy_pattern_gate
# --------------------------------------------------------------------------- #
_LEGACY_PATTERNS = [
    (
        "_sync_ssh_keys_legacy",
        re.compile(r"_sync_ssh_keys_legacy"),
    ),
    (
        "direct-tenant_id-authorization",
        # Bare ``tenant_id`` used directly for authorization (e.g. if x.tenant_id == ...)
        re.compile(r"tenant_id\s*==|==\s*tenant_id|filter\(.*tenant_id.*\)"),
    ),
    (
        "archive-calling-delete",
        # An archive function that also deletes (data-loss inside an "archive" path).
        re.compile(r"def\s+archive\b[\s\S]{0,400}?\bdelete\b"),
    ),
    (
        "lock-acquire-failure-proceeding",
        re.compile(
            r"proceeding anyway|continue without lock|continuing without lock", re.IGNORECASE
        ),
    ),
]


def legacy_pattern_gate(
    gh,
    snapshot: AcceptanceSnapshot,
    base_sha: str,
    merge_sha: str,
    *,
    read_file: Callable[[str], str] | None = None,
) -> list[ItemVerdict]:
    """Changed production files must not reintroduce banned legacy patterns.

    Scans changed non-test files for: ``_sync_ssh_keys_legacy``, direct
    ``tenant_id`` authorization, ``archive`` calling ``delete``, lock-acquire
    failures that proceed. REJECTED with the pattern + file if found;
    CONFIRMED if none found in the readable changed files; INDETERMINATE if the
    gate cannot read any content.
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    prod_files = [p for p in changed if not _is_test_path(p)]
    if not prod_files:
        # No production files to scan: gate is not applicable (silent).
        return []
    reader = _resolve_reader(gh, merge_sha, read_file)
    saw_any_content = False
    for p in prod_files:
        content = reader(p)
        if not isinstance(content, str) or not content:
            continue
        saw_any_content = True
        for name, pat in _LEGACY_PATTERNS:
            m = pat.search(content)
            if m:
                return [
                    ItemVerdict(
                        item=f"legacy-pattern:{name}",
                        verdict=Verdict.REJECTED,
                        evidence=[
                            {
                                "ref": f"file:{p}",
                                "note": f"banned legacy pattern '{name}' present in {p}",
                            }
                        ],
                        rationale=(
                            f"Changed production file reintroduces a banned legacy "
                            f"pattern ({name})."
                        ),
                    )
                ]
    if not saw_any_content:
        return [
            ItemVerdict(
                item="legacy-pattern:scan",
                verdict=Verdict.INDETERMINATE,
                evidence=[{"ref": "none", "note": "could not read any changed file content"}],
                rationale="Gate could not read changed-file content; cannot confirm.",
            )
        ]
    return [
        ItemVerdict(
            item="legacy-pattern:scan",
            verdict=Verdict.CONFIRMED,
            evidence=[
                {"ref": f"file:{p}", "note": "no banned legacy patterns"} for p in prod_files
            ],
            rationale="No banned legacy patterns found in changed production files.",
        )
    ]


# --------------------------------------------------------------------------- #
# Gate 3: call_chain_gate
# --------------------------------------------------------------------------- #
_REPO_OR_SERVICE_RE = re.compile(r"^app/(repositories|services)/.+\.py$")


def _module_dotted(path: str) -> str:
    """Convert app/repositories/audit_repo.py -> app.repositories.audit_repo."""
    return path[:-3].replace("/", ".") if path.endswith(".py") else path.replace("/", ".")


def _is_repo_or_service_module(path: str) -> bool:
    """Whether a path is a repository/service production module.

    The call-chain gate can't see the full pre-change tree, so it treats every
    repo/service module in the diff as a candidate "new module" and requires a
    production caller to also be in the diff — a genuinely new module normally
    lands together with at least one caller in the same PR.
    """
    return bool(_REPO_OR_SERVICE_RE.match(path))


def call_chain_gate(
    gh,
    snapshot: AcceptanceSnapshot,
    base_sha: str,
    merge_sha: str,
    *,
    read_file: Callable[[str], str] | None = None,
) -> list[ItemVerdict]:
    """New repository/service modules must have a production caller in the diff.

    REJECTED if a new repo/service module appears with only test references in
    the changed tree; CONFIRMED if a non-test changed file imports it;
    INDETERMINATE if no repo/service module changed.
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    new_modules = [p for p in changed if _is_repo_or_service_module(p)]
    if not new_modules:
        # No repo/service modules in the diff: gate is not applicable (silent).
        return []
    reader = _resolve_reader(gh, merge_sha, read_file)
    non_test_changed = [p for p in changed if not _is_test_path(p)]
    missing: list[str] = []
    confirmed_any = False
    for mod_path in new_modules:
        dotted = _module_dotted(mod_path)
        # Look for ``from <dotted> import`` or ``import <dotted>`` in a non-test
        # changed file other than the module itself.
        has_prod_caller = False
        for p in non_test_changed:
            if p == mod_path:
                continue
            content = reader(p) or ""
            if not isinstance(content, str):
                continue
            if re.search(rf"\bfrom\s+{re.escape(dotted)}\s+import\b", content) or re.search(
                rf"\bimport\s+{re.escape(dotted)}\b", content
            ):
                has_prod_caller = True
                break
        if has_prod_caller:
            confirmed_any = True
        else:
            missing.append(mod_path)
    if missing and not confirmed_any:
        return [
            ItemVerdict(
                item=f"call-chain:{_strip_ext(missing[0])}",
                verdict=Verdict.REJECTED,
                evidence=[
                    {
                        "ref": f"missing-caller:{missing[0]}",
                        "note": f"new repo/service module {missing[0]} has no production caller in the diff",
                    }
                ],
                rationale=(
                    "A new repository/service module was added but no changed "
                    "production file imports it (dead code / missing wiring)."
                ),
            )
        ]
    if confirmed_any:
        return [
            ItemVerdict(
                item="call-chain:new-modules",
                verdict=Verdict.CONFIRMED,
                evidence=[
                    {"ref": f"caller:{p}", "note": "production import present"}
                    for p in non_test_changed
                ],
                rationale="At least one new repo/service module has a production caller.",
            )
        ]
    # New modules present but no caller confirmed AND not definitively missing
    # (some had callers, or couldn't read content): stay silent rather than emit
    # a noise INDETERMINATE that would flip a confirmed issue.
    return []


# --------------------------------------------------------------------------- #
# Gate 4: deployment_gate
# --------------------------------------------------------------------------- #
_DEPLOYMENT_PATH_RE = re.compile(
    r"(^migrations/|install\.sh|sudoers|\.service$|Dockerfile|docker-compose|\.toml$|deploy)",
    re.IGNORECASE,
)
_MIGRATION_FILE_RE = re.compile(r"^migrations/versions/.+\.py$")


def deployment_gate(
    gh,
    snapshot: AcceptanceSnapshot,
    base_sha: str,
    merge_sha: str,
    *,
    read_file: Callable[[str], str] | None = None,
) -> list[ItemVerdict]:
    """Deployment-critical changes must ship their corresponding artifact.

    First cut is intentionally conservative — mostly INDETERMINATE — and only
    CONFIRMS when a schema-touching change has a matching migration file in the
    diff. It does not REJECT (no reliable "half-present" detection without the
    full tree); that is left to a later, richer cut.
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    deploy_files = [p for p in changed if _DEPLOYMENT_PATH_RE.search(p)]
    if not deploy_files:
        # No deployment-critical paths: gate is not applicable (silent).
        return []
    has_migration = any(_MIGRATION_FILE_RE.match(p) for p in changed)
    if has_migration:
        return [
            ItemVerdict(
                item="deployment:artifacts",
                verdict=Verdict.CONFIRMED,
                evidence=[
                    {"ref": f"migration:{p}", "note": "migration file present"}
                    for p in changed
                    if _MIGRATION_FILE_RE.match(p)
                ],
                rationale="A migration file is present for the schema/deployment change.",
            )
        ]
    # Deployment path changed (service/sudoers/installer/Dockerfile) but no
    # migration — not inherently wrong; defer.
    return [
        ItemVerdict(
            item="deployment:artifacts",
            verdict=Verdict.INDETERMINATE,
            evidence=[
                {
                    "ref": f"deploy:{p}",
                    "note": "deployment-critical file changed; artifact check deferred",
                }
                for p in deploy_files
            ],
            rationale=(
                "A deployment-critical path changed; first-cut gate cannot "
                "mechanically confirm the paired artifact. Defer to reviewer."
            ),
        )
    ]


# --------------------------------------------------------------------------- #
# Gate 5: regression_gate
# --------------------------------------------------------------------------- #
# An ``except`` whose body is just ``pass`` (or empty). Captures the clause.
_EMPTY_EXCEPT_RE = re.compile(
    r"except\b[^\n]*:\s*\n\s*(pass|\.\.\.|[ \t]*\n\s*(?=except|finally|def|class|\Z))",
    re.MULTILINE,
)


def regression_gate(
    gh,
    snapshot: AcceptanceSnapshot,
    base_sha: str,
    merge_sha: str,
    *,
    read_file: Callable[[str], str] | None = None,
) -> list[ItemVerdict]:
    """Security-path changes must not introduce silent-swallow regressions.

    REJECTED if a security/data-loss file adds an ``except`` whose body is just
    ``pass``/``...``/empty (swallows the error silently). INDETERMINATE
    otherwise (a bare TODO/noqa in a security path is suggestive but not a
    definitive regression, so we don't REJECT on it).
    """
    changed = gh.get_changed_files(base=base_sha, head=merge_sha) or []
    reader = _resolve_reader(gh, merge_sha, read_file)
    sec_files = [p for p in changed if _is_security_path(p) and not _is_test_path(p)]
    if not sec_files:
        # No security/data-loss files: gate is not applicable (silent).
        return []
    offending: list[str] = []
    for p in sec_files:
        content = reader(p)
        if not isinstance(content, str) or not content:
            continue
        if _EMPTY_EXCEPT_RE.search(content):
            offending.append(p)
    if offending:
        return [
            ItemVerdict(
                item=f"regression:{_strip_ext(offending[0])}",
                verdict=Verdict.REJECTED,
                evidence=[
                    {
                        "ref": f"file:{offending[0]}",
                        "note": f"empty/pass except body in security path {offending[0]}",
                    }
                ],
                rationale=(
                    "A security/data-loss file has an except body that is just "
                    "pass/.../empty (silent error swallowing)."
                ),
            )
        ]
    return []  # No empty/pass except found; gate cannot CONFIRM, so stay silent.


# --------------------------------------------------------------------------- #
# Orchestrator
# --------------------------------------------------------------------------- #
def run_mechanical_gates(
    gh,
    snapshot: AcceptanceSnapshot,
    base_sha: str,
    merge_sha: str,
    *,
    read_file: Callable[[str], str] | None = None,
) -> list[ItemVerdict]:
    """Run all 5 mechanical gates and return a flat list of their verdicts.

    Each gate is independently defensive — a gate that raises degrades to a
    single INDETERMINATE rather than aborting the whole verification.
    """
    out: list[ItemVerdict] = []
    for gate in (
        negative_test_gate,
        legacy_pattern_gate,
        call_chain_gate,
        deployment_gate,
        regression_gate,
    ):
        try:
            out.extend(gate(gh, snapshot, base_sha, merge_sha, read_file=read_file))
        except Exception as exc:  # noqa: BLE001 - degrade to INDETERMINATE
            out.append(
                ItemVerdict(
                    item=f"gate:{gate.__name__}",
                    verdict=Verdict.INDETERMINATE,
                    evidence=[{"ref": "error", "note": f"gate raised: {exc!r}"}],
                    rationale="Mechanical gate failed to run; defaulting to indeterminate.",
                )
            )
    return out
