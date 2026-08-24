# Sudoers Git/GH Wrapper Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace direct cross-user `git`/`gh` sudoers entries with installed validating wrappers and prove the five PR #2665 critical failures cannot recur.

**Architecture:** Add focused Python wrappers with pure validation helpers and thin execution entrypoints. Rewire only sudo paths in `github_ops`, then make every sudoers generator authorize only the wrapper paths.

**Tech Stack:** Python 3.11+, pytest, bash sudoers generators, Dockerfile install steps, GitHub CLI and git command-line semantics.

**Spec:** `docs/superpowers/specs/2026-08-24-sudoers-git-gh-wrapper-hardening-design.md`

## Global Constraints

- Use TDD: write failing tests before production code.
- Sudoers `GIT_SAFE` and `GH_SAFE` must contain only `/usr/local/bin/openace-git *` and `/usr/local/bin/openace-gh *`.
- Wrapper validation must run before any call to the real `git` or `gh`.
- `github_ops` sudo paths must call wrapper paths; non-sudo paths may keep direct `git`/`gh`.
- Docker and package installs must install wrappers and config before generating sudoers.
- Package sudoers output must define every `Cmnd_Alias` referenced by user rules.
- Wrapper config loading must fail closed when config is missing, malformed, non-root-owned, group/world writable, or not a regular file. Unit tests may inject an in-memory config object to avoid root ownership requirements.
- `gh pr merge --admin` authorization must come from root-owned `gh-wrapper.json`, not from sudo-preserved environment.
- Tests must cover every git and gh shape listed in the spec's canonical command inventories.
- Independent implementation and PR review must reach zero blocking or important findings before user review.

---

### Task 1: Red Tests For Reverted #2665 Criticals

**Files:**
- Modify: `tests/unit/test_sudoers_hardening.py`
- Create: `tests/unit/test_openace_git_wrapper.py`
- Create: `tests/unit/test_openace_gh_wrapper.py`

**Interfaces:**
- Produces tests that import wrappers from `scripts/openace-git.py` and `scripts/openace-gh.py` via `importlib.util.spec_from_file_location`.
- Produces sudoers assertions consumed by later tasks.

- [ ] **Step 1: Write failing sudoers and wiring tests**

Add assertions that:

```python
def test_git_safe_uses_only_wrapper_entries_in_all_generators(...):
    assert _extract_cmnd_alias(text, "GIT_SAFE") == ["/usr/local/bin/openace-git *"]

def test_gh_safe_uses_only_wrapper_entries_in_all_generators(...):
    assert _extract_cmnd_alias(text, "GH_SAFE") == ["/usr/local/bin/openace-gh *"]

def test_docker_gh_safe_has_no_line_continuation_tail():
    assert "${GH_PATH} -R *" not in _extract_cmnd_alias(DOCKER_ENTRYPOINT.read_text(), "GH_SAFE")
    assert "${GH_PATH} api *" not in _extract_cmnd_alias(DOCKER_ENTRYPOINT.read_text(), "GH_SAFE")

def test_github_ops_sudo_paths_call_wrappers():
    text = GITHUB_OPS_PY.read_text()
    assert '"/usr/local/bin/openace-git"' in text
    assert '"/usr/local/bin/openace-gh"' in text
```

Also assert `Dockerfile` and package install source mention `openace-git`, `openace-gh`, and `config/openace`.

- [ ] **Step 2: Write failing wrapper security tests**

Add tests for:

```python
assert not validate_git_argv(["-c", "alias.pwn=!id", "pwn", "--version"]).allowed
assert validate_git_argv(["--version"]).allowed
assert not validate_git_argv(["--exec-path=/tmp/x", "status"]).allowed
assert not validate_git_argv(["-c", "protocol.ext.allow=always", "fetch", "origin"]).allowed
assert not validate_git_argv(["push", "origin", "main", "--force"]).allowed
assert not validate_git_argv(["push", "origin", "main", "--force-with-lease"]).allowed
assert not validate_git_argv(["branch", "-D", "main"]).allowed
assert not validate_git_argv(["checkout", "-b", "main"]).allowed
assert not validate_git_argv(["reset", "-q", "HEAD", "--", "../secret"]).allowed
assert not validate_git_argv(["grep", "--no-index", "-l", "-I", "-E", "-e", r"^<{7,}( |$)", "-e", r"^={7,}$", "-e", r"^>{7,}( |$)", "--", "a/../b"]).allowed
assert validate_git_argv(["push", "origin", "auto-dev/abc", "--force-with-lease"]).allowed
```

Add an exhaustive accepted-shapes table matching the spec's canonical git command inventory:

```python
@pytest.mark.parametrize("argv", [
    ["remote", "get-url", "origin"],
    ["remote", "add", "origin", "https://github.com/open-ace/open-ace.git"],
    ["push", "-u", "origin", "auto-dev/abc"],
    ["push", "origin", "--delete", "auto-dev/abc"],
    ["push", "origin", "auto-dev/abc", "--force-with-lease"],
	    ["branch", "--show-current"],
	    ["branch", "-D", "auto-dev/abc"],
	    ["rev-parse", "HEAD"],
	    ["rev-parse", "--verify", "origin/main^{commit}"],
	    ["checkout", "origin/main"],
	    ["checkout", "-b", "auto-dev/abc"],
	    ["checkout", "-b", "auto-dev/abc", "origin/main"],
	    ["show-ref", "--verify", "--quiet", "refs/heads/auto-dev/abc"],
	    ["reset", "--hard", "HEAD"],
	    ["reset", "--hard", "origin/main"],
	    ["reset", "-q", "HEAD", "--", "app/file.py"],
    ["ls-remote", "origin", "main"],
    ["worktree", "add", "-b", "auto-dev/abc", "/tmp/wt", "origin/main"],
    ["worktree", "add", "/tmp/wt", "auto-dev/abc"],
    ["worktree", "remove", "/tmp/wt", "--force"],
    ["worktree", "list", "--porcelain", "-z"],
    ["symbolic-ref", "--short", "HEAD"],
    ["cat-file", "-e", "abc123^{commit}"],
    ["fetch", "--no-tags", "origin", "abc123"],
    ["fetch", "origin", "main"],
    ["diff", "HEAD~1", "HEAD"],
    ["diff", "--numstat", "HEAD~1", "HEAD"],
    ["diff", "--name-only", "HEAD~1", "HEAD"],
    ["diff", "-M", "--name-status", "HEAD~1", "HEAD"],
    ["diff", "--name-only", "--diff-filter=U"],
    ["diff", "--name-only"],
	    ["diff", "--cached", "--name-only"],
	    ["rev-list", "--count", "HEAD~1..HEAD"],
	    ["log", "--full-history", "--format=%H", "HEAD~1..HEAD", "--", "app/file.py"],
	    ["show", "--format=", "HEAD"],
	    ["show", "--numstat", "--format=", "HEAD"],
	    ["show", "--name-only", "--format=", "HEAD"],
	    ["status", "--porcelain"],
	    ["ls-files", "--others", "--exclude-standard"],
	    ["ls-files", "--stage", "-z"],
	    [
	        "grep", "--no-index", "-l", "-I", "-E",
	        "-e", r"^<{7,}( |$)",
	        "-e", r"^={7,}$",
	        "-e", r"^>{7,}( |$)",
	        "--", "app/file.py",
	    ],
	    ["add", "-A"],
	    ["rm", "-r", "--cached", "--ignore-unmatch", ".worktrees"],
	    ["commit", "-m", "message"],
	    ["commit", "-m", "message", "--no-verify"],
	    ["init"],
	])
def test_current_github_ops_git_shapes_are_allowed(argv):
    assert validate_git_argv(_sudo_git_prefix() + argv, config=test_config()).allowed
```

For `gh`:

