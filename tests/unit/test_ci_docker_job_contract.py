"""Contract for the main-push docker job's local image availability.

The docker job builds with buildx and then runs `docker run`/`docker images`
against the built tag inside the same job. Without `load: true` the build
never exports the image into the local Docker engine, so every consumer step
fails — exactly the main-push-only red of #3169 that PR CI cannot catch
(the job is gated to `push` + `refs/heads/main`). This pins the behavior:
whenever the job consumes the built tag locally, the producing build-push
step must load it into the engine.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

pytestmark = [pytest.mark.regression, pytest.mark.issue(3169)]

TAG_PATTERN = re.compile(r"open-ace:\$\{\{ github\.sha \}\}")


def _docker_job():
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return workflow["jobs"]["docker"]


def test_docker_job_stays_main_push_only():
    job = _docker_job()
    assert job["needs"] == ["lint", "test", "build"]
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def test_local_tag_consumers_require_the_build_step_to_load_the_image():
    job = _docker_job()
    steps = job["steps"]

    build_steps = [
        step for step in steps if str(step.get("uses", "")).startswith("docker/build-push-action@")
    ]
    assert len(build_steps) == 1, "expected exactly one docker/build-push-action step"
    build = build_steps[0]

    consumers = [
        step
        for step in steps
        if step.get("run") and TAG_PATTERN.search(step["run"]) and "docker " in step["run"]
    ]
    assert consumers, "docker job must still exercise the built image locally"

    # The producer must tag with the same literal and export into the engine.
    tags = build.get("with", {}).get("tags", [])
    tags_text = " ".join(tags) if isinstance(tags, list) else str(tags)
    assert TAG_PATTERN.search(tags_text)
    assert build["with"].get("load") is True, (
        "build-push step must set load: true whenever later steps run the "
        "built tag via docker run/docker images (image must exist locally)"
    )
    assert build["with"].get("push") is False
    # Without an explicit target Docker builds the LAST stage (migration),
    # whose entrypoint ignores the command and execs server.py — the image
    # verified here must be the web image.
    assert build["with"].get("target") == "production"

    verify = next(step for step in steps if step.get("name") == "Verify code-server installation")
    assert TAG_PATTERN.search(verify["run"]) and "docker run" in verify["run"]
    # The production image is production-capable (FLASK_ENV=production), so
    # its entrypoint's fail-closed security-mode validation rejects one-shot
    # commands without an explicit OPENACE_SECURITY_MODE.
    assert "-e OPENACE_SECURITY_MODE=" in verify["run"]
    size = next(step for step in steps if step.get("name") == "Check image size")
    assert TAG_PATTERN.search(size["run"]) and "docker images" in size["run"]
    assert "GITHUB_STEP_SUMMARY" in size["run"]


def test_production_image_precreates_logs_dir_for_non_root_entrypoint():
    # docker-entrypoint.sh runs `mkdir -p /app/logs` (Issue #1205) as uid 1000
    # before dispatching one-shot commands, /app itself is root-owned, and the
    # gitignored logs/ directory never ships in CI build contexts — the image
    # must therefore pre-create it with open-ace ownership.
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")
    production = dockerfile.split("FROM production AS development")[0]
    assert "mkdir -p /app/logs && chown open-ace:open-ace /app/logs" in production


# ── Multi-user deployment smoke wiring (#3289/#3293) ────────────────


@pytest.mark.issue(3289)
@pytest.mark.issue(3293)
def test_docker_job_runs_the_multiuser_deployment_smoke():
    """The root+multi-user positive paths (real useradd, permission-700
    collection, cross-user read regression, 100-user scale, wrapper audit
    pseudonymization) have no PR-lane coverage by construction — CI has no
    root. The main-push docker job's smoke step is their only automated
    verification; removing it (or losing --user 0 / any of the three
    multi-user envs) silently reintroduces the #3289/#3293 gap."""
    steps = _docker_job()["steps"]
    smoke = next((step for step in steps if "multiuser_smoke.py" in str(step.get("run", ""))), None)
    assert smoke is not None, (
        "docker job must run scripts/multiuser_smoke.py (the #3289/#3293 "
        "deployment-level verification)"
    )
    run = smoke["run"]
    assert "docker run" in run and TAG_PATTERN.search(
        run
    ), "smoke must docker run the freshly built main-push tag"
    assert "--user 0" in run, "production target ends at USER 1000 — the smoke needs root"
    for env in (
        "OPENACE_SECURITY_MODE=development",
        "WORKSPACE_MULTI_USER_MODE=true",
        "OPENACE_ALLOW_ROOT_MULTI_USER=1",
        "WORKSPACE_BASE_DIR=/workspace",
    ):
        assert f"-e {env}" in run, f"smoke step lost -e {env}"


def test_docker_sandbox_blocks_the_pr_gate():
    """PR #3386 review: docker-sandbox must be a REQUIRED PR-gate input.

    The repo ruleset's only required status check is "PR Gate"; if the
    sandbox-image job were not wired into its needs/validation, a sandbox
    build regression (e.g. the uid/gid-1000 clash) could not block a merge.
    """
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    gate = workflow["jobs"]["pr-gate"]

    assert "docker-sandbox" in gate["needs"]

    gate_step = gate["steps"][0]
    assert (
        gate_step.get("env", {}).get("SANDBOX") == "${{ needs.docker-sandbox.result }}"
    ), "gate must read the sandbox job result"
    assert '"SANDBOX"' in gate_step.get(
        "run", ""
    ), "gate must validate the sandbox result as required"


