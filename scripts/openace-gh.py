#!/usr/bin/env python3
"""Validating sudo wrapper for gh commands used by Open ACE."""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any

CONFIG_PATH = Path("/etc/openace/gh-wrapper.json")

DEFAULT_CONFIG: dict[str, Any] = {
    "allow_admin_merge": False,
    "workflow_branch_patterns": [r"^(auto-dev|review-fix|ci-repair)/[A-Za-z0-9._/-]+$"],
    "real_gh_paths": ["/usr/bin/gh", "/usr/local/bin/gh"],
    "fixed_jq_filters": {
        "issue_comment": ".[] | {id, body, createdAt: .created_at, author: {login: .user.login}}",
        "paginated_issue_comment": ".[] | {id, body, created_at, user: .user.login}",
        "closure": '.[] | select(.event == "closed") | {closed_at: .created_at, closer_login: .actor.login}',
        "review_comment": ".[] | {id, path, body, line, created_at, user: .user.login}",
    },
}

REPO_RE = re.compile(r"^(?:[A-Za-z0-9.-]+/)?[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
HOST_RE = re.compile(r"^[A-Za-z0-9.-]+$")
SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RE = re.compile(r"^[A-Za-z0-9._/@{}^:+~,-]+$")
SHA_RE = re.compile(r"^[0-9a-fA-F]{7,40}$")
NUMBER_RE = re.compile(r"^\d+$")
NAME_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


class ValidationResult:
    def __init__(self, allowed: bool, reason: str = "", config: dict[str, Any] | None = None):
        self.allowed = allowed
        self.reason = reason
        self.config = config


def _allow() -> ValidationResult:
    return ValidationResult(True)


def _deny(reason: str) -> ValidationResult:
    return ValidationResult(False, reason)


def _config(config: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(DEFAULT_CONFIG)
    merged["fixed_jq_filters"] = dict(DEFAULT_CONFIG["fixed_jq_filters"])
    if config:
        for key, value in config.items():
            if key == "fixed_jq_filters" and isinstance(value, dict):
                merged["fixed_jq_filters"].update(value)
            else:
                merged[key] = value
    return merged


def _number(value: str) -> bool:
    return bool(NUMBER_RE.fullmatch(value or ""))


def _sha(value: str) -> bool:
    return bool(SHA_RE.fullmatch(value or ""))


def _ref(value: str) -> bool:
    return bool(value) and not value.startswith("-") and bool(REF_RE.fullmatch(value))


def _branch(value: str, config: dict[str, Any]) -> bool:
    patterns = config.get("workflow_branch_patterns") or DEFAULT_CONFIG["workflow_branch_patterns"]
    return (
        bool(value)
        and not value.startswith("-")
        and any(re.fullmatch(pattern, value) for pattern in patterns)
    )


def _repo(value: str) -> bool:
    return bool(REPO_RE.fullmatch(value or ""))


def _slug(value: str) -> bool:
    return bool(SLUG_RE.fullmatch(value or ""))


def _host(value: str) -> bool:
    return bool(HOST_RE.fullmatch(value or ""))


def _real_gh_path(config: dict[str, Any]) -> str:
    configured = config.get("real_gh_paths") or DEFAULT_CONFIG["real_gh_paths"]
    candidates = configured if isinstance(configured, list) else DEFAULT_CONFIG["real_gh_paths"]
    for candidate in candidates:
        path = str(candidate)
        if path.startswith("/") and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    return str(DEFAULT_CONFIG["real_gh_paths"][0])


def _text(value: str) -> bool:
    return value is not None and "\0" not in value and not value.startswith("-")


def _parse_prefix(argv: list[str]) -> tuple[ValidationResult, list[str]]:
    args = list(argv)
    if len(args) >= 2 and args[0] in {"-R", "--repo"}:
        if not _repo(args[1]):
            return _deny("invalid repo"), []
        args = args[2:]
    elif args and args[0].startswith("--repo="):
        if not _repo(args[0].split("=", 1)[1]):
            return _deny("invalid repo"), []
        args = args[1:]
    return _allow(), args


def _consume_optional_description(args: list[str], start: int) -> int | None:
    if start == len(args):
        return start
    if start + 1 == len(args) - 1 and args[start] == "--description" and _text(args[start + 1]):
        return len(args)
    return None


def _validate_repo(args: list[str]) -> ValidationResult:
    if args == ["repo", "view", "--json", "nameWithOwner"]:
        return _allow()
    if len(args) >= 4 and args[:2] == ["repo", "create"] and NAME_RE.fullmatch(args[2] or ""):
        if args[3] not in {"--private", "--public"}:
            return _deny("repo create visibility required")
        end = _consume_optional_description(args, 4)
        if end == len(args):
            return _allow()
    return _deny("repo command is not allowed")


def _validate_issue(args: list[str]) -> ValidationResult:
    if len(args) < 2:
        return _deny("missing issue subcommand")
    sub = args[1]
    if sub == "create":
        i = 2
        seen_title = seen_body = False
        while i < len(args):
            if i + 1 >= len(args):
                return _deny("issue create flag missing value")
            flag, value = args[i], args[i + 1]
            if flag == "--title" and _text(value):
                seen_title = True
            elif flag == "--body" and value is not None and "\0" not in value:
                seen_body = True
            elif (flag == "--label" and _text(value)) or (flag == "--repo" and _slug(value)):
                pass
            else:
                return _deny("issue create flag is not allowed")
            i += 2
        return _allow() if seen_title and seen_body else _deny("issue create needs title and body")
    if (
        sub in {"comment"}
        and len(args) == 5
        and _number(args[2])
        and args[3] == "--body"
        and "\0" not in args[4]
    ):
        return _allow()
    if sub in {"close", "reopen"} and len(args) == 3 and _number(args[2]):
        return _allow()
    if sub == "edit" and len(args) in {5, 7} and _number(args[2]):
        i = 3
        seen = set()
        while i < len(args):
            if i + 1 >= len(args):
                return _deny("issue edit flag is not allowed")
            flag, value = args[i], args[i + 1]
            if (
                flag == "--title"
                and _text(value)
                or flag == "--body"
                and value is not None
                and "\0" not in value
            ):
                pass
            else:
                return _deny("issue edit flag is not allowed")
            seen.add(args[i])
            i += 2
        return _allow() if seen else _deny("issue edit needs a field")
    if sub == "view":
        if (
            len(args) == 5
            and _number(args[2])
            and args[3:]
            in (
                ["--json", "number,title,body,url,state,labels,comments"],
                ["--json", "state,closedAt"],
            )
        ):
            return _allow()
        if len(args) == 6 and _number(args[2]) and args[3:] == ["--comments", "--json", "comments"]:
            return _allow()
    return _deny("issue command is not allowed")


def _validate_pr(args: list[str], config: dict[str, Any]) -> ValidationResult:
    if len(args) < 2:
        return _deny("missing pr subcommand")
    sub = args[1]
    if sub in {"close", "reopen"} and len(args) == 3 and _number(args[2]):
        return _allow()
    if (
        sub == "comment"
        and len(args) == 5
        and _number(args[2])
        and args[3] == "--body"
        and "\0" not in args[4]
    ):
        return _allow()
    if sub == "create":
        i = 2
        seen_title = seen_body = seen_base = False
        while i < len(args):
            flag = args[i]
            if flag == "--draft":
                i += 1
                continue
            if i + 1 >= len(args):
                return _deny("pr create flag missing value")
            value = args[i + 1]
            if flag == "--title" and _text(value):
                seen_title = True
            elif flag == "--body" and value is not None and "\0" not in value:
                seen_body = True
            elif flag == "--base" and _ref(value):
                seen_base = True
            elif flag == "--head" and _branch(value, config):
                pass
            else:
                return _deny("pr create flag is not allowed")
            i += 2
        return (
            _allow() if seen_title and seen_body and seen_base else _deny("pr create missing field")
        )
    if sub == "list":
        if (
            len(args) == 12
            and args[2] == "--head"
            and _branch(args[3], config)
            and args[4:]
            == ["--base", "main", "--state", "open", "--json", "number,url,title", "--limit", "1"]
        ):
            return _allow()
    if sub == "view":
        if (
            len(args) == 5
            and _number(args[2])
            and args[3:]
            in (
                [
                    "--json",
                    "number,title,body,url,state,headRefName,baseRefName,additions,deletions,changedFiles,commits",
                ],
                ["--json", "commits"],
            )
        ):
            return _allow()
        if (
            len(args) == 7
            and _number(args[2])
            and args[3:] == ["--json", "mergeCommit", "--jq", ".mergeCommit.oid"]
        ):
            return _allow()
    if (
        sub == "checks"
        and len(args) == 5
        and _number(args[2])
        and args[3:] == ["--json", "name,state,bucket,link"]
    ):
        return _allow()
    if sub == "diff" and len(args) == 3 and _number(args[2]):
        return _allow()
    if sub == "merge" and len(args) >= 4 and _number(args[2]):
        flags = args[3:]
        strategies = [flag for flag in flags if flag in {"--merge", "--squash", "--rebase"}]
        if len(strategies) != 1:
            return _deny("exactly one merge strategy is required")
        allowed = {"--merge", "--squash", "--rebase", "--auto"}
        if config.get("allow_admin_merge"):
            allowed.add("--admin")
        if all(flag in allowed for flag in flags):
            return _allow()
    return _deny("pr command is not allowed")


def _validate_run(args: list[str]) -> ValidationResult:
    if (
        len(args) == 8
        and args[:3] == ["run", "list", "--commit"]
        and _sha(args[3])
        and args[4:] == ["--json", "databaseId,name", "--limit", "30"]
    ):
        return _allow()
    if (
        len(args) in {4, 5}
        and args[:2] == ["run", "view"]
        and _number(args[2])
        and args[3] == "--log-failed"
    ):
        if len(args) == 4 or args[4] == "--allow-escape-sequences":
            return _allow()
    if (
        len(args) in {6, 7}
        and args[:2] == ["run", "view"]
        and _number(args[2])
        and args[3] == "--job"
        and _number(args[4])
        and args[5] == "--log-failed"
    ):
        if len(args) == 6 or args[6] == "--allow-escape-sequences":
            return _allow()
    return _deny("run command is not allowed")


def _parse_api_prefix(args: list[str]) -> tuple[ValidationResult, list[str]]:
    rest = args[1:]
    if len(rest) >= 2 and rest[0] == "--hostname":
        if not _host(rest[1]):
            return _deny("invalid hostname"), []
        rest = rest[2:]
    return _allow(), rest


def _repo_path(prefix: str, path: str) -> str | None:
    if not path.startswith("repos/"):
        return None
    rest = path[len("repos/") :]
    parts = rest.split("/")
    if len(parts) < 3:
        return None
    slug = "/".join(parts[:2])
    if not _slug(slug):
        return None
    tail = "/".join(parts[2:])
    return tail if tail.startswith(prefix) else None


def _tail_after(prefix: str, path: str) -> str | None:
    tail = _repo_path(prefix, path)
    if tail is None:
        return None
    return tail[len(prefix) :]


def _validate_api_fields(fields: list[str], config: dict[str, Any]) -> bool:
    if len(fields) % 2 != 0:
        return False
    seen = {"title": False, "base": False, "body": False}
    for flag, assignment in zip(fields[::2], fields[1::2], strict=True):
        if flag != "-f" or "=" not in assignment:
            return False
        key, value = assignment.split("=", 1)
        if key == "title" and _text(value):
            seen["title"] = True
        elif key == "base" and _ref(value):
            seen["base"] = True
        elif key == "body" and "\0" not in value:
            seen["body"] = True
        elif (key == "head" and _branch(value, config)) or (key == "draft" and value == "true"):
            pass
        else:
            return False
    return all(seen.values())


def _validate_api(args: list[str], config: dict[str, Any]) -> ValidationResult:
    parsed, rest = _parse_api_prefix(args)
    if not parsed.allowed:
        return parsed
    if rest == ["user", "--jq", ".login"]:
        return _allow()
    if len(rest) >= 4 and rest[:2] == ["--method", "POST"] and rest[2].startswith("repos/"):
        tail = _repo_path("pulls", rest[2])
        if tail == "pulls" and _validate_api_fields(rest[3:], config):
            return _allow()
        return _deny("POST api path is not allowed")
    if "--method" in rest or "-X" in rest:
        return _deny("api method is not allowed")
    paginate = False
    if rest and rest[0] == "--paginate":
        paginate = True
        rest = rest[1:]
    if not rest:
        return _deny("api path missing")
    path = rest[0]
    trailing = rest[1:]
    filters = config.get("fixed_jq_filters", {})

    if _repo_path("pulls/", path) and _number(path.rsplit("/", 1)[-1]) and not trailing:
        return _allow()
    commit_tail = _repo_path("commits/", path)
    if commit_tail:
        parts = commit_tail.split("/")
        if len(parts) == 2 and _sha(parts[1]) and not trailing:
            return _allow()
        if (
            len(parts) == 3
            and _sha(parts[1])
            and parts[2] in {"status", "check-runs"}
            and not trailing
        ):
            return _allow()
    branch_tail = _repo_path("branches/", path)
    if (
        branch_tail
        and branch_tail.endswith("/protection")
        and _ref(branch_tail[: -len("/protection")])
        and not paginate
        and not trailing
    ):
        return _allow()
    rules_tail = _repo_path("rules/branches/", path)
    if rules_tail and paginate and _ref(rules_tail[len("rules/branches/") :]) and not trailing:
        return _allow()
    issue_comments = _tail_after("issues/", path)
    if issue_comments:
        parts = issue_comments.split("/")
        if len(parts) == 2 and parts[1] == "comments" and _number(parts[0]):
            if trailing in (
                ["--jq", filters.get("issue_comment")],
                ["--jq", filters.get("paginated_issue_comment")],
            ):
                return _allow()
        if len(parts) == 2 and parts[1] == "timeline" and _number(parts[0]):
            if paginate and trailing == ["--jq", filters.get("closure")]:
                return _allow()
    pr_comments = _tail_after("pulls/", path)
    if pr_comments:
        parts = pr_comments.split("/")
        if len(parts) == 2 and parts[1] == "comments" and _number(parts[0]):
            if not paginate and trailing == ["--jq", filters.get("review_comment")]:
                return _allow()
    job_tail = _repo_path("actions/jobs/", path)
    if job_tail and job_tail.endswith("/logs"):
        job_id = job_tail[len("actions/jobs/") : -len("/logs")]
        if _number(job_id) and trailing in ([], ["--allow-escape-sequences"]):
            return _allow()
    return _deny("api command is not allowed")


def validate_gh_argv(argv: list[str], config: dict[str, Any] | None = None) -> ValidationResult:
    cfg = _config(config)
    if argv in (["--version"], ["--help"]):
        return _allow()
    parsed, args = _parse_prefix(list(argv))
    if not parsed.allowed:
        return parsed
    if not args:
        return _deny("missing gh command")
    command = args[0]
    if command == "repo":
        return _validate_repo(args)
    if command == "issue":
        return _validate_issue(args)
    if command == "pr":
        return _validate_pr(args, cfg)
    if command == "run":
        return _validate_run(args)
    if command == "api":
        return _validate_api(args, cfg)
    return _deny(f"gh command is not allowed: {command}")


def load_gh_config(
    path: str | Path = CONFIG_PATH, *, require_root_owner: bool = True
) -> ValidationResult:
    try:
        lst = os.lstat(path)
        if stat.S_ISLNK(lst.st_mode):
            return ValidationResult(False, "config must not be a symlink")
        st = os.stat(path)
    except OSError as exc:
        return ValidationResult(False, f"config not readable: {exc}")
    if not stat.S_ISREG(st.st_mode):
        return ValidationResult(False, "config is not a regular file")
    if require_root_owner and st.st_uid != 0:
        return ValidationResult(False, "config is not owned by root")
    if st.st_mode & 0o022:
        return ValidationResult(False, "config is group/world writable")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        return ValidationResult(False, f"config malformed: {exc}")
    if not isinstance(data, dict):
        return ValidationResult(False, "config must be a JSON object")
    return ValidationResult(True, config=_config(data))


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    loaded = load_gh_config()
    if not loaded.allowed:
        print(f"openace-gh: {loaded.reason}", file=sys.stderr)
        return 126
    result = validate_gh_argv(args, config=loaded.config)
    if not result.allowed:
        print(f"openace-gh: denied: {result.reason}", file=sys.stderr)
        return 126
    try:
        completed = subprocess.run([_real_gh_path(loaded.config or {}), *args])
        return completed.returncode
    except FileNotFoundError as exc:
        print(f"openace-gh: real gh binary not found: {exc}", file=sys.stderr)
        return 126


if __name__ == "__main__":
    raise SystemExit(main())
