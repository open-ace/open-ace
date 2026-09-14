"""Qwen CLI adapter --approval-mode mapping guard.

The qwen-code CLI renamed the "suggest" approval mode to "default" between
0.15.10 and 0.20 (enum at >=0.20: plan | default | auto-edit | auto | yolo;
see packages/core/src/config/approval-mode.ts at tag v0.23.3). Dockerfile
installs @qwen-code/qwen-code@0.23.3, so every mode the adapter emits must
be in that enum or the CLI rejects the launch with an invalid-choice error.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REMOTE_AGENT_DIR = PROJECT_ROOT / "remote-agent"
if str(REMOTE_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(REMOTE_AGENT_DIR))

# ApprovalMode values accepted by @qwen-code/qwen-code >= 0.20
CLI_APPROVAL_MODES = {"plan", "default", "auto-edit", "auto", "yolo"}


def _adapter():
    from cli_adapters import ADAPTERS

    return ADAPTERS["qwen-code-cli"]()


def _approval_arg(permission_mode):
    args = _adapter().build_start_args("s1", "/tmp/p", permission_mode=permission_mode)
    i = args.index("--approval-mode")
    return args[i + 1]


@pytest.mark.parametrize(
    ("permission_mode", "expected"),
    [
        ("ask", "default"),  # "suggest" was renamed to "default" in CLI 0.20
        ("suggest", "default"),  # legacy spelling still accepted
        ("auto", "auto"),
        ("bypass", "yolo"),
        ("full-auto", "yolo"),
        # NOT the CLI's real auto-edit mode (which prompts for shell):
        # "auto-edit" is the default permission_mode of unattended autonomous
        # workflows; headless remote sessions have no human to answer the
        # CLI's shell-approval prompts, so it must map to a non-prompting mode.
        ("auto-edit", "yolo"),
    ],
)
def test_permission_mode_maps_to_valid_cli_mode(permission_mode, expected):
    assert _approval_arg(permission_mode) == expected


def test_all_mapped_modes_within_cli_enum():
    for mode in ("ask", "suggest", "auto", "bypass", "full-auto", "auto-edit"):
        assert _approval_arg(mode) in CLI_APPROVAL_MODES


def test_base_start_args_contract():
    args = _adapter().build_start_args("s1", "/tmp/p")
    assert args[0] == "qwen"
    for flag, value in (
        ("--auth-type", "openai"),
        ("--input-format", "stream-json"),
        ("--output-format", "stream-json"),
    ):
        assert args[args.index(flag) + 1] == value
    assert "--channel=SDK" in args
