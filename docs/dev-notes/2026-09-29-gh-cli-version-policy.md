# gh CLI Version Policy (archived)

> **Archived 2026-09-29 (docs governance).** The original 499-line management
> plan was an unimplemented proposal (empty record tables, planned-but-absent
> caching, and a version-mismatch warning that does not exist in
> `docker-entrypoint.sh`). This file keeps only the durable facts. The pin is
> also documented in `CONTRIBUTING.md`.

## Current pin

- gh CLI **v2.42.1**, installed via the pinned `.deb` download in
  `Dockerfile` (see the `gh` install step).
- Why pinned: the autonomous pipeline's git-network retry behavior and the
  GitHub ops tests (`tests/unit/test_git_network_retry.py`,
  `tests/unit/test_github_ops.py`) are written against this version's
  behavior.

## Review cadence

- Quarterly, or immediately after a GitHub API change announcement.
- Bumping the pin is one commit: `Dockerfile` + `CONTRIBUTING.md` + the
  affected tests.

## Known exception (honest limitation)

The `Dockerfile` install step has a fallback path that installs `gh` from the
GitHub apt source **without a version pin** when the pinned `.deb` download
fails. In that fallback the "pinned version" guarantee does not hold, and no
runtime check warns about it (`docker-entrypoint.sh` only checks presence and
executability). Hardening this fallback is a follow-up, not a documented
promise.

## Archived original

The full original document (compatibility matrix templates, quarterly process
trees, announcement templates) is in git history:
`docs/GH_CLI_VERSION_COMPATIBILITY.md` as of 2026-09-29.