```python
assert not validate_gh_argv(["repo", "delete", "owner/repo"]).allowed
assert not validate_gh_argv(["api", "-X", "DELETE", "repos/owner/repo"]).allowed
assert not validate_gh_argv(["pr", "merge", "1", "--admin"]).allowed
assert validate_gh_argv(["pr", "merge", "1", "--admin"], config=test_config(allow_admin_merge=True)).allowed
assert not validate_gh_argv(["pr", "view", "1", "--web"]).allowed
assert validate_gh_argv(["run", "view", "123", "--log-failed", "--allow-escape-sequences"]).allowed
assert not validate_gh_argv(["api", "repos/owner/repo/issues/1/comments", "--jq", ".[]"]).allowed
assert not validate_gh_argv(["pr", "list", "--head", "main", "--base", "main", "--state", "open", "--json", "number,url,title", "--limit", "1"]).allowed
```

Add an exhaustive accepted-shapes table matching the spec's canonical gh command inventory, including repo-scoped `-R owner/repo` and non-repo-scoped `api` forms:

```python
@pytest.mark.parametrize("argv", [
    ["repo", "view", "--json", "nameWithOwner"],
    ["repo", "create", "new-repo", "--private", "--description", "desc"],
    ["-R", "owner/repo", "issue", "create", "--title", "title", "--body", "body", "--label", "bug", "--repo", "owner/repo"],
    ["-R", "owner/repo", "issue", "comment", "1", "--body", "body"],
    ["-R", "owner/repo", "issue", "close", "1"],
    ["-R", "owner/repo", "issue", "reopen", "1"],
    ["-R", "owner/repo", "issue", "edit", "1", "--title", "title"],
    ["-R", "owner/repo", "issue", "view", "1", "--json", "number,title,body,url,state,labels,comments"],
    ["-R", "owner/repo", "issue", "view", "1", "--comments", "--json", "comments"],
    ["-R", "owner/repo", "issue", "view", "1", "--json", "state,closedAt"],
    ["-R", "owner/repo", "pr", "close", "1"],
    ["-R", "owner/repo", "pr", "reopen", "1"],
    ["-R", "owner/repo", "pr", "comment", "1", "--body", "body"],
    ["-R", "owner/repo", "pr", "create", "--title", "title", "--body", "body", "--base", "main", "--head", "auto-dev/abc", "--draft"],
    ["-R", "owner/repo", "pr", "list", "--head", "auto-dev/abc", "--base", "main", "--state", "open", "--json", "number,url,title", "--limit", "1"],
    ["-R", "owner/repo", "pr", "view", "1", "--json", "number,title,body,url,state,headRefName,baseRefName,additions,deletions,changedFiles,commits"],
    ["-R", "owner/repo", "pr", "view", "1", "--json", "commits"],
    ["-R", "owner/repo", "pr", "view", "1", "--json", "mergeCommit", "--jq", ".mergeCommit.oid"],
    ["-R", "owner/repo", "pr", "checks", "1", "--json", "name,state,bucket,link"],
    ["-R", "owner/repo", "pr", "diff", "1"],
    ["-R", "owner/repo", "pr", "merge", "1", "--merge", "--auto"],
    ["-R", "owner/repo", "run", "list", "--commit", "a" * 40, "--json", "databaseId,name", "--limit", "30"],
    ["-R", "owner/repo", "run", "view", "123", "--log-failed", "--allow-escape-sequences"],
    ["-R", "owner/repo", "run", "view", "123", "--job", "456", "--log-failed", "--allow-escape-sequences"],
    ["api", "user", "--jq", ".login"],
    ["api", "--method", "POST", "repos/owner/repo/pulls", "-f", "title=title", "-f", "base=main", "-f", "body=body", "-f", "head=auto-dev/abc"],
    ["api", "repos/owner/repo/pulls/1"],
    ["api", "repos/owner/repo/commits/" + "a" * 40],
    ["api", "repos/owner/repo/branches/main/protection"],
    ["api", "--paginate", "repos/owner/repo/rules/branches/main"],
    ["api", "repos/owner/repo/issues/1/comments", "--jq", FIXED_ISSUE_COMMENT_FILTER],
    ["api", "--paginate", "repos/owner/repo/issues/1/comments", "--jq", FIXED_PAGINATED_ISSUE_COMMENT_FILTER],
    ["api", "--paginate", "repos/owner/repo/issues/1/timeline", "--jq", FIXED_CLOSURE_FILTER],
    ["api", "repos/owner/repo/pulls/1/comments", "--jq", FIXED_REVIEW_COMMENT_FILTER],
    ["api", "repos/owner/repo/commits/" + "a" * 40 + "/status"],
    ["api", "repos/owner/repo/commits/" + "a" * 40 + "/check-runs"],
    ["api", "repos/owner/repo/actions/jobs/456/logs", "--allow-escape-sequences"],
    ["api", "--hostname", "gh.example.com", "repos/owner/repo/actions/jobs/456/logs"],
])
def test_current_github_ops_gh_shapes_are_allowed(argv):
    assert validate_gh_argv(argv, config=test_config()).allowed
```

