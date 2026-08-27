# CLAUDE.md

Guidance for Claude Code and human contributors.

## Where process documents go

Fix write-ups, CI post-mortems, implementation summaries, progress snapshots,
handover notes and per-issue plans go in **`docs/dev-notes/`** — never the
repository root.

- Name them `<issue>-<slug>.md` (`2437-flock-reclaim-plan.md`) or
  `<date>-<slug>.md` (`2026-08-11-ci-lint-fix.md`).
- Do not open a second file for the same effort. There must never be another
  `..._ROUND2.md` / `..._FINAL.md` / `..._FINAL_VERIFICATION.md` chain —
  update the existing note instead.
- Prefer not writing a file at all: a code comment, a docstring, or the PR
  description is usually the better home. Write a dev-note only when the
  reasoning must outlive the PR.
- The repository root keeps only user-facing docs (`README.md`, `CHANGELOG.md`,
  `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `ROADMAP.md`, `SECURITY.md`) plus AI
  tool instruction files. Everything else in the root is rejected by
  `scripts/lint/check_root_docs.py` (pre-commit) and ignored by `.gitignore`.

Why this rule exists: between 2026-08-05 and 08-12 the autonomous pipeline
committed 23 such files (2,554 lines) to the root. They referenced only each
other, nothing else referenced them, and they pushed the product README below a
wall of CI firefighting logs — right as the project received its first external
visitors. See `docs/dev-notes/README.md`.

## Pushing

In interactive sessions (human contributors and Claude Code), do not call
`git push` directly. Use `scripts/push.sh [git-push args]`: it runs the CI
lint scoped to the branch's changed files first, refuses to run over
uncommitted unrelated work, folds formatter autofixes into the commit being
pushed (amending when the commit is not yet on the remote), and aborts the
push on failures autofix cannot resolve.

This rule is for interactive sessions ONLY. Autonomous worktree agents must
not run `scripts/push.sh` (or any git add/commit/push): the orchestrator
commits and pushes after scope validation (its constraint #7), and the script
would bypass `_validate_autonomous_change_scope` and the stale-lease-safe
`github_ops.git_push`. The orchestrator's CI-repair prompt states the same
rule from the agent side.

Why this rule exists: on 2026-08-15 the same failure hit #2712, #2718 and
#2719 (#2205 before them) — black/isort autofixes landed in the worktree but
not in the pushed commit, CI lint went red, and a local `pre-commit run` still
passed because it checks the worktree, not the commit. When such a push slips
through anyway, the `Lint Heal` workflow (`.github/workflows/lint-heal.yml`)
pushes the fixes back and re-triggers CI — but going through
`scripts/push.sh` avoids the wasted red run.

## Test placement and CI semantics

- Choose one canonical location by runtime contract: `tests/unit/`,
  `tests/integration/`, `tests/e2e/`, or
  `tests/performance/`.
- The `tests/issues/<number>/` legacy quarantine was retired (deleted) by the
  #2429 final exodus; do not recreate it. Put regressions in their canonical
  layer with `pytest.mark.regression` and `pytest.mark.issue(<number>)`.
- Do not create a top-level `tests/regression/` or copy a test across multiple
  directories. Mark bug tests once with `pytest.mark.regression` and
  `pytest.mark.issue(<number>)` in their canonical layer.
- Before claiming a test is a gate, verify the exact CI lane that executes it.
  See `docs/TEST_LAYERS.md` for the directory/lane contract and migration rules.

## Schema snapshots (`schema-sync` CI)

`schema/schema-postgres.sql` and `schema/schema-sqlite.sql` are GENERATED from
the Alembic migrations by `scripts/rebuild_schema_snapshots.py`. The
`schema-sync` CI regenerates them and gates on a byte-exact `git diff`.

- Never hand-edit the `schema/*.sql` files (parens, indent, column order, SQLite
  type-case are derived; a hand edit will not match regeneration).
- When you change anything under `migrations/versions/`, regenerate and commit:
  ```bash
  python scripts/rebuild_schema_snapshots.py --postgres-url postgresql://user:pass@localhost/disposable
  git add schema/schema-postgres.sql schema/schema-sqlite.sql
  ```
- The pre-commit `check-schema-sync` hook is warn-only + structure-only; it can
  pass while the byte-exact CI gate fails. Always regenerate.

## 出站 HTTP 请求规范

Issue #2237: 所有出站 HTTP/HTTPS 请求必须遵循以下规范，以避免 gevent 环境下的
RecursionError 并确保 SSRF 防护完整。

### 优先级顺序

1. **优先使用 `safe_request()`**：
   - 自动获得 SSRF 防护（公网 IP 验证）
   - 自动禁用代理查找（避免 gevent recursion）
   - 保持 TLS SNI 和证书验证
   - 支持 DNS rebinding 防护
   - 支持环境变量配置代理（`HTTP_PROXY`、`HTTPS_PROXY`）

   ```python
   from app.utils.outbound_url_guard import safe_request

   response = safe_request("POST", "https://api.example.com/endpoint", json=data)
   ```

2. **如果必须使用 `requests` 直接调用**：
   - 必须添加 `proxies={"http": None, "https": None}` 参数
   - 必须确保 URL 已通过安全验证（公网 IP）
   - 必须在注释中说明原因

   ```python
   import requests

   # 直接调用原因：[说明为什么不能使用 safe_request]
   response = requests.post(url, json=data, proxies={"http": None, "https": None})
   ```

3. **禁止使用的模式**：
   - 裸 `requests.get(url)` 或 `requests.post(url)` 调用
   - 未验证用户提供的 URL 直接用于请求
   - 在 gevent 环境下忽略代理配置

### 适用场景

**应使用 `safe_request()` 的场景**：
- 第三方公网 API（飞书、钉钉、GitHub、OpenAI 等）
- 用户配置的 Webhook URL
- 任何涉及用户输入 URL 的请求

**可能需要直接 `requests` 的场景**：
- 内网服务调用（需确保 URL 已验证）
- 特殊代理需求（需显式配置）
- 已在安全隔离环境中运行

### 代理配置

如果你的环境需要通过代理访问外部 API，请设置环境变量：

```bash
export HTTP_PROXY=http://proxy.example.com:8080
export HTTPS_PROXY=http://proxy.example.com:8080
```

`safe_request()` 会自动使用这些代理配置。
