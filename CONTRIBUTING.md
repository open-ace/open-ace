# Contributing to Open ACE

> **ACE** = **AI Computing Explorer**

Thank you for your interest in contributing to Open ACE! This document provides guidelines and instructions for contributing.

## 🌟 Ways to Contribute

- **Report bugs** - Submit issues for bugs you find
- **Suggest features** - Share your ideas for new features
- **Improve documentation** - Help make our docs better
- **Submit pull requests** - Contribute code changes

## 🐛 Reporting Issues

Before submitting an issue, please:

1. **Search existing issues** to avoid duplicates
2. **Use a clear title** that describes the problem
3. **Provide details**:
   - Steps to reproduce
   - Expected behavior
   - Actual behavior
   - Environment (OS, Python version)

### Issue Template

```markdown
**Description**
A clear description of the issue.

**Steps to Reproduce**
1. Run `python3 cli.py today`
2. See error...

**Expected Behavior**
What you expected to happen.

**Actual Behavior**
What actually happened.

**Environment**
- OS: macOS 14.0
- Python: 3.11.0
- Open ACE version: 1.0.0
```

## 🔧 Development Setup

### Prerequisites

- Python 3.10+
- pip

### Setup

```bash
# Fork and clone the repository
git clone https://github.com/open-ace/open-ace.git
cd open-ace

# Create virtual environment
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Install development dependencies
pip install pytest playwright

# Run tests
pytest
```

## 📝 Code Style

- Follow [PEP 8](https://pep8.org/) style guidelines
- Use meaningful variable and function names
- Add docstrings to functions and classes
- Keep functions focused and concise

### Example

```python
def get_daily_usage(date: str, tool_name: str = None) -> dict:
    """
    Get token usage for a specific date.

    Args:
        date: Date in YYYY-MM-DD format
        tool_name: Optional tool filter (claude, qwen, openclaw)

    Returns:
        Dictionary with usage statistics
    """
    # Implementation
    pass
```

## 🧪 Testing

### Run Tests

```bash
# Run all tests
pytest

# Run specific test file
pytest tests/unit/test_db.py

# Run with coverage
pytest --cov=scripts/shared tests/
```

### Writing Tests

- Place tests in the `tests/` directory
- Name test files with `test_` prefix
- Use descriptive test function names

```python
def test_get_daily_usage_with_valid_date():
    """Test that get_daily_usage returns correct data for valid date."""
    result = get_daily_usage("2026-03-21")
    assert result is not None
    assert "tokens" in result
```

## 📋 Pull Request Process

1. **Create a branch** for your changes
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes** following code style guidelines

3. **Add tests** for new functionality

4. **Run tests** to ensure everything passes
   ```bash
   pytest
   ```

5. **Commit your changes** with a clear message
   ```bash
   git commit -m "Add feature: description of feature"
   ```

6. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

7. **Create a Pull Request** on GitHub

### PR Checklist

- [ ] Code follows PEP 8 style
- [ ] Tests pass locally
- [ ] New features have tests
- [ ] Documentation updated if needed
- [ ] Commit messages are clear

## 📚 Documentation

Documentation is in the `docs/` directory:

- `ARCHITECTURE.md` - System architecture
- `DEPLOYMENT.md` - Deployment guide
- `DEVELOPMENT.md` - Development guide
- `CONCEPTS.md` - Core concepts

The published Docusaurus docs site lives in the separate `open-ace/open-ace-docs` repository. Keep product docs changes in this repository under `docs/`.

When adding new features, please update relevant documentation.

## 🏗️ Project Structure

```
open-ace/
├── cli.py              # CLI entry point
├── server.py              # Web server entry point
├── scripts/
│   ├── fetch_*.py      # Data collection scripts
│   ├── shared/         # Shared modules (db, utils, config)
│   └── migrations/     # Database migration scripts
├── templates/          # HTML templates
├── static/             # Static files (CSS, JS)
├── tests/              # Test files
└── docs/               # Documentation
```

## 💬 Getting Help

- Open an issue for bugs or feature requests
- Check existing documentation in `docs/`

## 📄 License

By contributing, you agree that your contributions will be licensed under the Apache 2.0 License.

---

## 🛠️ Docker Environment Tooling Maintenance

### Git and GitHub CLI Version Management

The Docker environment includes git and GitHub CLI (gh) for autonomous development workflows. These tools are critical for:
- Git operations (clone, branch, commit, push, worktree)
- GitHub API operations (PR/Issue creation, token validation, repo queries)

**Current Versions:**
- git: Installed via apt-get (Debian trixie repository)
- gh CLI: v2.42.1 (installed from GitHub releases)

**Version Update Policy:**

We recommend quarterly evaluation of gh CLI version updates to track security patches and new features:

1. **Check for new releases**: Visit [GitHub CLI releases](https://github.com/cli/cli/releases)
2. **Review CVE vulnerabilities**: Monitor [GitHub CLI security advisories](https://github.com/cli/cli/security/advisories)
3. **Update Dockerfile**: Update the version number in `Dockerfile` line for gh CLI installation
4. **Test the build**: Ensure `docker compose build` succeeds with the new version
5. **Update documentation**: Update this section with the new version number and date

**Build Failure Handling:**

If gh CLI installation fails during Docker build:
- The Dockerfile includes a fallback mechanism: deb package → apt repository
- Both methods failing indicates network connectivity issues
- Recommended fixes:
  - Check network connection and GitHub releases availability
  - Try manual rebuild after network restoration
  - Contact maintainers if issues persist

### GitHub Token Configuration Isolation

**Important**: Autonomous development operations use GH_TOKEN environment variable injection, not persistent configuration directories.

**Configuration Isolation Boundaries:**
- All autonomous development operations use GH_TOKEN environment variable passed via subprocess
- gh CLI in autonomous workflows only executes commands, does not persist configuration
- Commands requiring configuration directories (e.g., `gh auth status`) are not used in autonomous workflows
- Different users' gh operations are isolated via independent GH_TOKEN injection
- No configuration sharing between users in multi-user workspace mode

**Implications:**
- The `~/.config/gh` directory is not required for autonomous development
- Users should not run `gh auth login` or `gh auth status` in autonomous workflow contexts
- Token validation uses `gh api user` command (no config required) or fallback to direct API call

---

Thank you for contributing to Open ACE! 🎉
