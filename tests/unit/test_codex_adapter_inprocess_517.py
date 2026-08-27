"""In-process Codex adapter/backend tests for Issue #517 (batch 16).

Extracted from the server/browser-bound e2e lanes
``tests/issues/517/e2e_codex_comprehensive.py`` (12 in-process defs) and
``tests/issues/517/e2e_codex_integration.py`` (8 in-process defs). These defs
never logically depended on the e2e modules' module-scoped autouse
``_lane_seed_and_auth`` fixture (no server, DB or browser access); this file
is self-contained and keeps every surviving assertion byte-identical unless
the ledger below says a def was merged.

Dedupe ledger (20 extracted defs -> 14 survivors; reviewer-corrected rule:
for each cross-file pair keep the integration-file copy when it subsumes the
comprehensive copy's assertions, otherwise merge the disjoint extras into the
kept def):

- env vars: comprehensive ``test_adapter_env_vars`` SUBSUMED by integration
  ``test_codex_adapter_env_vars`` (membership asserts entailed by the
  equality/indexing asserts) -> comprehensive copy deleted.
- settings: comprehensive ``test_adapter_settings`` SUBSUMED by integration
  ``test_codex_adapter_settings`` (strict superset) -> deleted.
- terminal menu: comprehensive ``test_terminal_menu_codex`` SUBSUMED by
  integration ``test_terminal_menu_includes_codex`` (``install_cmd`` equality
  entails the ``"@openai/codex" in install_cmd`` substring assert) -> deleted.
- tool name normalization: comprehensive ``test_tool_name_normalization``
  SUBSUMED by integration copy (same name, strict superset) -> deleted.
- user tool account: comprehensive ``test_user_tool_account_codex`` SUBSUMED
  by integration copy (same name, strict superset) -> deleted.
- tool connector: DISJOINT EXTRAS -> MERGED. Kept integration
  ``test_tool_connector_has_codex`` and folded in the comprehensive-only
  assert ``"coding" in codex.capabilities``; comprehensive
  ``test_tool_connector_codex`` deleted.
- adapter args: NOT a cross-file duplicate pair. Comprehensive
  ``test_adapter_interactive_args``/``test_adapter_single_shot`` and
  integration ``test_codex_adapter_build_args`` cover the same two adapter
  methods at different granularities with different extras (``args[0] ==
  "codex"``, ``--sandbox``, ``o3``), so all three defs are kept unchanged.

Unique-to-comprehensive survivors (kept verbatim): ``test_adapter_resume_args``,
``test_adapter_permission_modes``, ``test_provider_mapping_codex``,
``test_fetch_route_codex``. Unique-to-integration survivor (kept verbatim):
``test_codex_cli_adapter_imports_corrected``.
"""

import inspect
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.regression, pytest.mark.issue(517)]

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REMOTE_AGENT_DIR = PROJECT_ROOT / "remote-agent"
if str(REMOTE_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(REMOTE_AGENT_DIR))


def _get_codex_adapter():
    sys.path.insert(0, str(REMOTE_AGENT_DIR))
    from cli_adapters import ADAPTERS

    return ADAPTERS["codex"]()


# ═══════════════════════════════════════════════════════
# CLI Adapter (from e2e_codex_integration.py)
# ═══════════════════════════════════════════════════════


def test_codex_cli_adapter_imports_corrected():
    """Codex CLI adapter can be imported and has required methods."""
    sys.path.insert(0, str(REMOTE_AGENT_DIR))
    from cli_adapters import ADAPTERS

    # The adapter key is "codex" (not "codex-cli") in ADAPTERS registry
    assert "codex" in ADAPTERS, f"codex not in ADAPTERS: {list(ADAPTERS.keys())}"
    adapter_cls = ADAPTERS["codex"]
    adapter = adapter_cls()

    # Verify required attributes
    assert adapter.EXECUTABLE == "codex", f"Unexpected executable: {adapter.EXECUTABLE}"
    assert adapter.NPM_PACKAGE == "@openai/codex", f"Unexpected npm package: {adapter.NPM_PACKAGE}"

    # Verify methods exist
    assert hasattr(adapter, "get_env_vars"), "Missing get_env_vars"
    assert hasattr(adapter, "build_start_args"), "Missing build_start_args"
    assert hasattr(adapter, "build_single_shot_args"), "Missing build_single_shot_args"
    assert hasattr(adapter, "get_settings_path"), "Missing get_settings_path"
    assert hasattr(adapter, "configure_settings"), "Missing configure_settings"

    print(f"    Adapter: {adapter_cls.__name__}, executable={adapter.EXECUTABLE}")


