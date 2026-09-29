"""Regression tests for #3321: codex autonomous runs must resume.

Before #3321 the single-shot dispatch dropped ``resume`` entirely, so every
milestone was a cold ``codex exec`` with no prior context. These tests pin the
adapter-level argv construction; the orchestrator-level capture/persist/resolve
round-trip is covered by ``test_codex_resume_carry_3321.py``.
"""

from __future__ import annotations

import os
import sys

import pytest

REMOTE_AGENT_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "remote-agent")
)
if REMOTE_AGENT_DIR not in sys.path:
    sys.path.insert(0, REMOTE_AGENT_DIR)


def _codex_adapter():
    from cli_adapters import ADAPTERS

    return ADAPTERS["codex"]()


@pytest.mark.regression
@pytest.mark.issue(3321)
def test_codex_single_shot_resume_emits_exec_resume_with_id():
    """A resume single-shot run targets ``codex exec resume <id>``.

    Fails before #3321 because ``build_single_shot_args`` has no ``resume``
    parameter and always builds a cold ``codex exec``.
    """
    adapter = _codex_adapter()

    args = adapter.build_single_shot_args(
        "continue the work",
        project_path="/workspace",
        model="o3",
        resume=True,
        resume_session_id="01a062a7-6e97-7c20-a694-2ebd922ea386",
    )

    assert args[:3] == ["codex", "exec", "resume"], args
    assert "01a062a7-6e97-7c20-a694-2ebd922ea386" in args, args
    assert "--json" in args, args
    # The prompt is still delivered.
    assert "continue the work" in args, args


@pytest.mark.regression
@pytest.mark.issue(3321)
def test_codex_resume_keeps_read_only_sandbox_via_config_override():
    """Resume preserves the read-only sandbox despite ``--sandbox`` being rejected.

    ``codex exec resume`` rejects ``--sandbox``; the policy is carried by
    ``-c sandbox_mode=read-only`` instead. Without this a resumed turn would run
    under codex's default (more permissive) sandbox — less isolated than turn 1.
    """
    adapter = _codex_adapter()

    args = adapter.build_single_shot_args(
        "continue",
        project_path="/workspace",
        model="o3",
        resume=True,
        resume_session_id="tid-1",
    )

    assert "--sandbox" not in args, f"resume rejects --sandbox: {args}"
    assert "-c" in args, args
    ci = args.index("-c")
    assert args[ci + 1] == "sandbox_mode=read-only", args


@pytest.mark.regression
@pytest.mark.issue(3321)
def test_codex_non_resume_single_shot_argv_unchanged():
    """The first-milestone (cold) argv is byte-for-byte what it was pre-#3321."""
    adapter = _codex_adapter()

    args = adapter.build_single_shot_args("do the task", project_path="/workspace", model="o3")

    assert args == [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        "read-only",
        "--model",
        "o3",
        "do the task",
    ], args


@pytest.mark.regression
@pytest.mark.issue(3321)
def test_openclaw_single_shot_ignores_resume_kwargs_without_raising():
    """openclaw shares the single-shot call site; passing resume kwargs must not raise.

    ``_run_single_shot`` calls ``adapter.build_single_shot_args`` for both codex
    and openclaw. openclaw is deferred (#3321), so it accepts-and-ignores the new
    kwargs rather than growing resume behaviour or a ``TypeError``.
    """
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["openclaw"]()

    base = adapter.build_single_shot_args("task", project_path="/workspace", model="o3")
    with_kwargs = adapter.build_single_shot_args(
        "task",
        project_path="/workspace",
        model="o3",
        resume=True,
        resume_session_id="ignored",
    )

    assert base == with_kwargs, "openclaw must ignore resume kwargs, not act on them"
