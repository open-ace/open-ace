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

- Python 3.9+
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
pytest tests/test_db.py

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

When adding new features, please update relevant documentation.

## 🏗️ Project Structure

```
open-ace/
├── cli.py              # CLI entry point
├── web.py              # Web server entry point
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

Thank you for contributing to Open ACE! 🎉