def test_codex_adapter_env_vars():
    """Codex adapter sets correct environment variables."""
    sys.path.insert(0, str(REMOTE_AGENT_DIR))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()
    env = adapter.get_env_vars(proxy_url="http://proxy:8080", proxy_token="test-token")

    assert "OPENAI_API_KEY" in env, "Missing OPENAI_API_KEY"
    assert env["OPENAI_API_KEY"] == "test-token", "OPENAI_API_KEY should be proxy_token"
    assert "OPENAI_BASE_URL" in env, "Missing OPENAI_BASE_URL"
    assert "v1" in env["OPENAI_BASE_URL"], "OPENAI_BASE_URL should contain /v1"
    print(f"    Env vars: {list(env.keys())}")


def test_codex_adapter_build_args():
    """Codex adapter builds correct CLI arguments."""
    sys.path.insert(0, str(REMOTE_AGENT_DIR))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()

    # Interactive args
    args = adapter.build_start_args(session_id="test-123", project_path="/tmp", model="o3")
    assert "codex" in args, f"Expected 'codex' in args: {args}"
    assert "--model" in args, "Missing --model flag"
    assert "o3" in args, "Model not in args"
    print(f"    Interactive args: {args}")

    # Single shot args
    args = adapter.build_single_shot_args("write a test", project_path="/tmp", model="o3")
    assert "exec" in args, "Missing 'exec' subcommand"
    assert "--json" in args, "Missing --json flag"
    print(f"    Single-shot args: {args}")


def test_codex_adapter_settings():
    """Codex adapter configures settings correctly."""
    sys.path.insert(0, str(REMOTE_AGENT_DIR))
    from cli_adapters import ADAPTERS

    adapter = ADAPTERS["codex"]()
    settings = adapter.build_settings(
        base_settings={
            "model": "o3",
            "env": {"OPENAI_API_KEY": "should-be-stripped"},
        }
    )

    # Should have model_reasoning_summary
    assert "model_reasoning_summary" in settings, "Missing model_reasoning_summary"
    assert settings["model_reasoning_summary"] == "auto", "model_reasoning_summary should be 'auto'"
    # Sensitive keys should be stripped from env
    env = settings.get("env", {})
    assert "OPENAI_API_KEY" not in env, "OPENAI_API_KEY should be stripped"
    print(f"    Settings keys: {list(settings.keys())}")


def test_terminal_menu_includes_codex():
    """Terminal menu includes Codex entry."""
    sys.path.insert(0, str(REMOTE_AGENT_DIR))
    import importlib

    tm = importlib.import_module("terminal_menu")

    codex_entries = [t for t in tm.TOOLS if t["cli"] == "codex"]
    assert codex_entries, "No codex entry in TOOLS"
    codex = codex_entries[0]
    assert codex["name"] == "Codex", f"Unexpected name: {codex['name']}"
    assert codex["install_cmd"] == "npm install -g @openai/codex@latest"
    assert codex["env_key"] == "OPENAI_API_KEY"
    print(f"    Codex menu entry: {codex}")


# ═══════════════════════════════════════════════════════
# CLI Adapter (from e2e_codex_comprehensive.py)
# ═══════════════════════════════════════════════════════


def test_adapter_interactive_args():
    """Codex adapter builds correct interactive mode args."""
    adapter = _get_codex_adapter()
    args = adapter.build_start_args(session_id="s1", project_path="/tmp", model="o3")
    assert args[0] == "codex"
    assert "--model" in args
    assert "o3" in args
    print(f"    Args: {args}")


def test_adapter_resume_args():
    """Codex adapter uses 'resume' subcommand for session restore."""
    adapter = _get_codex_adapter()
    args = adapter.build_start_args(
        session_id="abc-123", project_path="/tmp/project", model="o3", resume=True
    )
    assert "resume" in args, f"Expected 'resume' in args: {args}"
    assert "abc-123" in args, f"Expected session_id in resume args: {args}"
    assert "--model" in args, "Expected --model flag in resume args"
    assert "--cd" in args, "Expected --cd flag for project_path"
    print(f"    Resume args: {args}")


