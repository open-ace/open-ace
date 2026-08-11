#!/usr/bin/env python3
"""Collect bounded, contract-aware GitHub Actions health metrics."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import json
import math
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_POLICY = PROJECT_ROOT / "ci" / "ci-health-policy.json"
ABSENT = "ABSENT"
CONCLUSION_KEYS = (
    "success",
    "failure",
    "cancelled",
    "skipped",
    "neutral",
    "timed_out",
    "action_required",
    "stale",
    "startup_failure",
)


class MetricsError(RuntimeError):
    """Fail-closed collection or schema error."""


def parse_time(value: str | None, field: str) -> datetime:
    if not value:
        raise MetricsError(f"missing timestamp: {field}")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MetricsError(f"invalid timestamp {field}: {value!r}") from exc


def measured_delta(start: str | None, end: str | None, field: str) -> dict[str, Any]:
    raw = (parse_time(end, f"{field}.end") - parse_time(start, f"{field}.start")).total_seconds()
    if raw < -2:
        raise MetricsError(f"timestamp order invalid for {field}: {raw}s")
    return {
        "seconds": max(0.0, raw),
        "timestamp_skew_clamped": raw < 0,
        "raw_seconds": raw,
    }


def validate_conclusion(value: Any, field: str) -> str:
    if value not in CONCLUSION_KEYS:
        raise MetricsError(f"invalid completed conclusion {field}: {value!r}")
    return value


def nearest_rank(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(percentile * len(ordered)))
    return ordered[rank - 1]


def summarize_values(
    values: list[float], min_samples: int, *, cohort_samples: int | None = None
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "count": len(values),
        "p50_seconds": nearest_rank(values, 0.50),
        "p95_seconds": None,
        "p95_status": "insufficient_data",
    }
    if (cohort_samples if cohort_samples is not None else len(values)) >= min_samples:
        result["p95_seconds"] = nearest_rank(values, 0.95)
        result["p95_status"] = "observed"
    return result


def canonical_request(path: str, params: dict[str, Any] | None = None) -> str:
    encoded = urllib.parse.urlencode(sorted((params or {}).items()))
    return f"{path}?{encoded}" if encoded else path


class RequestBudget:
    def __init__(self, maximum: int, reserve: int):
        self.maximum = maximum
        self.reserve = reserve
        self.remaining: int | None = None
        self.actual = 0
        self.estimated = 0

    def note_rate_limit(self, remaining: int) -> None:
        self.remaining = remaining

    @property
    def usable(self) -> int:
        if self.remaining is None:
            return 1
        return min(
            max(0, self.maximum - self.actual),
            max(0, self.remaining - self.reserve),
        )

    def guard(self) -> None:
        maximum_exhausted = self.actual + 1 > self.maximum
        reserve_exhausted = self.remaining is not None and self.remaining <= self.reserve
        if maximum_exhausted or reserve_exhausted:
            raise MetricsError(
                "request budget exhausted: "
                f"actual={self.actual}, remaining={self.remaining}, reserve={self.reserve}"
            )

    def begin_request(self) -> None:
        self.guard()
        self.actual += 1

    def update_remaining(self, remaining_header: str | None = None) -> None:
        if remaining_header is not None:
            self.remaining = int(remaining_header)

    def preflight(self, estimated_total: int) -> None:
        self.estimated = estimated_total
        requests_left = max(0, estimated_total - self.actual)
        remaining_capacity = (
            requests_left if self.remaining is None else max(0, self.remaining - self.reserve)
        )
        if estimated_total > self.maximum or requests_left > remaining_capacity:
            raise MetricsError(
                "request estimate exceeds budget: "
                f"estimated={estimated_total}, actual={self.actual}, max={self.maximum}, "
                f"remaining_capacity={remaining_capacity}"
            )


class GitHubClient:
    def __init__(self, repo: str, token: str, budget: RequestBudget):
        self.repo = repo
        self.token = token
        self.budget = budget

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        self.budget.begin_request()
        query = urllib.parse.urlencode(params or {})
        url = f"https://api.github.com{path}"
        if query:
            url += f"?{query}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "open-ace-ci-health",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                payload = json.loads(response.read())
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as exc:
            raise MetricsError(f"GitHub API request failed: {url}: {exc}") from exc
        self.budget.update_remaining(headers.get("x-ratelimit-remaining"))
        return payload, headers

    def get_text(self, path: str) -> tuple[str, dict[str, str]]:
        self.budget.begin_request()
        url = f"https://api.github.com{path}"
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {self.token}",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "open-ace-ci-health",
            },
        )

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None

        try:
            with urllib.request.build_opener(NoRedirect).open(request, timeout=30) as response:
                headers = {key.lower(): value for key, value in response.headers.items()}
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            if exc.code != 302:
                raise MetricsError(f"GitHub API text request failed: {url}: {exc}") from exc
            headers = {key.lower(): value for key, value in exc.headers.items()}
            redirect = headers.get("location", "")
            validate_log_redirect(redirect)
            self.budget.update_remaining(headers.get("x-ratelimit-remaining"))
            self.budget.begin_request()
            redirected_request = urllib.request.Request(
                redirect, headers={"User-Agent": "open-ace-ci-health"}
            )
            try:
                with urllib.request.urlopen(redirected_request, timeout=30) as response:
                    text = response.read().decode("utf-8", errors="replace")
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as inner:
                raise MetricsError(f"GitHub log download failed after redirect: {inner}") from inner
        except (urllib.error.URLError, TimeoutError) as exc:
            raise MetricsError(f"GitHub API text request failed: {url}: {exc}") from exc
        self.budget.update_remaining(headers.get("x-ratelimit-remaining"))
        return text, headers


def validate_log_redirect(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    hostname = (parsed.hostname or "").lower()
    allowed_suffixes = (
        ".blob.core.windows.net",
        ".actions.githubusercontent.com",
        ".githubusercontent.com",
    )
    if (
        parsed.scheme != "https"
        or not any(hostname.endswith(suffix) for suffix in allowed_suffixes)
        or parsed.username
        or parsed.password
        or parsed.port not in (None, 443)
    ):
        raise MetricsError("GitHub job-log redirect target is not allowlisted")


class FixtureClient:
    """Replay exact API responses for deterministic offline verification."""

    def __init__(self, fixture: dict[str, Any], budget: RequestBudget):
        self.responses = defaultdict(list)
        for response in fixture.get("responses", []):
            key = canonical_request(response["path"], response.get("params"))
            self.responses[key].append(response)
        self.budget = budget

    def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, dict[str, str]]:
        self.budget.begin_request()
        key = canonical_request(path, params)
        if not self.responses[key]:
            raise MetricsError(f"fixture response missing: {key}")
        response = self.responses[key].pop(0)
        if response.get("error"):
            raise MetricsError(f"fixture API error: {response['error']}")
        headers = {k.lower(): str(v) for k, v in response.get("headers", {}).items()}
        self.budget.update_remaining(headers.get("x-ratelimit-remaining"))
        return response["json"], headers

    def get_text(self, path: str) -> tuple[str, dict[str, str]]:
        self.budget.begin_request()
        key = canonical_request(path)
        if not self.responses[key]:
            raise MetricsError(f"fixture response missing: {key}")
        response = self.responses[key].pop(0)
        if response.get("error"):
            raise MetricsError(f"fixture API error: {response['error']}")
        headers = {k.lower(): str(v) for k, v in response.get("headers", {}).items()}
        self.budget.update_remaining(headers.get("x-ratelimit-remaining"))
        return response["text"], headers


def load_policy(path: Path) -> dict[str, Any]:
    policy = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "version",
        "derivation_version",
        "window_days",
        "sample_cap",
        "min_samples",
        "max_collector_requests",
        "reserve_remaining",
        "max_jobs_per_attempt",
        "cohorts",
    }
    if policy.get("version") != 1 or not required.issubset(policy):
        raise MetricsError(f"invalid CI health policy: {path}")
    if policy["sample_cap"] != 25 or policy["max_jobs_per_attempt"] != 100:
        raise MetricsError("unsupported sampling or job-page policy")
    return policy


def rate_limit(client: GitHubClient | FixtureClient, budget: RequestBudget) -> None:
    payload, _ = client.get("/rate_limit")
    remaining = payload.get("resources", {}).get("core", {}).get("remaining")
    if not isinstance(remaining, int):
        raise MetricsError("rate_limit response missing core.remaining")
    budget.note_rate_limit(remaining)


def run_list_params(
    cohort: dict[str, Any], cutoff: datetime, *, status: str, per_page: int
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "event": cohort["event"],
        "status": status,
        "per_page": per_page,
        "created": f">={cutoff.isoformat().replace('+00:00', 'Z')}",
    }
    if cohort.get("branch"):
        params["branch"] = cohort["branch"]
    return params


def validate_bounded_runs(
    cohort: dict[str, Any], payload: dict[str, Any], cutoff: datetime, sample_cap: int
) -> list[dict[str, Any]]:
    total = payload.get("total_count")
    runs = payload.get("workflow_runs")
    if not isinstance(total, int) or not isinstance(runs, list):
        raise MetricsError(f"invalid run-list response for {cohort['id']}")
    if len(runs) != min(sample_cap, total):
        raise MetricsError(f"bounded sample size mismatch for {cohort['id']}")
    ids = [run.get("id") for run in runs]
    if None in ids or len(ids) != len(set(ids)):
        raise MetricsError(f"duplicate or missing run id for {cohort['id']}")
    created = [parse_time(run.get("created_at"), "run.created_at") for run in runs]
    if created != sorted(created, reverse=True):
        raise MetricsError(f"run sample is not newest-first for {cohort['id']}")
    for run, created_at in zip(runs, created, strict=True):
        if run.get("event") != cohort["event"] or run.get("status") != "completed":
            raise MetricsError(f"run filter mismatch for {cohort['id']}: {run.get('id')}")
        if cohort.get("branch") and run.get("head_branch") != cohort["branch"]:
            raise MetricsError(f"run branch mismatch for {cohort['id']}: {run.get('id')}")
        if created_at < cutoff:
            raise MetricsError(f"run outside eligibility window: {run.get('id')}")
    return runs


def payload_link_diagnostic(
    pull_requests: Any, runtime_number: int, runtime_base: str, runtime_head: str
) -> tuple[str, list[int]]:
    """Describe mutable run payload linkage without making it authoritative."""
    if not isinstance(pull_requests, list):
        return "ambiguous", []
    numbers = [pr.get("number") for pr in pull_requests if isinstance(pr.get("number"), int)]
    if not pull_requests:
        return "empty", numbers
    matches = [pr for pr in pull_requests if pr.get("number") == runtime_number]
    if len(pull_requests) != 1 or len(matches) != 1:
        state = "number_mismatch" if len(pull_requests) == 1 else "ambiguous"
        return state, numbers
    pr = matches[0]
    head_mismatch = pr.get("head", {}).get("sha") != runtime_head
    base_mismatch = pr.get("base", {}).get("sha") != runtime_base
    if head_mismatch and base_mismatch:
        return "head_and_base_mismatch", numbers
    if head_mismatch:
        return "dynamic_head", numbers
    if base_mismatch:
        return "base_mismatch", numbers
    return "matched", numbers


def _validate_raw_jobs(
    run_id: int, attempt: int, payload: dict[str, Any], headers: dict[str, str], max_jobs: int
) -> list[dict[str, Any]]:
    total = payload.get("total_count")
    jobs = payload.get("jobs")
    if (
        not isinstance(total, int)
        or not isinstance(jobs, list)
        or total > max_jobs
        or len(jobs) != total
        or 'rel="next"' in headers.get("link", "")
    ):
        raise MetricsError(f"run {run_id} attempt {attempt} jobs incomplete")
    job_ids = [job.get("id") for job in jobs]
    if None in job_ids or len(job_ids) != len(set(job_ids)):
        raise MetricsError(f"run {run_id} attempt {attempt} duplicate job id")
    return jobs


def resolve_pr_runtime(
    client: GitHubClient | FixtureClient,
    repo: str,
    run: dict[str, Any],
    max_jobs: int,
    jobs_cache: dict[tuple[int, int], tuple[dict[str, Any], dict[str, str]]],
) -> None:
    run_id = run["id"]
    jobs_payload, jobs_headers = client.get(
        f"/repos/{repo}/actions/runs/{run_id}/attempts/1/jobs", {"per_page": max_jobs}
    )
    jobs = _validate_raw_jobs(run_id, 1, jobs_payload, jobs_headers, max_jobs)
    jobs_cache[(run_id, 1)] = (jobs_payload, jobs_headers)
    select_jobs = [job for job in jobs if job.get("name") == "Select suites"]
    if len(select_jobs) != 1:
        raise MetricsError(f"run {run_id} does not have one Select suites job")
    log, _ = client.get_text(f"/repos/{repo}/actions/jobs/{select_jobs[0]['id']}/logs")
    merge_candidates = set(re.findall(r"\+([0-9a-f]{40}):refs/remotes/pull/(\d+)/merge", log))
    base_candidates = set(re.findall(r"BASE_SHA:\s*([0-9a-f]{40})", log))
    selected_candidates = {
        tuple(
            sorted(
                part.strip()
                for part in match.split(",")
                if part.strip() and part.strip() != "policy-only"
            )
        )
        for match in re.findall(
            r"Selected suites:\s*(policy-only|[a-z0-9_-]+(?:\s*,\s*[a-z0-9_-]+)*)",
            log,
        )
    }
    if len(merge_candidates) != 1 or len(base_candidates) != 1 or len(selected_candidates) != 1:
        raise MetricsError(f"run {run_id} runtime merge/base log evidence is ambiguous")
    merge_sha, pr_number_text = next(iter(merge_candidates))
    pr_number = int(pr_number_text)
    selection_base_sha = next(iter(base_candidates))
    selected_suites = next(iter(selected_candidates))
    commit, _ = client.get(f"/repos/{repo}/git/commits/{merge_sha}")
    parents = [parent.get("sha") for parent in commit.get("parents", [])]
    if commit.get("sha") != merge_sha or len(parents) != 2 or parents[1] != run.get("head_sha"):
        raise MetricsError(f"run {run_id} runtime merge parents do not match log/head")
    execution_base_sha = parents[0]

    payload_state, payload_numbers = payload_link_diagnostic(
        run.get("pull_requests"), pr_number, execution_base_sha, run["head_sha"]
    )
    run.update(
        {
            "_execution_base_sha": execution_base_sha,
            "_selection_base_sha": selection_base_sha,
            "_runtime_head_sha": run["head_sha"],
            "_runtime_merge_sha": merge_sha,
            "_base_alignment": (
                "matched" if execution_base_sha == selection_base_sha else "diverged"
            ),
            "_selected_suites": selected_suites,
            "_pr_number": pr_number,
            "_pr_number_source": "runtime_merge_ref",
            "_payload_link_state": payload_state,
            "_payload_pr_numbers": payload_numbers,
        }
    )


def load_tree(client: GitHubClient | FixtureClient, repo: str, commit: str) -> dict[str, str]:
    payload, _ = client.get(f"/repos/{repo}/git/trees/{commit}", {"recursive": "1"})
    if payload.get("truncated") is not False or not isinstance(payload.get("tree"), list):
        raise MetricsError(f"incomplete recursive tree for {commit}")
    return {
        entry["path"]: entry["sha"]
        for entry in payload["tree"]
        if entry.get("type") == "blob" and entry.get("path") and entry.get("sha")
    }


def load_suite_catalog(client: GitHubClient | FixtureClient, repo: str, blob_sha: str) -> set[str]:
    payload, _ = client.get(f"/repos/{repo}/git/blobs/{blob_sha}")
    if payload.get("sha") != blob_sha or payload.get("encoding") != "base64":
        raise MetricsError(f"invalid ci/suites.json blob response: {blob_sha}")
    try:
        encoded = "".join(payload["content"].split())
        content = base64.b64decode(encoded, validate=True).decode("utf-8")
        document = json.loads(content)
    except (KeyError, ValueError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MetricsError(f"invalid ci/suites.json blob content: {blob_sha}") from exc
    suites = document.get("suites")
    if not isinstance(suites, dict) or not all(isinstance(name, str) for name in suites):
        raise MetricsError(f"invalid suite catalog schema: {blob_sha}")
    return set(suites)


def contract_hash(
    run: dict[str, Any], cohort: dict[str, Any], trees: dict[str, dict[str, str]], version: int
) -> tuple[str, dict[str, Any]]:
    checkout_sha = (
        run.get("_runtime_merge_sha") if cohort["event"] == "pull_request" else run.get("head_sha")
    )
    if not checkout_sha or checkout_sha not in trees:
        raise MetricsError(f"run {run.get('id')} missing verified checkout tree")
    entries: list[dict[str, str]] = []
    for path in sorted(cohort["contract_paths"]):
        entries.append({"path": path, "blob": trees[checkout_sha].get(path, ABSENT)})
    digest_material = {
        "derivation_version": version,
        "cohort_id": cohort["id"],
        "entries": entries,
        "selected_suites": list(run.get("_selected_suites", ())),
    }
    digest = hashlib.sha256(
        json.dumps(digest_material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    contract = {
        **digest_material,
        "checkout_sha": checkout_sha,
        "execution_base_sha": run.get("_execution_base_sha"),
        "selection_base_sha": run.get("_selection_base_sha"),
        "head_sha": run.get("_runtime_head_sha") or run.get("head_sha"),
        "base_alignment": run.get("_base_alignment"),
    }
    return digest, contract


def collect_attempts(
    client: GitHubClient | FixtureClient,
    repo: str,
    run: dict[str, Any],
    max_jobs: int,
    jobs_cache: dict[tuple[int, int], tuple[dict[str, Any], dict[str, str]]] | None = None,
) -> list[dict[str, Any]]:
    attempt_count = run.get("run_attempt")
    if not isinstance(attempt_count, int) or attempt_count < 1:
        raise MetricsError(f"run {run.get('id')} has invalid run_attempt")
    attempts = []
    canonical_jobs: list[dict[str, Any]] = []
    if jobs_cache is None:
        jobs_cache = {}
    for number in range(1, attempt_count + 1):
        meta, _ = client.get(f"/repos/{repo}/actions/runs/{run['id']}/attempts/{number}")
        cache_key = (run["id"], number)
        if cache_key in jobs_cache:
            jobs_payload, headers = jobs_cache[cache_key]
        else:
            jobs_payload, headers = client.get(
                f"/repos/{repo}/actions/runs/{run['id']}/attempts/{number}/jobs",
                {"per_page": max_jobs},
            )
        if meta.get("run_attempt") != number:
            raise MetricsError(f"run {run['id']} attempt metadata mismatch")
        attempt_conclusion = validate_conclusion(
            meta.get("conclusion"), f"run {run['id']} attempt {number}"
        )
        jobs = _validate_raw_jobs(run["id"], number, jobs_payload, headers, max_jobs)
        attempt_started = parse_time(meta.get("run_started_at"), "attempt.run_started_at")
        normalized_jobs = []
        for job in jobs:
            job_conclusion = validate_conclusion(
                job.get("conclusion"),
                f"run {run['id']} attempt {number} job {job.get('id')}",
            )
            created = parse_time(job.get("created_at"), "job.created_at")
            started = parse_time(job.get("started_at"), "job.started_at")
            completed = parse_time(job.get("completed_at"), "job.completed_at")
            start_offset = (started - attempt_started).total_seconds()
            completed_offset = (completed - attempt_started).total_seconds()
            inherited = start_offset < -2 or completed_offset < -2
            normalized = {
                "job_id": job["id"],
                "name": job.get("name"),
                "conclusion": job_conclusion,
                "labels": job.get("labels", []),
                "created_at": job.get("created_at"),
                "started_at": job.get("started_at"),
                "completed_at": job.get("completed_at"),
                "timing_state": "current_attempt",
                "inherited_from_attempt": None,
                "inherited_from_job_id": None,
            }
            if inherited:
                if number == 1 or completed_offset > 2:
                    raise MetricsError(
                        f"run {run['id']} attempt {number} job {job['id']} "
                        "has invalid cross-attempt timestamps"
                    )
                if job_conclusion == "skipped" and start_offset >= -2:
                    if abs((started - created).total_seconds()) > 2:
                        raise MetricsError(
                            f"run {run['id']} attempt {number} skipped job {job['id']} "
                            "has invalid inherited timestamp shape"
                        )
                    matches = [
                        prior
                        for prior in canonical_jobs
                        if prior["name"] == job.get("name")
                        and prior["conclusion"] == "skipped"
                        and prior["completed_at"] == job.get("completed_at")
                    ]
                elif start_offset < -2 and completed_offset < -2:
                    matches = [
                        prior
                        for prior in canonical_jobs
                        if prior["name"] == job.get("name")
                        and prior["conclusion"] == job_conclusion
                        and prior["started_at"] == job.get("started_at")
                        and prior["completed_at"] == job.get("completed_at")
                    ]
                else:
                    raise MetricsError(
                        f"run {run['id']} attempt {number} job {job['id']} "
                        "has invalid inherited timestamp shape"
                    )
                if len(matches) != 1:
                    raise MetricsError(
                        f"run {run['id']} attempt {number} job {job['id']} "
                        "does not have one canonical timing origin"
                    )
                normalized.update(
                    {
                        "timing_state": "inherited_snapshot",
                        "inherited_from_attempt": matches[0]["attempt"],
                        "inherited_from_job_id": matches[0]["job_id"],
                        "queue": None,
                        "execution": None,
                    }
                )
            else:
                normalized.update(
                    {
                        "queue": measured_delta(
                            job.get("created_at"), job.get("started_at"), "job.queue"
                        ),
                        "execution": measured_delta(
                            job.get("started_at"), job.get("completed_at"), "job.execution"
                        ),
                    }
                )
                canonical_jobs.append({**normalized, "attempt": number})
            normalized_jobs.append(normalized)
        attempts.append(
            {
                "attempt": number,
                "conclusion": attempt_conclusion,
                "queue": measured_delta(
                    meta.get("created_at"), meta.get("run_started_at"), "workflow.queue"
                ),
                "wall": measured_delta(
                    meta.get("run_started_at"), meta.get("updated_at"), "attempt.wall"
                ),
                "created_at": meta.get("created_at"),
                "updated_at": meta.get("updated_at"),
                "jobs": normalized_jobs,
            }
        )
    return attempts


def normalize_run(
    run: dict[str, Any], contract_id: str, contract: dict[str, Any], attempts: list[dict[str, Any]]
) -> dict[str, Any]:
    first = attempts[0]
    final = attempts[-1]
    return {
        "run_id": run["id"],
        "url": run.get("html_url"),
        "head_sha": run.get("head_sha"),
        "pr_number": run.get("_pr_number"),
        "pr_number_source": run.get("_pr_number_source"),
        "contract_base_source": (
            "runtime_merge_parent" if run.get("_execution_base_sha") else "run_head"
        ),
        "runtime_merge_sha": run.get("_runtime_merge_sha"),
        "execution_base_sha": run.get("_execution_base_sha"),
        "selection_base_sha": run.get("_selection_base_sha"),
        "base_alignment": run.get("_base_alignment"),
        "selected_suites": list(run.get("_selected_suites", ())),
        "payload_link_state": run.get("_payload_link_state"),
        "payload_pr_numbers": run.get("_payload_pr_numbers", []),
        "contract_id": contract_id,
        "contract": contract,
        "first_conclusion": first["conclusion"],
        "eventual_conclusion": final["conclusion"],
        "recovered_on_retry": first["conclusion"] != "success" and final["conclusion"] == "success",
        "first_attempt_wall": first["wall"],
        "eventual_resolution": measured_delta(
            run.get("created_at"), final["updated_at"], "eventual_resolution"
        ),
        "attempts": attempts,
    }


def aggregate_contract(runs: list[dict[str, Any]], min_samples: int) -> dict[str, Any]:
    first = Counter(run["first_conclusion"] for run in runs)
    eventual = Counter(run["eventual_conclusion"] for run in runs)
    recoveries = sum(run["recovered_on_retry"] for run in runs)
    first_walls = [run["first_attempt_wall"]["seconds"] for run in runs]
    eventual_latencies = [run["eventual_resolution"]["seconds"] for run in runs]
    workflow_queue: list[float] = []
    job_queue: list[float] = []
    job_execution: list[float] = []
    slow_jobs = []
    inherited_job_count = 0
    for run in runs:
        for attempt in run["attempts"]:
            workflow_queue.append(attempt["queue"]["seconds"])
            for job in attempt["jobs"]:
                if job["timing_state"] == "inherited_snapshot":
                    inherited_job_count += 1
                    continue
                job_queue.append(job["queue"]["seconds"])
                job_execution.append(job["execution"]["seconds"])
                slow_jobs.append(
                    {
                        "run_id": run["run_id"],
                        "attempt": attempt["attempt"],
                        "job_id": job["job_id"],
                        "name": job["name"],
                        "seconds": job["execution"]["seconds"],
                    }
                )
    sample_count = len(runs)
    first_conclusions = {key: first.get(key, 0) for key in CONCLUSION_KEYS}
    eventual_conclusions = {key: eventual.get(key, 0) for key in CONCLUSION_KEYS}
    first_conclusions.update(
        {key: value for key, value in first.items() if key not in first_conclusions}
    )
    eventual_conclusions.update(
        {key: value for key, value in eventual.items() if key not in eventual_conclusions}
    )
    return {
        "sample_count": sample_count,
        "first_conclusions": first_conclusions,
        "eventual_conclusions": eventual_conclusions,
        "first_pass_rate": first.get("success", 0) / sample_count if sample_count else None,
        "eventual_pass_rate": eventual.get("success", 0) / sample_count if sample_count else None,
        "retry_recovery_count": recoveries,
        "retry_recovery_rate": recoveries / sample_count if sample_count else None,
        "inherited_job_snapshot_count": inherited_job_count,
        "first_attempt_wall": summarize_values(
            first_walls, min_samples, cohort_samples=sample_count
        ),
        "eventual_resolution": summarize_values(
            eventual_latencies, min_samples, cohort_samples=sample_count
        ),
        "workflow_queue": summarize_values(
            workflow_queue, min_samples, cohort_samples=sample_count
        ),
        "job_queue": summarize_values(job_queue, min_samples, cohort_samples=sample_count),
        "job_execution": summarize_values(job_execution, min_samples, cohort_samples=sample_count),
        "slow_jobs": sorted(slow_jobs, key=lambda item: item["seconds"], reverse=True)[:10],
    }


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# CI health metrics",
        "",
        f"Generated: {report['generated_at']}",
        f"Suite metrics included: {str(report['suite_metrics_included']).lower()}",
        "",
        "| Cohort | Eligible | Sampled | In progress | Queued | Contracts |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for cohort in report["cohorts"]:
        lines.append(
            f"| {cohort['id']} | {cohort['eligible_count']} | {cohort['sampled_count']} "
            f"| {cohort['in_progress_count']} | {cohort['queued_count']} "
            f"| {len(cohort['contracts'])} |"
        )
    for cohort in report["cohorts"]:
        if cohort["payload_link_states"]:
            states = ", ".join(
                f"{state}={count}" for state, count in sorted(cohort["payload_link_states"].items())
            )
            lines.extend(["", f"PR payload diagnostics ({cohort['id']}): {states}"])
        for contract_id, contract in cohort["contracts"].items():
            status = contract["first_attempt_wall"]["p95_status"]
            lines.extend(
                [
                    "",
                    f"## {cohort['id']} / {contract_id[:12]}",
                    "",
                    f"- samples: {contract['sample_count']} ({status})",
                    f"- first-pass rate: {contract['first_pass_rate']}",
                    f"- eventual-pass rate: {contract['eventual_pass_rate']}",
                    f"- retry recoveries: {contract['retry_recovery_count']}",
                    f"- inherited job snapshots: {contract['inherited_job_snapshot_count']}",
                ]
            )
    lines.extend(
        [
            "",
            "## API budget",
            "",
            f"- estimated requests: {report['request_budget']['estimated']}",
            f"- actual requests: {report['request_budget']['actual']}",
            f"- remaining: {report['request_budget']['remaining']}",
        ]
    )
    return "\n".join(lines) + "\n"


def collect(
    client: GitHubClient | FixtureClient,
    repo: str,
    policy: dict[str, Any],
    budget: RequestBudget,
    *,
    now: datetime,
) -> dict[str, Any]:
    rate_limit(client, budget)
    cutoff = now - timedelta(days=policy["window_days"])
    cohort_sources = []
    unique_commits: set[str] = set()
    pr_runs: list[dict[str, Any]] = []
    total_attempts = 0

    for cohort in policy["cohorts"]:
        workflow_path = f"/repos/{repo}/actions/workflows/{cohort['workflow']}/runs"
        completed_params = run_list_params(
            cohort, cutoff, status="completed", per_page=policy["sample_cap"]
        )
        completed, completed_headers = client.get(workflow_path, completed_params)
        runs = validate_bounded_runs(cohort, completed, cutoff, policy["sample_cap"])
        counts = {}
        for status in ("in_progress", "queued"):
            payload, _ = client.get(
                workflow_path, run_list_params(cohort, cutoff, status=status, per_page=1)
            )
            if not isinstance(payload.get("total_count"), int):
                raise MetricsError(f"invalid {status} count for {cohort['id']}")
            counts[status] = payload["total_count"]
        for run in runs:
            head = run.get("head_sha")
            if not head:
                raise MetricsError(f"run {run.get('id')} missing head_sha")
            if cohort["event"] == "pull_request":
                pr_runs.append(run)
            else:
                unique_commits.add(head)
            attempts = run.get("run_attempt")
            if not isinstance(attempts, int) or attempts < 1:
                raise MetricsError(f"run {run.get('id')} has invalid run_attempt")
            total_attempts += attempts
        cohort_sources.append(
            {
                "definition": cohort,
                "eligible_count": completed["total_count"],
                "sampling_truncated_by_policy": completed["total_count"] > policy["sample_cap"],
                "has_next": 'rel="next"' in completed_headers.get("link", ""),
                "runs": runs,
                **{f"{key}_count": value for key, value in counts.items()},
            }
        )

    # Reserve the worst case for one checkout tree, suite-catalog blob,
    # redirected job log, and merge commit per sampled PR run.
    conservative_estimate = (
        budget.actual
        + total_attempts * 2
        + len(unique_commits)
        + len(pr_runs)  # checkout merge trees
        + len(pr_runs)  # historical suite-catalog blobs
        + len(pr_runs) * 3  # redirected logs (2) and merge commits (1)
    )
    budget.preflight(conservative_estimate)

    jobs_cache: dict[tuple[int, int], tuple[dict[str, Any], dict[str, str]]] = {}
    for run in pr_runs:
        resolve_pr_runtime(
            client,
            repo,
            run,
            policy["max_jobs_per_attempt"],
            jobs_cache,
        )
        unique_commits.add(run["_runtime_merge_sha"])

    # All variable PR requests have completed. This exact second guard covers
    # every remaining tree, attempt metadata, and uncached jobs request.
    exact_estimate = (
        budget.actual
        + len(unique_commits)
        + total_attempts
        + total_attempts
        - len(jobs_cache)
        + len(pr_runs)
    )
    budget.preflight(exact_estimate)
    trees = {commit: load_tree(client, repo, commit) for commit in sorted(unique_commits)}

    suite_blob_by_run: dict[int, str] = {}
    for run in pr_runs:
        suite_blob = trees[run["_runtime_merge_sha"]].get("ci/suites.json")
        if not suite_blob:
            raise MetricsError(f"run {run['id']} checkout tree lacks ci/suites.json")
        suite_blob_by_run[run["id"]] = suite_blob
    suite_blob_shas = set(suite_blob_by_run.values())
    remaining_attempt_requests = total_attempts * 2 - len(jobs_cache)
    budget.preflight(budget.actual + len(suite_blob_shas) + remaining_attempt_requests)
    suite_catalogs = {
        blob_sha: load_suite_catalog(client, repo, blob_sha) for blob_sha in sorted(suite_blob_shas)
    }
    for run in pr_runs:
        unknown = set(run["_selected_suites"]) - suite_catalogs[suite_blob_by_run[run["id"]]]
        if unknown:
            raise MetricsError(
                f"run {run['id']} selected unknown historical suites: {sorted(unknown)}"
            )

    cohort_reports = []
    for source in cohort_sources:
        cohort = source["definition"]
        normalized = []
        for run in source["runs"]:
            digest, contract = contract_hash(run, cohort, trees, policy["derivation_version"])
            attempts = collect_attempts(
                client,
                repo,
                run,
                policy["max_jobs_per_attempt"],
                jobs_cache,
            )
            normalized.append(normalize_run(run, digest, contract, attempts))
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for run in normalized:
            grouped[run["contract_id"]].append(run)
        cohort_reports.append(
            {
                "id": cohort["id"],
                "eligible_count": source["eligible_count"],
                "sampled_count": len(normalized),
                "invalid_count": 0,
                "sample_cap": policy["sample_cap"],
                "sampling_truncated_by_policy": source["sampling_truncated_by_policy"],
                "in_progress_count": source["in_progress_count"],
                "queued_count": source["queued_count"],
                "payload_link_states": dict(
                    Counter(
                        run["payload_link_state"] for run in normalized if run["payload_link_state"]
                    )
                ),
                "contracts": {
                    contract_id: aggregate_contract(runs, policy["min_samples"])
                    for contract_id, runs in sorted(grouped.items())
                },
                "runs": normalized,
            }
        )

    report = {
        "schema_version": 1,
        "report_contract": hashlib.sha256(
            json.dumps(policy, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "generated_at": now.isoformat().replace("+00:00", "Z"),
        "window_days": policy["window_days"],
        "suite_metrics_included": False,
        "cohorts": cohort_reports,
        "request_budget": {
            "estimated": budget.estimated,
            "actual": budget.actual,
            "remaining": budget.remaining,
            "maximum": budget.maximum,
            "reserve": budget.reserve,
        },
    }
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--policy", type=Path, default=DEFAULT_POLICY)
    parser.add_argument("--input", type=Path, help="Offline exact API response fixture")
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-markdown", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        policy = load_policy(args.policy)
        if not args.repo or "/" not in args.repo:
            raise MetricsError("--repo owner/name is required")
        budget = RequestBudget(policy["max_collector_requests"], policy["reserve_remaining"])
        if args.input:
            client: GitHubClient | FixtureClient = FixtureClient(
                json.loads(args.input.read_text(encoding="utf-8")), budget
            )
        else:
            token = os.environ.get("GITHUB_TOKEN")
            if not token:
                raise MetricsError("GITHUB_TOKEN is required without --input")
            client = GitHubClient(args.repo, token, budget)
        report = collect(client, args.repo, policy, budget, now=datetime.now(timezone.utc))
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_markdown.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        args.output_markdown.write_text(render_markdown(report), encoding="utf-8")
        return 0
    except (MetricsError, OSError, json.JSONDecodeError) as exc:
        print(f"CI METRICS ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