def test_qwen_stack_pins_are_consistent_across_all_sites():
    """Upgrade guard (PR #3386): the webui/CLI pair is pinned at SEVEN sites
    (two Dockerfiles, package install.sh, remote-agent install.sh/.ps1,
    terminal_menu.py install_cmd + QWEN_PINNED_VERSION,
    cli_adapters/qwen_code.py) plus the cn/en DEPLOYMENT.md manual-install
    guides. The docker-method installer pins nothing on the host by design:
    its qwen stack is the image-pinned pair (R15 review). An upgrade that
    misses any site ships mixed versions. This contract fails with the
    per-site inventory the moment the pins disagree, so an upgrade is one
    commit that touches every site CI points at.
    """
    repo = REPO_ROOT
    webui_sites = {
        "Dockerfile": (repo / "Dockerfile").read_text(encoding="utf-8"),
        "scripts/docker/webui-sandbox.Dockerfile": (
            repo / "scripts" / "docker" / "webui-sandbox.Dockerfile"
        ).read_text(encoding="utf-8"),
    }
    cli_sites = dict(webui_sites)
    cli_sites.update(
        {
            "scripts/install-central/package-method/install.sh": (
                repo / "scripts" / "install-central" / "package-method" / "install.sh"
            ).read_text(encoding="utf-8"),
            "remote-agent/install.sh": (repo / "remote-agent" / "install.sh").read_text(
                encoding="utf-8"
            ),
            "remote-agent/install.ps1": (repo / "remote-agent" / "install.ps1").read_text(
                encoding="utf-8"
            ),
        }
    )

    def pins(sites, pattern):
        found = {}
        for name, text in sites.items():
            match = re.search(pattern, text, re.MULTILINE)
            assert match is not None, f"{name}: pin not found via {pattern!r}"
            found[name] = match.group(1)
        return found

    webui = pins(
        webui_sites,
        r"npm install -g(?: --prefix /usr)? qwen-code-webui@([0-9.]+)",
    )
    webui["scripts/install-central/package-method/install.sh (QWEBUI_VERSION)"] = pins(
        {
            "scripts/install-central/package-method/install.sh": cli_sites[
                "scripts/install-central/package-method/install.sh"
            ]
        },
        r'^QWEBUI_VERSION="([0-9.]+)"',
    )["scripts/install-central/package-method/install.sh"]
    webui["docs/cn/DEPLOYMENT.md"] = pins(
        {
            "docs/cn/DEPLOYMENT.md": (repo / "docs" / "cn" / "DEPLOYMENT.md").read_text(
                encoding="utf-8"
            )
        },
        r"npm install -g qwen-code-webui@([0-9.]+)",
    )["docs/cn/DEPLOYMENT.md"]
    webui["docs/en/DEPLOYMENT.md"] = pins(
        {
            "docs/en/DEPLOYMENT.md": (repo / "docs" / "en" / "DEPLOYMENT.md").read_text(
                encoding="utf-8"
            )
        },
        r"npm install -g qwen-code-webui@([0-9.]+)",
    )["docs/en/DEPLOYMENT.md"]

    cli = {}
    for name in ("Dockerfile", "scripts/docker/webui-sandbox.Dockerfile"):
        cli[name] = pins(
            {name: cli_sites[name]},
            r"@qwen-code/qwen-code@([0-9.]+)",
        )[name]
    cli["scripts/install-central/package-method/install.sh (QWEN_CLI_VERSION)"] = pins(
        {
            "scripts/install-central/package-method/install.sh": cli_sites[
                "scripts/install-central/package-method/install.sh"
            ]
        },
        r'^QWEN_CLI_VERSION="([0-9.]+)"',
    )["scripts/install-central/package-method/install.sh"]
    cli["remote-agent/install.sh (QWEN_CLI_VERSION)"] = pins(
        {"remote-agent/install.sh": cli_sites["remote-agent/install.sh"]},
        r'^QWEN_CLI_VERSION="([0-9.]+)"',
    )["remote-agent/install.sh"]
    for doc in ("cn", "en"):
        cli[f"docs/{doc}/DEPLOYMENT.md"] = pins(
            {
                f"docs/{doc}/DEPLOYMENT.md": (repo / "docs" / doc / "DEPLOYMENT.md").read_text(
                    encoding="utf-8"
                )
            },
            r"@qwen-code/qwen-code@([0-9.]+)",
        )[f"docs/{doc}/DEPLOYMENT.md"]
    cli["remote-agent/install.ps1 ($QwenCliVersion)"] = pins(
        {"remote-agent/install.ps1": cli_sites["remote-agent/install.ps1"]},
        r"^\$QwenCliVersion = \"([0-9.]+)\"",
    )["remote-agent/install.ps1"]
    cli["remote-agent/terminal_menu.py"] = pins(
        {
            "remote-agent/terminal_menu.py": (
                REPO_ROOT / "remote-agent" / "terminal_menu.py"
            ).read_text(encoding="utf-8")
        },
        r"npm install -g @qwen-code/qwen-code@([0-9.]+)",
    )["remote-agent/terminal_menu.py"]
    cli["remote-agent/terminal_menu.py (QWEN_PINNED_VERSION)"] = pins(
        {
            "remote-agent/terminal_menu.py (QWEN_PINNED_VERSION)": (
                REPO_ROOT / "remote-agent" / "terminal_menu.py"
            ).read_text(encoding="utf-8")
        },
        r'^QWEN_PINNED_VERSION = "([0-9.]+)"',
    )["remote-agent/terminal_menu.py (QWEN_PINNED_VERSION)"]
    qwen_adapter = (REPO_ROOT / "remote-agent" / "cli_adapters" / "qwen_code.py").read_text(
        encoding="utf-8"
    )
    cli["remote-agent/cli_adapters/qwen_code.py"] = pins(
        {"remote-agent/cli_adapters/qwen_code.py": qwen_adapter},
        r'^\s*PINNED_VERSION = "([0-9.]+)"',
    )["remote-agent/cli_adapters/qwen_code.py"]
    assert len(re.findall(r"^\s*PINNED_VERSION\s*=", qwen_adapter, re.MULTILINE)) == 1
    assert len(re.findall(r"^\s*NPM_PACKAGE\s*=", qwen_adapter, re.MULTILINE)) == 1
    assert re.findall(r'^\s*NPM_PACKAGE = "([^"]+)"', qwen_adapter, re.MULTILINE) == [
        "@qwen-code/qwen-code"
    ]
    cli["remote-agent/cli_adapters/qwen_code.py (install command)"] = pins(
        {"remote-agent/cli_adapters/qwen_code.py (install command)": qwen_adapter},
        r'^\s*return "npm install -g @qwen-code/qwen-code@([0-9.]+)"',
    )["remote-agent/cli_adapters/qwen_code.py (install command)"]

    assert len(set(webui.values())) == 1, f"webui pins disagree: {webui}"
    assert len(set(cli.values())) == 1, f"CLI pins disagree: {cli}"


