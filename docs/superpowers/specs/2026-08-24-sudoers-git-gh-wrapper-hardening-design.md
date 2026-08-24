# Sudoers Git/GH Wrapper Hardening Design

## Goal

Close issue #2650 by replacing prefix-anchored sudoers entries for cross-user `git` and `gh` with root-owned wrappers that validate exact command shapes before executing the real tools.

## Background

Issue #2635 restored cross-user autonomous workflows by adding sudoers prefixes such as `git -c core.hooksPath=/dev/null *`, `git --git-dir=*`, `gh -R *`, and `gh api *`. Those entries are compatibility anchors, not security boundaries. Sudoers glob matching cannot express the command grammar needed to deny `git -c alias.*`, arbitrary destructive `git` verbs, or arbitrary `gh api -X DELETE` calls.

PR #2665 attempted wrapper-based hardening but was reverted because the implementation did not form a complete security boundary. This design keeps the valid direction while avoiding the observed failures: wrappers must be installed, `github_ops` must call them, sudoers must not retain direct `git`/`gh` wildcard entries, wrapper validation must happen before any passthrough, and tests must validate real emitted command shapes.

## Security Model

The service user keeps `(ALL)` runas for `GIT_SAFE` and `GH_SAFE`, but those aliases point only to `/usr/local/bin/openace-git *` and `/usr/local/bin/openace-gh *`.

The wrappers run as the selected sudo target user and execute the real `git` or `gh` binary only after validating argv. They do not provide privilege reduction beyond the existing sudo runas ceiling; they remove gratuitous direct `git`/`gh` RCE-shaped surface and make the policy expressible in code.

Wrapper scripts are root-owned executable files installed by Docker builds and package installs. Configuration is repo-shipped under `config/openace/` and installed read-only to `/etc/openace/`. Wrappers fail closed if a required config file is missing, malformed, not owned by root, group/world writable, or not a regular file.

## Git Wrapper

`openace-git` accepts only the global option shapes emitted by `github_ops._run_git`:

- zero or more safe `-c key=value` entries before the verb;
- optional `--git-dir=<path>` and `--work-tree=<path>` pair;
- optional `-C <path>`;
- a whitelisted git verb and constrained flags/positionals.

Allowed `-c` keys are explicit: `core.hooksPath=/dev/null`, `core.fsmonitor=false`, and `safe.directory=<path>`. Every real git command must include those hardening globals before the verb; only standalone `--version` and `--help` are exempt. `safe.directory` values must be absolute normalized paths under `/home`, `/workspace`, `/srv`, `/tmp`, `/private/tmp`, or the configured `allowed_path_roots`, and runtime config requires those context paths to exist and be owned by the current sudo target user. It rejects `alias.*`, `protocol.*`, `filter.*`, `core.sshCommand`, `core.editor`, `core.pager`, `core.fsmonitor` values other than `false`, `--exec-path`, and unknown global options.

Path operands for `--git-dir=<path>`, `--work-tree=<path>`, and `-C <path>` must be absolute normalized paths under the same allowed roots and, in runtime config, owned by the current sudo target user. Relative command operands are allowed only for repo-relative command parameters because `github_ops` intentionally passes paths to commands such as `git reset -q HEAD -- <path>`, but they must be plain relative paths: not absolute, no empty component, no `.` or `..` component, no NUL, and after normalization they must resolve under the trusted work tree from `--work-tree`/`-C` or under a configured allowed root. Absolute command operands must satisfy the allowed-root predicate directly.

Ref-like operands must match `^[A-Za-z0-9._/@{}^:+~,-]+$` and must not start with `-`. Repo slugs must match `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`; GHES repo slugs may include one hostname segment matching `^[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$`. Mutating branch operands must match `^(auto-dev|review-fix|ci-repair)/[A-Za-z0-9._/-]+$`; this applies to branch creation, branch deletion, branch push/delete, worktree branch creation, and force-with-lease. Read-only branch/ref operands use the broader ref predicate.

The verb allowlist is derived from current `github_ops` usage and must be exhaustive for current call sites. Destructive verbs are allowed only where the code already uses them and with exact constrained shapes, for example `reset --hard HEAD`, `reset --hard <trusted-ref>`, and `reset -q HEAD -- <path>`. `clean`, `filter-branch`, and `gc` remain denied. Force push is limited to `--force-with-lease` on the branch patterns above; raw `--force` is denied.

The canonical current git command inventory is:

| Verb | Allowed shapes from `github_ops` |
| --- | --- |
| `remote` | `remote get-url origin`; `remote add <name> <url>` |
| `push` | `push -u origin <workflow-branch>`; `push origin --delete <workflow-branch>`; `push <remote> <workflow-branch>`; `push <remote> <workflow-branch> --force-with-lease` |
| `branch` | `branch --show-current`; `branch -D <workflow-branch>` |
| `rev-parse` | `rev-parse HEAD`; `rev-parse --verify <ref>` |
| `checkout` | `checkout <ref>`; `checkout -b <workflow-branch>`; `checkout -b <workflow-branch> <base-ref>` |
| `show-ref` | `show-ref --verify --quiet refs/heads/<workflow-branch>` |
| `reset` | `reset --hard HEAD`; `reset --hard <ref>`; `reset -q HEAD -- <path>` |
| `ls-remote` | `ls-remote origin <branch>` |
| `worktree` | `worktree add -b <workflow-branch> <path> <base-ref>`; `worktree add <path> <ref>`; `worktree remove <path> --force`; `worktree list --porcelain -z` |
| `symbolic-ref` | `symbolic-ref --short HEAD` |
| `cat-file` | `cat-file -e <commit>^{commit}` |
| `fetch` | `fetch --no-tags origin <ref>`; `fetch <remote> <target>` |
| `diff` | `diff <base> <head>`; `diff --numstat <base> <head>`; `diff --name-only <base> <head>`; `diff -M --name-status <base> <head>`; `diff --name-only --diff-filter=U`; `diff --name-only`; `diff --cached --name-only` |
| `rev-list` | `rev-list --count <base>..<head>` |
| `log` | `log --full-history --format=%H <base>..<head> -- <path>` |
| `show` | `show --format= <sha>`; `show --numstat --format= <sha>`; `show --name-only --format= <sha>` |
| `status` | `status --porcelain` |
| `ls-files` | `ls-files --others --exclude-standard`; `ls-files --stage -z` |
| `grep` | `grep --no-index -l -I -E -e <fixed-conflict-marker-regex> -e <fixed-conflict-marker-regex> -e <fixed-conflict-marker-regex> -- <path>...` |
| `add` | `add -A` |
| `rm` | `rm -r --cached --ignore-unmatch .worktrees` |
| `commit` | `commit -m <message>`; `commit -m <message> --no-verify` |
| `init` | `init` |

The wrapper allows `--version` and `--help` only as exact single-argument commands. Any mixed passthrough such as `-c alias.x=!sh x --version` is denied before execution.

## GH Wrapper

`openace-gh` accepts the command shapes emitted by `github_ops._run_gh`:

- optional `-R owner/repo` or `-R host/owner/repo`;
- optional `--hostname <host>` for GHES API calls;
- whitelisted `repo`, `issue`, `pr`, `run`, and `api` subcommands with per-subcommand flag validation.

`gh api` is restricted by method and path. Read-only `GET` is the default method. `DELETE`, `PUT`, and `PATCH` are denied unless a future task adds an explicit path-specific need. `POST` is allowed only for the existing PR-create API path `repos/<owner>/<repo>/pulls`. Job log, ruleset, user, pull-comment, and issue-comment read paths are allowed.

`gh pr merge --admin` is controlled only by root-owned `/etc/openace/gh-wrapper.json` field `allow_admin_merge`. The wrapper must not trust `OPENACE_ALLOW_ADMIN_MERGE` from sudo environment. Deployment code may generate the root-owned config from operator-provided environment before dropping privileges, but runtime wrapper authorization reads the file only.

The canonical current gh command inventory is:

