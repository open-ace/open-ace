# Repository Setup Checklist

Use this checklist for GitHub settings that cannot be fully configured from files in the repository.

## About Section

- Description: `Self-hosted enterprise AI workspace and governance platform`
- Website: `https://www.open-ace.com`
- Docs: `https://open-ace.github.io/open-ace-docs/docs/intro`
- Docs repository: `https://github.com/open-ace/open-ace-docs`
- Topics: `ai-governance`, `ai-workspace`, `enterprise-ai`, `llmops`, `flask`, `react`, `self-hosted`, `claude-code`, `qwen-code`, `codex`

## Community

- Enable Discussions.
- Pin a "Show and tell" or "Adoption stories" discussion.
- Create labels: `good first issue`, `help wanted`, `documentation`, `first-run`, `deployment`, `security`, `frontend`, `backend`.
- Keep 5-10 small issues labeled `good first issue`.

## Releases

### Release Process

Use `scripts/release.sh` to prepare and publish a new release:

```bash
# Preview changes (dry-run)
./scripts/release.sh --version 1.2.0 --dry-run

# Execute release
./scripts/release.sh --version 1.2.0
```

The script automates:
1. Validate version format (SemVer: X.Y.Z)
2. Update `pyproject.toml` version
3. Update `CHANGELOG.md` (move [Unreleased] to new version section)
4. Create git commit and tag
5. Push tag to origin

After tag is pushed, create GitHub Release with CHANGELOG content:
```bash
gh release create v1.2.0 --title "Open ACE v1.2.0" --notes-file release_notes.md --latest
```

### Release Cadence

| Version Type | Version Bump | Trigger Conditions | Frequency |
|--------------|-------------|-------------------|-----------|
| **Major** | 2.0.0 | Architecture refactor, breaking API changes | 1-2 per year |
| **Minor** | 1.1.0 → 1.2.0 | New features, significant improvements | Monthly (1st Tuesday) |
| **Patch** | 1.1.1 | Bug fixes, security patches | As needed (weekly or on-demand) |

**Patch Release Guidelines:**
- Accumulate ≥3 bug fixes before releasing patch
- Security fixes can be released immediately
- Wait ≥7 days between patch releases to avoid excessive frequency

### Release Checklist

- Publish releases from Git tags such as `v1.1.0`.
- Use `scripts/generate_changelog.py` to collect commits since last release:
  ```bash
  python3 scripts/generate_changelog.py --since v1.1.0
  ```
- Attach Docker/deployment notes and a short upgrade guide to each release.
- Keep `CHANGELOG.md` aligned with the latest release.
- Configure `PYPI_API_TOKEN` only after confirming the intended PyPI project and package ownership.
- If `PYPI_API_TOKEN` is not configured, the release workflow skips PyPI publishing and still uploads GitHub release assets.

## Demo

- Avoid shared public administrator credentials.
- Prefer a resettable sandbox account, read-only sample data, or a short guided video until the sandbox is hardened.