_SCAN_SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    "build",
    "dist",
    ".worktrees",
    ".claude",
    ".venv",
    "venv",
}


def _iter_repo_files(root: Path) -> list[Path]:
    """git ls-files when available; otherwise a deterministic tree walk so
    the sweep also runs from `git archive` snapshots / source tarballs that
    carry no .git (PR #3386 R15 review: the git-only form exited 128 there)."""
    import subprocess as sp

    try:
        out = sp.run(["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True)
        return [root / rel for rel in out.stdout.splitlines() if rel]
    except (sp.CalledProcessError, OSError):
        return sorted(
            path
            for path in root.rglob("*")
            if path.is_file() and not (_SCAN_SKIP_DIRS & set(path.parts))
        )


def _logical_lines(text: str, suffix: str = ""):
    """Yield (lineno, line) with shell backslash AND PowerShell backtick
    continuations joined — a command split across lines is still one
    command (PR #3386 R15/R16 reviews)."""
    pending = None
    pending_no = 0
    for no, raw in enumerate(text.splitlines(), 1):
        if pending is not None:
            line = pending + raw.lstrip()
        else:
            line = raw
            pending_no = no
        continued = line.endswith("\\")
        if suffix == ".ps1":
            continued = continued or line.endswith("`")
        if suffix in {".bat", ".cmd"}:
            continued = continued or line.endswith("^")
        if continued:
            # The escaped newline disappears. Existing whitespace before the
            # continuation remains significant; never manufacture a space
            # inside a token such as `qwen-code-\\\nwebui`.
            pending = line[:-1]
        else:
            yield pending_no, line
            pending = None
    if pending is not None:
        yield pending_no, pending


_NPM_TOKENS = {"npm", "npm.cmd", "npm.exe"}
_SHELLISH_INSTALL_SUFFIXES = {
    ".bat",
    ".cmd",
    ".md",
    ".ps1",
    ".sh",
    ".mk",
    ".yaml",
    ".yml",
    ".zsh",
}
# npm's official install aliases INCLUDING the historical isnt* typo
# aliases npm resolves to Install (verified against npm 11.x).
_INSTALL_ALIASES = {
    "install",
    "instal",
    "insta",
    "inst",
    "ins",
    "in",
    "i",
    "isntall",
    "isntal",
    "isnta",
    "isnt",
    "add",
}
_PKG_RE = re.compile(r"^(qwen-code-webui|@qwen-code/qwen-code)(.*)$")
_TOKEN_STRIP = "\\\"'(),;`&|"


def _command_segments(line: str, suffix: str = "") -> list[str]:
    """Split shell commands and discard comments while respecting quotes."""
    segments: list[str] = []
    start = 0
    quote = ""
    escaped = False
    i = 0
    while i < len(line):
        char = line[i]
        if quote:
            if char == quote and not escaped:
                quote = ""
            escaped = char == "\\" and not escaped
            if char != "\\":
                escaped = False
            i += 1
            continue
        if char in {'"', "'"}:
            quote = char
            i += 1
            continue
        if char == "#" and (i == 0 or line[i - 1].isspace()):
            segments.append(line[start:i])
            break
        width = 2 if line[i : i + 2] in {"&&", "||"} else 1
        caret_escaped_ampersand = suffix in {".bat", ".cmd"} and i > 0 and line[i - 1] == "^"
        separator = (
            char in {";", "|"}
            or width == 2
            or (char == "&" and not caret_escaped_ampersand and bool(line[start:i].strip()))
        )
        if separator:
            segments.append(line[start:i])
            start = i + width
            i += width
            continue
        i += 1
    else:
        segments.append(line[start:])
    return [segment for segment in segments if segment.strip()]


def _command_tokens(line: str) -> list[str]:
    """Split shell/PowerShell-ish text without losing quoted paths."""
    return re.findall(r'"[^"]*"|\'[^\']*\'|\S+', line)


def _literal_token(token: str) -> str:
    # Adjacent shell quotes concatenate at runtime.
    return token.replace('"', "").replace("'", "").strip(_TOKEN_STRIP)


def _package_literal_token(token: str) -> str:
    """Resolve quoting/escaping forms that can conceal a package literal."""
    value = token
    if value.startswith("$'") and value.endswith("'"):
        value = value[2:-1]
        value = re.sub(r"\\x([0-9A-Fa-f]{2})", lambda match: chr(int(match.group(1), 16)), value)
    value = re.sub(r"\\(.)", r"\1", value)
    value = re.sub(r"\^(.)", r"\1", value)
    value = re.sub(r"`(.)", r"\1", value)
    return _literal_token(value)


def _direct_npm_token(token: str) -> bool:
    literal = _literal_token(token)
    basename = re.split(r"[/\\]", literal)[-1].lower()
    normalized = _package_literal_token(token)
    normalized_basename = re.split(r"[/\\]", normalized)[-1].lower()
    return basename in _NPM_TOKENS or normalized_basename in _NPM_TOKENS


_EXPECTED_PACKAGE_VERSIONS = {
    "qwen-code-webui": "0.2.43",
    "@qwen-code/qwen-code": "0.23.3",
}
_TRUSTED_DYNAMIC_INSTALL_LINES = {
    "remote-agent/cli_adapters/claude_code.py": {
        'return f"npm install -g {self.NPM_PACKAGE}@latest"'
    },
    "remote-agent/cli_adapters/codex_cli.py": {
        'return f"npm install -g {self.NPM_PACKAGE}@latest"'
    },
}
_UNRESOLVED_INSTALL_ARG_RE = re.compile(
    r"\$(?:[A-Za-z_{('\"0-9@*?#$!\-])" r"|%~?[0-9*]|%[^%\s]+%" r"|![^!\s]+!|@\(|\{[^{}]+\}"
)
_NPM_OPTIONS_WITH_VALUES = {
    "--auth-type",
    "--audit-level",
    "--before",
    "--cache",
    "--cafile",
    "--cpu",
    "--fetch-retries",
    "--fetch-retry-factor",
    "--fetch-retry-maxtimeout",
    "--fetch-retry-mintimeout",
    "--include",
    "--install-strategy",
    "--libc",
    "--loglevel",
    "--maxsockets",
    "--node-options",
    "--omit",
    "--os",
    "--otp",
    "--prefix",
    "--proxy",
    "--registry",
    "--scope",
    "--save-prefix",
    "--script-shell",
    "--tag",
    "--userconfig",
    "--workspace",
    "-w",
    "-C",
}
_CONTROL_FLOW_PREFIXES = {"for", "foreach", "if", "switch", "while"}
_DISPLAY_PREFIXES = {"echo", "printf", "write-host", "write-output"}


def _unresolved_install_arg(token: str) -> bool:
    if token.count("`") >= 2:
        return True  # POSIX command substitution, not a PowerShell escape
    if _UNRESOLVED_INSTALL_ARG_RE.search(token):
        return True
    # PowerShell splatting (`@Pkgs`) is unresolved; npm scopes
    # (`@vendor/package`) are ordinary literal package names.
    return re.fullmatch(r"@[A-Za-z_][A-Za-z0-9_]*", _literal_token(token)) is not None


def _can_contain_shell_install(path: Path, text: str) -> bool:
    return (
        path.suffix.lower() in _SHELLISH_INSTALL_SUFFIXES
        or path.name.lower().startswith("dockerfile")
        or path.name.lower() in {"makefile", "gnumakefile"}
        or (not path.suffix and text.startswith("#!"))
    )


def _is_comment_or_control_segment(path: Path, segment: str) -> bool:
    stripped = segment.lstrip()
    lowered = stripped.casefold()
    if path.suffix.lower() in {".bat", ".cmd"}:
        command = lowered.lstrip("@").lstrip()
        if re.match(r"rem(?:\s|$)", command) or command.startswith("::"):
            return True
        if command.startswith("echo ") and "^&" in segment:
            return True
    return path.suffix.lower() == ".ps1" and stripped.startswith("<#")


def _serialized_command_tokens(segment: str) -> list[str]:
    """Expose commands embedded in quotes/YAML/JSON and join shell fragments."""
    value = segment.replace('"', "").replace("'", "")
    value = re.sub(r"([\\^`])(?=\S)", "", value)
    value = re.sub(r"[{}\[\],():]", " ", value)
    return value.split()


def _expected_qwen_spec(token: str) -> bool | None:
    literal = _package_literal_token(token)
    if "@npm:" in literal:
        literal = literal.split("@npm:", 1)[1]
    match = _PKG_RE.match(literal)
    if not match:
        if "qwen-code-webui" in literal or "@qwen-code/qwen-code" in literal:
            return False  # tarball/URL/serialized form: not an exact package spec
        return None
    rest = match.group(2)
    if not rest or not rest.startswith("@"):
        return False if not rest else None
    suffix = rest[1:].rstrip(_TOKEN_STRIP)
    return suffix == _EXPECTED_PACKAGE_VERSIONS[match.group(1)]


def _install_argument_positions(tokens: list[str], alias_pos: int) -> list[int]:
    """Return npm install positional arguments, excluding option values."""
    positions: list[int] = []
    skip_value = False
    for index in range(alias_pos + 1, len(tokens)):
        cleaned = _literal_token(tokens[index])
        if skip_value:
            skip_value = False
            continue
        if cleaned == "--":
            positions.extend(range(index + 1, len(tokens)))
            break
        if cleaned.startswith("--") and "=" in cleaned:
            continue
        if cleaned in _NPM_OPTIONS_WITH_VALUES:
            skip_value = True
            continue
        if cleaned.startswith("-"):
            continue
        positions.append(index)
    return positions


def _npm_subcommand_positions(
    tokens: list[str], normalized: list[str], npm_positions: list[int]
) -> list[int]:
    """Locate npm's actual subcommand instead of later prose tokens."""
    result: list[int] = []
    for npm_pos in npm_positions:
        skip_value = False
        optional_unknown_value = False
        for index in range(npm_pos + 1, len(tokens)):
            cleaned = _literal_token(tokens[index])
            if skip_value:
                skip_value = False
                continue
            if cleaned.startswith("--") and "=" in cleaned:
                continue
            if cleaned in _NPM_OPTIONS_WITH_VALUES:
                skip_value = True
                optional_unknown_value = False
                continue
            if cleaned.startswith("-"):
                optional_unknown_value = cleaned.startswith("--")
                continue
            if normalized[index].lower() in _INSTALL_ALIASES:
                result.append(index)
                break
            if optional_unknown_value:
                optional_unknown_value = False
                continue
            result.append(index)
            break
    return result


def _npm_install_alias_positions(
    tokens: list[str], normalized: list[str], npm_positions: list[int]
) -> list[int]:
    return [
        index
        for index in _npm_subcommand_positions(tokens, normalized, npm_positions)
        if normalized[index].lower() in _INSTALL_ALIASES
    ]


def _only_npm_options_between(tokens: list[str], start: int, end: int) -> bool:
    """Whether tokens[start:end] are only npm options and their values."""
    skip_value = False
    for token in tokens[start:end]:
        cleaned = _literal_token(token)
        if skip_value:
            skip_value = False
            continue
        if cleaned.startswith("--") and "=" in cleaned:
            continue
        if cleaned in _NPM_OPTIONS_WITH_VALUES:
            skip_value = True
            continue
        if cleaned.startswith("-"):
            continue
        return False
    return not skip_value


def _serialized_qwen_install_is_unpinned(segment: str) -> bool:
    tokens = _serialized_command_tokens(segment)
    normalized = [_package_literal_token(token) for token in tokens]
    npm_positions = [index for index, token in enumerate(tokens) if _direct_npm_token(token)]
    aliases = _npm_install_alias_positions(tokens, normalized, npm_positions)
    display = bool(tokens) and _literal_token(tokens[0]).lstrip("@").casefold() in _DISPLAY_PREFIXES
    for alias in aliases:
        states = {
            index: _expected_qwen_spec(tokens[index])
            for index in _install_argument_positions(tokens, alias)
        }
        if any(state is False for state in states.values()):
            return True
        if not display and any(
            _unresolved_install_arg(tokens[index]) and states[index] is not True for index in states
        ):
            return True
    return False


def _scan_unpinned_qwen_installs(files) -> list[str]:
    """Reject every qwen install whose exact approved pin is not provable.

    Any unresolved install argument is rejected, even when the package name
    comes from the environment or string composition. Only narrow, structurally
    locked production generators receive an exception.
    """
    violations = []
    for path in files:
        if path.resolve() == Path(__file__).resolve():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if path.suffix.lower() == ".ps1":
            text = re.sub(
                r"<#.*?#>",
                lambda match: "\n" * match.group(0).count("\n"),
                text,
                flags=re.DOTALL,
            )
        try:
            relative_path = path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
        except ValueError:
            relative_path = ""
        if relative_path.startswith("static/assets/"):
            continue  # generated/minified frontend output, never an install entry
        trusted_dynamic_lines = _TRUSTED_DYNAMIC_INSTALL_LINES.get(relative_path, set())

        for lineno, line in _logical_lines(text, path.suffix.lower()):
            violated = False
            for segment in _command_segments(line, path.suffix.lower()):
                if _is_comment_or_control_segment(path, segment):
                    continue
                serialized_hint = re.sub(r"[\"'\\^`]", "", segment.casefold())
                if (
                    "npm" in serialized_hint or "qwen" in serialized_hint
                ) and _serialized_qwen_install_is_unpinned(segment):
                    violations.append(f"{path}:{lineno} unpinned entry: {line.strip()}")
                    violated = True
                    break
                tokens = _command_tokens(segment)
                cleaned = [_literal_token(token) for token in tokens]
                normalized = [_package_literal_token(token) for token in tokens]
                all_alias_positions = [
                    index
                    for index, token in enumerate(normalized)
                    if token.lower() in _INSTALL_ALIASES
                ]
                npm_positions = [
                    index for index, token in enumerate(tokens) if _direct_npm_token(token)
                ]
                subcommand_positions = _npm_subcommand_positions(tokens, normalized, npm_positions)
                alias_positions = _npm_install_alias_positions(tokens, normalized, npm_positions)
                if npm_positions and not alias_positions:
                    # A computed npm subcommand cannot prove it is not an
                    # install alias when followed by an unresolved/qwen target.
                    alias_positions = [
                        candidate
                        for candidate in subcommand_positions
                        if _unresolved_install_arg(tokens[candidate])
                        and any(
                            _unresolved_install_arg(tokens[index])
                            or _expected_qwen_spec(tokens[index]) is not None
                            for index in range(candidate + 1, len(tokens))
                        )
                    ][:1]
                npm_install = bool(alias_positions)
                if not npm_install:
                    # Shell launchers may precede a computed npm executable,
                    # e.g. `sudo "$Runner" install "$PKGS"`.
                    npm_install = any(
                        alias_pos > 0
                        and _can_contain_shell_install(path, text)
                        and cleaned[0].lower() not in _CONTROL_FLOW_PREFIXES
                        and cleaned[0].lstrip("@").lower() not in _DISPLAY_PREFIXES
                        and not (
                            path.suffix.lower() == ".md"
                            and cleaned[alias_pos].lower() in {"i", "in"}
                        )
                        and any(
                            _unresolved_install_arg(tokens[index])
                            and (
                                _only_npm_options_between(tokens, index + 1, alias_pos)
                                or any(
                                    _expected_qwen_spec(tokens[target]) is not None
                                    for target in _install_argument_positions(tokens, alias_pos)
                                )
                            )
                            for index in range(alias_pos)
                        )
                        and not any("pip" in token.casefold() for token in cleaned[:alias_pos])
                        and any(
                            _unresolved_install_arg(tokens[index])
                            or _expected_qwen_spec(tokens[index]) is not None
                            for index in _install_argument_positions(tokens, alias_pos)
                        )
                        for alias_pos in all_alias_positions
                    )
                    if npm_install:
                        alias_positions = all_alias_positions
                if not npm_install:
                    continue
                if segment.strip() in trusted_dynamic_lines:
                    continue

                first_alias = min(alias_positions)
                all_package_states = [
                    _expected_qwen_spec(token) for token in tokens[first_alias + 1 :]
                ]
                argument_positions = _install_argument_positions(tokens, first_alias)
                package_states = {
                    index: _expected_qwen_spec(tokens[index]) for index in argument_positions
                }
                unresolved_args = any(
                    _unresolved_install_arg(tokens[index]) and package_states[index] is not True
                    for index in argument_positions
                )
                if unresolved_args or any(state is False for state in all_package_states):
                    violations.append(f"{path}:{lineno} unpinned entry: {line.strip()}")
                    violated = True
                    break
            if violated:
                continue
    return violations


def test_no_unpinned_qwen_stack_install_entries_anywhere():
    """Upgrade guard (PR #3386 review): every `npm install` line touching the
    qwen stack packages — in scripts, docs, or tests — must carry the exact
    validated literal version. Bare, variable, stale, or @latest entries are
    exactly how the docker-method installer and the
    DEPLOYMENT.md guides kept installing unvalidated mixed stacks after the
    pair was pinned everywhere else."""
    violations = _scan_unpinned_qwen_installs(_iter_repo_files(REPO_ROOT))

    assert not violations, (
        "qwen stack npm install entries must be pinned "
        "(qwen-code-webui@X @qwen-code/qwen-code@Y):\n" + "\n".join(violations)
    )


def test_unpinned_sweep_flags_continuations_and_aliases(tmp_path):
    """Negative tests from the PR #3386 R15 review: line continuations and
    npm's i/add aliases must not bypass the sweep."""
    bypass = tmp_path / "bypass.sh"
    bypass.write_text(
        "npm install -g \\\n  qwen-code-webui\n"
        "npm i -g qwen-code-webui\n"
        "npm add -g @qwen-code/qwen-code\n"
        "npm in -g qwen-code-webui\n"
        "npm --silent install -g @qwen-code/qwen-code\n"
        "npm isntall -g qwen-code-webui\n"
        "npm --prefix /opt/cache install -g qwen-code-webui\n"
        "LATEST=latest; npm install -g qwen-code-webui@${LATEST}\n"
        "npm.cmd install -g @qwen-code/qwen-code\n",
        encoding="utf-8",
    )
    ps_bypass = tmp_path / "bypass.ps1"
    ps_bypass.write_text(
        "npm install -g `\n  qwen-code-webui\n",
        encoding="utf-8",
    )
    pinned = tmp_path / "pinned.sh"
    pinned.write_text(
        "npm install -g qwen-code-webui@0.2.43 @qwen-code/qwen-code@0.23.3\n"
        'npm i -g "qwen-code-webui@0.2.43"\n'
        "npm add -g @qwen-code/qwen-code@0.23.3 \\\n  --unsafe-perm\n",
        encoding="utf-8",
    )

    violations = _scan_unpinned_qwen_installs([bypass, ps_bypass, pinned])

    # 9 shell-form entries (continuation, i, add, in/isntall aliases,
    # valueless and value-carrying leading options, non-allowlisted
    # ${VAR}, npm.cmd shim) + 1 PowerShell backtick continuation
    assert len(violations) == 10, violations
    assert all("bypass" in v for v in violations)
    joined = "\n".join(violations)
    for needle in (
        "npm install -g qwen-code-webui",  # continuation joined
        "npm i -g qwen-code-webui",
        "npm add -g @qwen-code/qwen-code",
        "npm in -g qwen-code-webui",
        "npm --silent install -g @qwen-code/qwen-code",
        "npm isntall -g qwen-code-webui",
        "npm --prefix /opt/cache install -g qwen-code-webui",
        "npm install -g qwen-code-webui@${LATEST}",
        "npm.cmd install -g @qwen-code/qwen-code",
    ):
        assert needle in joined, needle


def test_unpinned_sweep_rejects_indirection_paths_and_reassignment(tmp_path):
    """R18: fail closed by syntax class, not by a growing list of exact
    spellings. Package indirection cannot prove a pin; npm itself may be an
    absolute path, a variable, or a case-insensitive Windows shim; and an
    allowlisted version variable stops being trusted after reassignment."""
    cases = {
        "shell-package-var.sh": 'PKGS="qwen-code-webui"\nnpm install -g "$PKGS"\n',
        "powershell-package-var.ps1": ('$Pkgs = @("@qwen-code/qwen-code")\nnpm install -g $Pkgs\n'),
        "absolute-npm.sh": "/usr/bin/npm install -g qwen-code-webui\n",
        "variable-npm.sh": ('NPM="/usr/bin/npm"\n"$NPM" install -g @qwen-code/qwen-code\n'),
        "windows-case.ps1": "NPM.CMD install -g qwen-code-webui\n",
        "windows-quoted-path.ps1": (
            '& "C:\\Program Files\\nodejs\\npm.cmd" install -g @qwen-code/qwen-code\n'
        ),
        "version-reassignment.sh": (
            'QWEBUI_VERSION="0.2.43"\n'
            'QWEBUI_VERSION="latest"\n'
            'npm install -g "qwen-code-webui@${QWEBUI_VERSION}"\n'
        ),
        "npm-array.sh": (
            "NPM_CMD=(npm)\n" "NPM_CMD=(sudo npm)\n" '"${NPM_CMD[@]}" install -g qwen-code-webui\n'
        ),
        "package-array.sh": ("PKGS=(qwen-code-webui)\n" 'npm install -g "${PKGS[@]}"\n'),
        "powershell-case.ps1": ('$Pkgs = @("@qwen-code/qwen-code")\nnpm install -g $PKGS\n'),
        "powershell-version-reassignment.ps1": (
            '$QwenCliVersion = "0.23.3"\n'
            '$qWENcLIVERSION = "latest"\n'
            'npm install -g "@qwen-code/qwen-code@$QWENCLIVERSION"\n'
        ),
        "conditional-reassignment.sh": (
            'QWEBUI_VERSION="0.2.43"\n'
            "if true; then QWEBUI_VERSION=latest; fi\n"
            'npm install -g "qwen-code-webui@${QWEBUI_VERSION}"\n'
        ),
        "conditional-pin.sh": (
            "if false; then QWEBUI_VERSION=0.2.43; fi\n"
            'npm install -g "qwen-code-webui@${QWEBUI_VERSION}"\n'
        ),
        "quoted-npm-component.sh": '/usr/bin/"npm" install -g qwen-code-webui\n',
        "concatenated-package.sh": 'npm install -g qwen-code-webui"@latest"\n',
        "multiline-package-array.sh": (
            "PKGS=(\n  qwen-code-webui\n)\n" 'npm install -g "${PKGS[@]}"\n'
        ),
        "multiline-powershell-array.ps1": (
            '$Pkgs = @(\n  "@qwen-code/qwen-code"\n)\n' "npm install -g $Pkgs\n"
        ),
        "package-command-substitution.sh": (
            "PKGS=$(printf '%s' qwen-code-webui)\n" 'npm install -g "$PKGS"\n'
        ),
        "composed-package.sh": (
            "BASE=qwen-code\n" 'PKG="${BASE}-webui"\n' 'npm install -g "$PKG"\n'
        ),
        "external-package-variable.sh": 'npm install -g "$PKGS"\n',
        "positional-package.sh": 'npm install -g "$1"\n',
        "all-positional-packages.sh": 'npm install -g "$@"\n',
        "powershell-splat.ps1": "npm install -g @Pkgs\n",
        "batch-positional.cmd": "npm install -g %1\n",
        "batch-modified-positional.cmd": "npm install -g %~1\n",
        "batch-replacement.cmd": "npm install -g %PKGS:old=new%\n",
        "batch-delayed.cmd": "npm install -g !PKGS!\n",
        "batch-delayed-replacement.cmd": "npm install -g !PKGS:old=new!\n",
        "double-indirection.sh": '"$Runner" install -g "$PKGS"\n',
        "short-alias-indirection.sh": '$Runner i -g "$PKGS"\n',
        "in-alias-indirection.sh": "$Runner in -g qwen-code-webui\n",
        "ansi-c-quoted-package.sh": "npm install -g $'qwen-code-webui'\n",
        "bash-escaped-version.sh": "npm install -g qwen-code-webui\\@latest\n",
        "cmd-escaped-version.cmd": "npm install -g qwen-code-webui^@latest\n",
        "powershell-escaped-version.ps1": "npm install -g qwen-code-webui`@latest\n",
        "ansi-c-hex-version.sh": "npm install -g $'qwen-code-webui\\x40latest'\n",
        "backtick-substitution.sh": 'npm install "`printf qwen-code-webui`"\n',
        "escaped-npm.sh": "n\\pm install -g qwen-code-webui\n",
        "escaped-npm.cmd": "n^pm install -g qwen-code-webui\n",
        "escaped-npm.ps1": "n`pm install -g qwen-code-webui\n",
        "token-continuation.sh": "npm install qwen-code-\\\nwebui@latest\n",
        "executor-continuation.sh": "n\\\npm install qwen-code-webui\n",
        "offline-direct.sh": "npm install --offline qwen-code-webui\n",
        "offline-indirect.sh": 'npm install --offline "$PKGS"\n',
        "workflow.yml": '"$Runner" install "$PKGS"\n',
        "Dockerfile": '"$Runner" install "$PKGS"\n',
        "Makefile": 'target:\n\t"$(Runner)" install "$(PKGS)"\n',
        "variable-subcommand.sh": 'npm "$CMD" qwen-code-webui\n',
        "defaulted-subcommand.sh": "npm ${CMD:-install} qwen-code-webui\n",
        "escaped-subcommand.sh": "npm ins\\tall qwen-code-webui\n",
        "escaped-subcommand.cmd": "npm ins^tall qwen-code-webui\n",
        "escaped-subcommand.ps1": "npm ins`tall qwen-code-webui\n",
        "ansi-subcommand.sh": "npm $'install' qwen-code-webui\n",
        "sudo-wrapper.sh": 'sudo "$Runner" install "$PKGS"\n',
        "command-wrapper.sh": 'command "$Runner" install "$PKGS"\n',
        "call-wrapper.cmd": "call %Runner% install %PKGS%\n",
        "npm-alias-webui.sh": "npm install mine@npm:qwen-code-webui@latest\n",
        "npm-alias-cli.sh": "npm install mine@npm:@qwen-code/qwen-code@latest\n",
        "locale-quoted-package.sh": 'npm install $"qwen-code-webui"\n',
        "ansi-octal-version.sh": "npm install $'qwen-code-webui\\100latest'\n",
        "quoted-display.md": 'echo "npm install -g qwen-code-webui@latest"\n',
        "nested-shell.sh": 'sh -c "npm install -g qwen-code-webui@latest"\n',
        "quoted-workflow.yml": 'run: "npm install -g qwen-code-webui@latest"\n',
        "package.json": '{"scripts":{"bad":"npm install qwen-code-webui@latest"}}\n',
        "nested-cmd.cmd": 'cmd /c "npm install qwen-code-webui@latest"\n',
        "Dockerfile.json": 'RUN ["npm","install","qwen-code-webui"]\n',
        "adjacent-webui-quotes.sh": 'npm install "qwen-code-"webui@latest\n',
        "adjacent-cli-quotes.sh": 'npm install "@qwen-code/"qwen-code@latest\n',
        "adjacent-npm-quotes.sh": '"n"pm install qwen-code-webui\n',
        "cmd-caret-continuation.cmd": "npm install -g ^\r\n qwen-code-webui\n",
        "executor-option.sh": '"$Runner" --silent install -g qwen-code-webui\n',
        "defaulted-executor-option.sh": ("${NPM:-npm} --silent install -g qwen-code-webui\n"),
        "array-executor-option.sh": ('"${NPM_CMD[@]}" --silent install -g qwen-code-webui\n'),
        "tarball-url.sh": (
            "npm install https://registry.npmjs.org/qwen-code-webui/-/"
            "qwen-code-webui-latest.tgz\n"
        ),
        "nested-unresolved.sh": 'sh -c "npm install -g $PKGS"\n',
        "nested-positional.sh": 'bash -c "npm install -g $1"\n',
        "package-unresolved.json": '{"scripts":{"bad":"npm install $PKGS"}}\n',
        "adjacent-npm-unresolved.sh": '"n"pm install $PKGS\n',
        "npm-color-option.sh": "npm --color always install -g qwen-code-webui\n",
        "npm-location-option.sh": ("npm --location global install -g qwen-code-webui\n"),
        "npm-color-short-i.sh": "npm --color always i -g qwen-code-webui\n",
        "npm-location-short-in.sh": ("npm --location global in -g @qwen-code/qwen-code\n"),
        "runner-color-option.sh": ('"$Runner" --color always install -g qwen-code-webui\n'),
        "powershell-get-command.ps1": (
            "$Npm = Get-Command npm\n" "& $Npm install -g qwen-code-webui\n"
        ),
        "defaulted-npm.sh": "${NPM:-npm} install -g qwen-code-webui\n",
        "unknown-executor.sh": "$Runner install -g qwen-code-webui\n",
        "wrong-fixed-version.sh": "npm install -g qwen-code-webui@0.2.44\n",
        "markdown-powershell.md": (
            "```powershell\n" '$Pkgs = @("@qwen-code/qwen-code")\n' "npm install -g $PKGS\n" "```\n"
        ),
    }
    paths = []
    for name, content in cases.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        paths.append(path)

    violations = _scan_unpinned_qwen_installs(paths)

    assert len(violations) == len(cases), violations
    assert {Path(item.split(":", 1)[0]).name for item in violations} == set(cases)


def test_unpinned_sweep_accepts_literal_pins_with_options_and_npm_paths(tmp_path):
    powershell = tmp_path / "pinned.ps1"
    powershell.write_text(
        '& "C:\\Program Files\\nodejs\\npm.cmd" install -g ' '"@qwen-code/qwen-code@0.23.3"\n',
        encoding="utf-8",
    )
    shell = tmp_path / "pinned.sh"
    shell.write_text(
        'npm install --registry "$REGISTRY" -g qwen-code-webui@0.2.43 # $HOME\n'
        'npm install --audit-level "$LEVEL" qwen-code-webui@0.2.43\n'
        'npm install --before "$DATE" @qwen-code/qwen-code@0.23.3\n'
        'npm install --cpu "$CPU" qwen-code-webui@0.2.43\n'
        'npm install --os "$OS" qwen-code-webui@0.2.43\n'
        'npm install --proxy "$PROXY" @qwen-code/qwen-code@0.23.3\n'
        'npm install --save-prefix "$PREFIX" qwen-code-webui@0.2.43\n'
        'npm install --otp "$OTP" qwen-code-webui@0.2.43\n'
        'npm install -C "$PREFIX" qwen-code-webui@0.2.43\n'
        'npm install -g @qwen-code/qwen-code@0.23.3 | tee "$LOG"\n'
        'npm install -g qwen-code-webui@0.2.43 & echo "$LOG"\n',
        encoding="utf-8",
    )
    display = tmp_path / "display.sh"
    display.write_text('echo "$Runner" install "$PKGS"\n', encoding="utf-8")
    cmd_comments = tmp_path / "comments.cmd"
    cmd_comments.write_text(
        "REM npm install qwen-code-webui@latest\n"
        "@REM npm install qwen-code-webui@latest\n"
        "REM\tnpm install qwen-code-webui@latest\n"
        ":: npm install @qwen-code/qwen-code@latest\n",
        encoding="utf-8",
    )
    cmd_display = tmp_path / "display.cmd"
    cmd_display.write_text(
        "echo no-op ^& npm install -g qwen-code-webui\n" "@echo %Runner% install %PKGS%\n",
        encoding="utf-8",
    )
    ps_comment = tmp_path / "comment.ps1"
    ps_comment.write_text("<# npm install qwen-code-webui@latest #>\n", encoding="utf-8")
    ps_display = tmp_path / "display.ps1"
    ps_display.write_text("Write-Output $Runner install $PKGS\n", encoding="utf-8")
    npm_non_install_subcommands = tmp_path / "npm-non-install-subcommands.sh"
    npm_non_install_subcommands.write_text(
        "npm run install qwen-code-webui\n"
        "npm exec helper install qwen-code-webui\n"
        "npm help install qwen-code-webui\n"
        "npm view install qwen-code-webui\n"
        'echo "npm run install qwen-code-webui"\n',
        encoding="utf-8",
    )

    assert (
        _scan_unpinned_qwen_installs(
            [
                powershell,
                shell,
                display,
                cmd_comments,
                cmd_display,
                ps_comment,
                ps_display,
                npm_non_install_subcommands,
            ]
        )
        == []
    )


def test_repo_file_enumeration_works_without_git(tmp_path):
    """`git archive` snapshots carry no .git; the sweep must still enumerate
    the tree (PR #3386 R15 review: git ls-files exited 128 there)."""
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    (tmp_path / "sub" / "b.txt").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "c.js").write_text("x", encoding="utf-8")

    files = _iter_repo_files(tmp_path)

    names = {f.relative_to(tmp_path).as_posix() for f in files}
    assert names == {"a.txt", "sub/b.txt"}