| Command | Allowed shapes from `github_ops` |
| --- | --- |
| `repo view` | `repo view --json nameWithOwner` |
| `repo create` | `repo create <name> --private [--description <text>]`; `repo create <name> --public [--description <text>]` |
| `issue create` | `issue create --title <title> --body <body> [--label <label>]... [--repo <owner/repo>]` |
| `issue comment` | `issue comment <number> --body <body>` |
| `issue close` | `issue close <number>` |
| `issue reopen` | `issue reopen <number>` |
| `issue edit` | `issue edit <number> [--title <title>] [--body <body>]`, requiring at least one edited field |
| `issue view` | `issue view <number> --json number,title,body,url,state,labels,comments`; `issue view <number> --comments --json comments`; `issue view <number> --json state,closedAt` |
| `pr close` | `pr close <number>` |
| `pr reopen` | `pr reopen <number>` |
| `pr comment` | `pr comment <number> --body <body>` |
| `pr create` | `pr create --title <title> --body <body> --base <ref> [--head <workflow-branch>] [--draft]` |
| `pr list` | `pr list --head <workflow-branch> --base main --state open --json number,url,title --limit 1` |
| `pr view` | `pr view <number> --json number,title,body,url,state,headRefName,baseRefName,additions,deletions,changedFiles,commits`; `pr view <number> --json commits`; `pr view <number> --json mergeCommit --jq .mergeCommit.oid` |
| `pr checks` | `pr checks <number> --json name,state,bucket,link`; later check data may include `head_sha` from adjacent parsing |
| `pr diff` | `pr diff <number>` |
| `pr merge` | `pr merge <number> --merge`; `pr merge <number> --squash`; `pr merge <number> --rebase`; optional `--auto`; optional `--admin` only when root-owned config allows it; exactly one strategy flag is required |
| `run list` | `run list --commit <sha> --json databaseId,name --limit 30` |
| `run view` | `run view <run-id> --log-failed [--allow-escape-sequences]`; `run view <run-id> --job <job-id> --log-failed [--allow-escape-sequences]` |
| `api` | `api [--hostname <host>] user --jq .login`; `api [--hostname <host>] --method POST repos/<owner>/<repo>/pulls -f title=<text> -f base=<ref> -f body=<text> [-f head=<workflow-branch>] [-f draft=true]`; `api [--hostname <host>] repos/<owner>/<repo>/pulls/<number>`; `api [--hostname <host>] repos/<owner>/<repo>/commits/<sha>`; `api [--hostname <host>] repos/<owner>/<repo>/branches/<branch>/protection`; `api [--hostname <host>] --paginate repos/<owner>/<repo>/rules/branches/<branch>`; `api [--hostname <host>] [--paginate] repos/<owner>/<repo>/issues/<number>/comments --jq <fixed-issue-comment-filter>`; `api [--hostname <host>] [--paginate] repos/<owner>/<repo>/issues/<number>/comments --jq <fixed-paginated-issue-comment-filter>`; `api [--hostname <host>] repos/<owner>/<repo>/issues/<number>/timeline --jq <fixed-closure-filter>` with `--paginate`; `api [--hostname <host>] repos/<owner>/<repo>/pulls/<number>/comments --jq <fixed-review-comment-filter>`; `api [--hostname <host>] repos/<owner>/<repo>/commits/<sha>/status`; `api [--hostname <host>] repos/<owner>/<repo>/commits/<sha>/check-runs`; `api [--hostname <host>] repos/<owner>/<repo>/actions/jobs/<job-id>/logs [--allow-escape-sequences]` |

Like `openace-git`, `openace-gh` allows `--version` and `--help` only as exact single-argument commands.

## Deployment Paths

Dockerfile copies wrappers to `/usr/local/bin` and config files to `/etc/openace`, sets wrapper mode `0755`, config mode `0644`, and root ownership. The package installer uses exact install commands equivalent to:

```bash
install -o root -g root -m 0755 "$install_dir/scripts/openace-git.py" /usr/local/bin/openace-git
install -o root -g root -m 0755 "$install_dir/scripts/openace-gh.py" /usr/local/bin/openace-gh
install -d -o root -g root -m 0755 /etc/openace
install -o root -g root -m 0644 "$install_dir/config/openace/git-wrapper.json" /etc/openace/git-wrapper.json
install -o root -g root -m 0644 "$install_dir/config/openace/gh-wrapper.json" /etc/openace/gh-wrapper.json
```

`docker-entrypoint.sh`, `scripts/generate-sudoers.sh`, and `scripts/install-central/package-method/install.sh` all generate:

```sudoers
Cmnd_Alias GIT_SAFE = /usr/local/bin/openace-git *
Cmnd_Alias GH_SAFE = /usr/local/bin/openace-gh *
```

They must not include direct `${GIT_PATH}` or `${GH_PATH}` entries in `GIT_SAFE` or `GH_SAFE`.

The package installer installs wrapper files and config before generating sudoers. Existing `OPENACE_UTILS` must remain defined where user rules reference it.

## Testing Requirements

Tests must cover the five critical failures from PR #2665:

- Docker sudoers must not retain direct `gh -R *` or `gh api *` after the wrapper entry.
- `--version` and `--help` passthrough must not execute when mixed with other arguments.
- Declared wrapper constraints must be enforced, including force-with-lease branch patterns and per-subcommand `gh` flags.
- Wrappers must be installed and `github_ops` sudo paths must call them.
- Package sudoers must still define aliases referenced by user rules, including `OPENACE_UTILS`.

Tests must validate every command shape in the canonical current `github_ops` inventories above. Known-dangerous command shapes must be rejected.

## Non-Goals

This change does not remove the existing root-runas wrappers for other operations, redesign autonomous workflow permissions, or make `git`/`gh` commands safe outside the wrapper path. It only replaces the unsafe sudoers compatibility anchors for cross-user git and gh execution.