def test_adapter_permission_modes():
    """Codex adapter maps permission modes correctly.

    Issue #2645: Security fix for permission mode mapping.
    - "ask"/"plan": Safe mode with approval prompts
    - "auto": Safe automatic mode (NO dangerous bypass)
    - "bypass": Dangerous mode with full bypass
    """
    adapter = _get_codex_adapter()

    # Ask mode (safe)
    args = adapter.build_start_args(session_id="s", project_path="/tmp", permission_mode="ask")
    assert "--ask-for-approval" in args
    assert "untrusted" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    print(f"    Ask mode: {args}")

    # Plan mode (alias for ask, safe)
    args = adapter.build_start_args(session_id="s", project_path="/tmp", permission_mode="plan")
    assert "--ask-for-approval" in args
    assert "untrusted" in args
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    print(f"    Plan mode: {args}")

    # Auto mode (safe, no dangerous flags)
    args = adapter.build_start_args(session_id="s", project_path="/tmp", permission_mode="auto")
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert "--ask-for-approval" not in args  # Should not have any permission flags
    print(f"    Auto mode (safe): {args}")

    # Bypass mode (dangerous)
    args = adapter.build_start_args(session_id="s", project_path="/tmp", permission_mode="bypass")
    assert "--dangerously-bypass-approvals-and-sandbox" in args
    print(f"    Bypass mode (dangerous): {args}")


def test_adapter_single_shot():
    """Codex adapter builds correct single-shot args."""
    adapter = _get_codex_adapter()
    args = adapter.build_single_shot_args("write a test", project_path="/tmp", model="o3")
    assert "exec" in args
    assert "--json" in args
    assert "--sandbox" in args
    assert "o3" in args
    print(f"    Single-shot: {args}")


# ═══════════════════════════════════════════════════════
# Provider Mapping (from e2e_codex_comprehensive.py)
# ═══════════════════════════════════════════════════════


def test_provider_mapping_codex():
    """_cli_tool_to_provider maps codex/codex-cli to openai."""
    from app.modules.workspace.remote_session_manager import RemoteSessionManager

    # Access the static/class method
    mgr = RemoteSessionManager.__new__(RemoteSessionManager)
    mgr._cli_tool_to_provider = RemoteSessionManager._cli_tool_to_provider.__get__(mgr)

    assert mgr._cli_tool_to_provider("codex") == "openai", "codex should map to openai provider"
    assert (
        mgr._cli_tool_to_provider("codex-cli") == "openai"
    ), "codex-cli should map to openai provider"
    print("    codex -> openai, codex-cli -> openai")


# ═══════════════════════════════════════════════════════
# Backend Modules (merged from both e2e files)
# ═══════════════════════════════════════════════════════


def test_tool_name_normalization():
    """Tool name normalization works for codex variants."""
    from app.utils.tool_names import CANONICAL_TOOL_NAMES, TOOL_NAME_ALIASES, normalize_tool_name

    assert normalize_tool_name("codex-cli") == "codex", "codex-cli should normalize to codex"
    assert normalize_tool_name("codex") == "codex", "codex should stay codex"
    assert "codex" in TOOL_NAME_ALIASES, "codex not in TOOL_NAME_ALIASES"
    assert "codex-cli" in CANONICAL_TOOL_NAMES, "codex-cli not in CANONICAL_TOOL_NAMES"
    print(f"    Aliases: {TOOL_NAME_ALIASES.get('codex', [])}")


def test_tool_connector_has_codex():
    """Tool connector registers codex tool.

    Merge survivor (ledger): integration copy + the comprehensive-only
    capabilities assert ``"coding" in codex.capabilities``.
    """
    from app.modules.workspace.tool_connector import get_tool_connector

    connector = get_tool_connector()
    codex = connector.get_tool("codex")
    assert codex, "codex not registered in tool connector"
    assert codex.name == "codex"
    assert codex.tool_type == "agent", f"Expected 'agent', got '{codex.tool_type}'"
    assert codex.supports_streaming, "codex should support streaming"
    assert codex.supports_tools, "codex should support tools"
    assert len(codex.models) > 0, "codex should have models"
    assert "coding" in codex.capabilities, "codex capabilities should include 'coding'"
    print(f"    Codex: type={codex.tool_type}, models={codex.models}")


def test_user_tool_account_codex():
    """User tool account model supports codex type."""
    from app.models.user_tool_account import TOOL_TYPES

    assert "codex" in TOOL_TYPES, "codex not in TOOL_TYPES"
    assert TOOL_TYPES["codex"] == "Codex", f"Unexpected display: {TOOL_TYPES['codex']}"
    print(f"    TOOL_TYPES['codex'] = {TOOL_TYPES['codex']}")


def test_fetch_route_codex():
    """Fetch route includes codex script execution."""
    from app.routes.fetch import run_fetch_scripts

    source = inspect.getsource(run_fetch_scripts)
    assert "fetch_codex.py" in source, "fetch_codex.py not referenced in run_fetch_scripts"
    print("    fetch_codex.py included in fetch route")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