Add config fail-closed tests:

```python
def test_missing_config_file_fails_closed(tmp_path):
    assert load_git_config(tmp_path / "missing.json").allowed is False

def test_group_writable_config_fails_closed(tmp_path):
    path = tmp_path / "git-wrapper.json"
    path.write_text("{}", encoding="utf-8")
    path.chmod(0o664)
    assert load_git_config(path).allowed is False
```

- [ ] **Step 3: Run tests to verify RED**

Run:

```bash
python -m pytest tests/unit/test_sudoers_hardening.py tests/unit/test_openace_git_wrapper.py tests/unit/test_openace_gh_wrapper.py -q
```

Expected: failures because wrappers do not exist and sudoers still contain direct git/gh entries.

### Task 2: Implement Git Wrapper

**Files:**
- Create: `scripts/openace-git.py`
- Create: `config/openace/git-wrapper.json`
- Modify: `tests/unit/test_openace_git_wrapper.py`

**Interfaces:**
- Produces `ValidationResult(allowed: bool, reason: str = "")`.
- Produces `validate_git_argv(argv: list[str], config: GitWrapperConfig | None = None) -> ValidationResult`.
- Produces executable wrapper entrypoint `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Implement minimal validator**

Parse global options before the verb. Allow exact `--version` and `--help` only when `argv` has length 1. Reject unknown globals, `--exec-path`, forbidden `-c` keys, unsafe `core.*` values, and forbidden verbs.

- [ ] **Step 2: Add git config**

Use JSON, not YAML, to avoid optional parser fallback risk. Include allowed verbs and exact shape rules for every command in the spec's canonical git inventory. Include `allowed_path_roots`, `force_with_lease_branch_patterns`, and safe `-c` keys.

- [ ] **Step 3: Run git wrapper tests**

Run:

```bash
python -m pytest tests/unit/test_openace_git_wrapper.py -q
```

Expected: pass.

### Task 3: Implement GH Wrapper

**Files:**
- Create: `scripts/openace-gh.py`
- Create: `config/openace/gh-wrapper.json`
- Modify: `tests/unit/test_openace_gh_wrapper.py`

**Interfaces:**
- Produces `validate_gh_argv(argv: list[str], config: GhWrapperConfig | None = None) -> ValidationResult`.
- Produces executable wrapper entrypoint `main(argv: list[str] | None = None) -> int`.

- [ ] **Step 1: Implement command and flag validation**

Parse optional `-R` and optional API `--hostname`. Validate command/subcommand pairs and flags from JSON config. Allow exact `--version` and `--help` only when standalone. Read `allow_admin_merge` only from the loaded root-owned config.

- [ ] **Step 2: Implement API path/method validation**

Default method is `GET`. Deny `DELETE`, `PUT`, and `PATCH`. Allow only current needed REST path patterns, including `user`, branch rules, PR creation, PR/issue comments, and Actions job logs.

- [ ] **Step 3: Run gh wrapper tests**

Run:

```bash
python -m pytest tests/unit/test_openace_gh_wrapper.py -q
```

Expected: pass.

### Task 4: Wire Wrappers Into Runtime And Install Paths

**Files:**
- Modify: `app/modules/workspace/autonomous/github_ops.py`
- Modify: `Dockerfile`
- Modify: `docker-entrypoint.sh`
- Modify: `scripts/generate-sudoers.sh`
- Modify: `scripts/install-central/package-method/install.sh`
- Modify: `tests/unit/test_sudoers_hardening.py`

**Interfaces:**
- `github_ops._run_git` sudo path uses `/usr/local/bin/openace-git`.
- `github_ops._run_gh` sudo path uses `/usr/local/bin/openace-gh`.
- Sudoers aliases authorize wrapper paths only.

- [ ] **Step 1: Update `github_ops` sudo command construction**

Replace sudo-path `git_bin` with `/usr/local/bin/openace-git` and sudo-path `gh` with `/usr/local/bin/openace-gh`. Preserve non-sudo direct command behavior.

- [ ] **Step 2: Update Dockerfile install**

Copy wrapper scripts and `config/openace/*.json` into `/usr/local/bin` and `/etc/openace`, with root ownership and modes `0755` for scripts and `0644` for config. The image installs `scripts/openace-git.py` as `/usr/local/bin/openace-git` and `scripts/openace-gh.py` as `/usr/local/bin/openace-gh`.

- [ ] **Step 3: Update package install**

Add `install_git_gh_wrappers "$install_dir"` before `configure_sudoers`. It must run:

```bash
install -o root -g root -m 0755 "$install_dir/scripts/openace-git.py" /usr/local/bin/openace-git
install -o root -g root -m 0755 "$install_dir/scripts/openace-gh.py" /usr/local/bin/openace-gh
install -d -o root -g root -m 0755 /etc/openace
install -o root -g root -m 0644 "$install_dir/config/openace/git-wrapper.json" /etc/openace/git-wrapper.json
install -o root -g root -m 0644 "$install_dir/config/openace/gh-wrapper.json" /etc/openace/gh-wrapper.json
```

- [ ] **Step 4: Update sudoers generators**

Replace direct `git`/`gh` alias bodies with wrapper-only aliases in all three sudoers paths. Keep `OPENACE_UTILS`, `MKDIR_SAFE`, and existing root-runas wrapper rules intact.

- [ ] **Step 5: Run sudoers tests**

Run:

```bash
python -m pytest tests/unit/test_sudoers_hardening.py -q
```

Expected: pass.

### Task 5: Full Targeted Verification And Independent Reviews

**Files:**
- No production files unless review feedback requires fixes.

**Interfaces:**
- Produces PR for user review.

- [ ] **Step 1: Run targeted verification**

Run:

```bash
python -m pytest tests/unit/test_openace_git_wrapper.py tests/unit/test_openace_gh_wrapper.py tests/unit/test_sudoers_hardening.py tests/issues/716/test_github_ops_sudo.py -q
bash -n scripts/generate-sudoers.sh docker-entrypoint.sh scripts/install-central/package-method/install.sh
python -m py_compile scripts/openace-git.py scripts/openace-gh.py app/modules/workspace/autonomous/github_ops.py
```

- [ ] **Step 2: Request independent implementation review**

Ask an independent agent to review the diff against this plan and the spec. Fix every Critical and Important finding; repeat until there are zero.

- [ ] **Step 3: Create PR**

Push branch and create PR referencing issue #2650. Do not merge.

- [ ] **Step 4: Request independent PR review**

Ask an independent agent to review the pushed PR from GitHub-visible state. Fix every Critical and Important finding; repeat until there are zero.

- [ ] **Step 5: Stop for user review**

Report PR URL, verification commands, independent review status, and remaining risks. Wait for user review.
